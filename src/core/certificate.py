import hashlib
import io
import json
import os
import zipfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.audit_logger import AuditLoggerManager
from src.core.legal import LegalVersionManager
from src.core.storage import PrivateStorageManager

CERTIFICATE_KIND = "certificate"
# Package names for certificate texts that do not declare "package" (versions 1.0 to 1.2)
LEGACY_PACKAGE_NAMES = {
    "certificate": "certificado.pdf", "evidence": "prueba/evidencia.json", "evidence_hash": "prueba/evidencia.sha256",
    "ots": "prueba/prueba.ots", "originals": "originales",
}
DEFAULT_LAYOUT = Path(__file__).resolve().parents[1] / "certificate_templates" / "certificado.html"
CERTIFICATES_DIR = "_certificates"

class PdfRenderer(ABC):
    """Turns the certificate HTML into a PDF. Swappable: Chromium today, another engine tomorrow without touching the rest."""

    @abstractmethod
    def render(self, html: str) -> bytes:
        ...

class ChromiumPdfRenderer(PdfRenderer):
    """HTML to PDF with Chromium (Playwright). Everything runs locally; no data goes to third parties."""

    def __init__(self, no_sandbox: bool = False):
        self.no_sandbox = no_sandbox

    def render(self, html: str) -> bytes:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"] if self.no_sandbox else [])
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()

def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"

def _utc(moment: Optional[datetime]) -> str:
    if not moment:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"

def _fill(template: str, **values) -> str:
    """Fills the {placeholders} of a text without failing if a configured text has an unknown one."""
    return template.format_map(_SafeDict(**values))


class CertificateIssuer:
    """
    Certifier: takes a verified evidence and issues its PRIMA certificate (PDF) and the delivery package (ZIP).
    - Texts come from the current version in `legal_versions` (kind certificate) and the design from an HTML template.
    - Each certificate records which text version and which design it was issued with.
    - It is idempotent: issuing the same evidence twice returns the same certificate.
    """

    def __init__(self, engine: Engine, storage: PrivateStorageManager, legal: LegalVersionManager,
                 audit: AuditLoggerManager, renderer: PdfRenderer, include_originals: bool = True,
                 layout_path: Optional[str] = None):
        self.engine = engine
        self.storage = storage
        self.legal = legal
        self.audit = audit
        self.renderer = renderer
        self.include_originals = include_originals
        self.layout_path = Path(layout_path) if layout_path else DEFAULT_LAYOUT

    # Consulta ---------------------------------------------------------------------

    def get_certificate(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, evidence_id, template_version, layout_sha256, pdf_ref, package_ref, pdf_sha256, "
                "bitcoin_block_height, bitcoin_block_time, issued_at FROM certificates WHERE evidence_id = :e"),
                {"e": evidence_id}).first()
        return dict(row._mapping) if row else None

    def read_pdf(self, evidence_id: str) -> Optional[bytes]:
        """Bytes of the issued certificate, or None. Reads only inside the storage folder."""
        return self._read_issued(evidence_id, "pdf_ref")

    def read_package(self, evidence_id: str) -> Optional[bytes]:
        """Bytes of the issued delivery package (ZIP), or None. Reads only inside the storage folder."""
        return self._read_issued(evidence_id, "package_ref")

    def _read_issued(self, evidence_id: str, ref_field: str) -> Optional[bytes]:
        cert = self.get_certificate(evidence_id)
        if not cert:
            return None
        root = os.path.realpath(self.storage.storage_root)
        path = os.path.realpath(os.path.join(root, cert[ref_field]))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def _load(self, evidence_id: str) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            ev = conn.execute(text(
                "SELECT e.id, e.title, e.status, e.manifest_hash, e.created_at, u.name AS owner_name, u.country AS owner_country "
                "FROM evidences e JOIN users u ON u.id = e.user_id WHERE e.id = :i"), {"i": evidence_id}).first()
            if not ev:
                raise KeyError(f"Evidencia {evidence_id} no encontrada.")
            proof = conn.execute(text(
                "SELECT manifest_json, ots_data, bitcoin_block_height, bitcoin_block_time FROM proofs WHERE evidence_id = :i"),
                {"i": evidence_id}).first()
            files = conn.execute(text(
                "SELECT filename, size, sha256, storage_reference FROM evidence_files WHERE evidence_id = :i ORDER BY filename"),
                {"i": evidence_id}).all()
        if ev.status != "VERIFIED":
            raise ValueError(f"La evidencia {evidence_id} no está verificada ({ev.status}); no se puede certificar.")
        if not proof:
            raise ValueError(f"La evidencia {evidence_id} no tiene prueba guardada.")
        return {"evidence": ev, "proof": proof, "files": files}

    # Issuing ----------------------------------------------------------------------

    def issue(self, evidence_id: str) -> Dict[str, Any]:
        existing = self.get_certificate(evidence_id)
        if existing:
            return existing

        data = self._load(evidence_id)
        ev, proof, files = data["evidence"], data["proof"], data["files"]
        template_row = self.legal.latest(CERTIFICATE_KIND)
        if not template_row:
            raise ValueError("No certificate text version is loaded.")
        texts = json.loads(template_row["text"])
        manifest = json.loads(proof.manifest_json)

        layout_html = self.layout_path.read_text(encoding="utf-8")
        html = self._render_html(layout_html, texts, ev, proof, files, manifest)
        pdf = self.renderer.render(html)
        package = self._build_package(texts, ev, proof, files, manifest, pdf)

        folder = os.path.join(self.storage.storage_root, CERTIFICATES_DIR, evidence_id)
        os.makedirs(folder, exist_ok=True)
        pdf_ref = f"{CERTIFICATES_DIR}/{evidence_id}/certificado.pdf"
        package_ref = f"{CERTIFICATES_DIR}/{evidence_id}/paquete.zip"
        with open(os.path.join(folder, "certificado.pdf"), "wb") as f:
            f.write(pdf)
        with open(os.path.join(folder, "paquete.zip"), "wb") as f:
            f.write(package)

        pdf_sha256 = hashlib.sha256(pdf).hexdigest()
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO certificates (evidence_id, template_version, layout_sha256, pdf_ref, package_ref, pdf_sha256, "
                "bitcoin_block_height, bitcoin_block_time) VALUES (:e, :tv, :ls, :pr, :kr, :ps, :bh, :bt) "
                "ON CONFLICT (evidence_id) DO NOTHING"),
                {"e": evidence_id, "tv": template_row["version"], "ls": hashlib.sha256(layout_html.encode("utf-8")).hexdigest(),
                 "pr": pdf_ref, "kr": package_ref, "ps": pdf_sha256, "bh": proof.bitcoin_block_height,
                 "bt": proof.bitcoin_block_time})
            self.audit.log_event(evidence_id, "CERTIFICATE_ISSUED", "system",
                                 {"template_version": template_row["version"], "pdf_sha256": pdf_sha256}, conn=conn)
        return self.get_certificate(evidence_id)

    def _render_html(self, layout_html: str, texts: Dict[str, Any], ev, proof, files, manifest) -> str:
        env = Environment(autoescape=select_autoescape(["html"], default_for_string=True, default=True))
        template = env.from_string(layout_html)

        block_date = _utc(proof.bitcoin_block_time)
        if proof.bitcoin_block_time:
            certification_line = _fill(texts["certification_line"], block_height=proof.bitcoin_block_height, block_date=block_date)
        else:
            certification_line = _fill(texts["certification_line_no_date"], block_height=proof.bitcoin_block_height)

        max_files = int(texts.get("max_files_on_page", 12))
        shown = [{"name": f.filename, "size": _human_size(f.size), "sha256": f.sha256} for f in files[:max_files]]
        extra = len(files) - len(shown)
        declaration = manifest.get("declaration", {})
        owner_line = f"{ev.owner_name} ({ev.owner_country})" if ev.owner_country else ev.owner_name

        return template.render(
            t=texts, labels=texts["labels"], blocks=set(texts.get("blocks", [])),
            evidence_id=ev.id, title=ev.title, owner_line=owner_line, created_at=_utc(ev.created_at),
            certification_line=certification_line, evidence_hash=ev.manifest_hash,
            system_version=manifest.get("system_version", ""), files=shown,
            more_files_text=_fill(texts["more_files_text"], count=extra) if extra > 0 else "",
            declaration_version=declaration.get("version", ""), declaration_text=declaration.get("text", ""))

    def _build_package(self, texts: Dict[str, Any], ev, proof, files, manifest, pdf: bytes) -> bytes:
        guide = texts["guide"]
        # Package names are part of the versioned text, like the guide that names them (1.0 to 1.2: Spanish names)
        names = {**LEGACY_PACKAGE_NAMES, **texts.get("package", {})}
        guide_text = "\n\n".join(
            [guide["title"], _fill(guide["intro"], evidence_id=ev.id)]
            + [_fill(step, evidence_hash=ev.manifest_hash, block_height=proof.bitcoin_block_height) for step in guide["steps"]]
            + [guide["closing"]]) + "\n"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(names["certificate"], pdf)
            z.writestr(names["evidence"], proof.manifest_json.encode("utf-8"))
            z.writestr(names["evidence_hash"], f"{ev.manifest_hash}  {names['evidence'].rsplit('/', 1)[-1]}\n")
            if proof.ots_data is not None:
                z.writestr(names["ots"], bytes(proof.ots_data))
            z.writestr(guide.get("filename", "guia_tecnica.txt"), guide_text.encode("utf-8"))
            if self.include_originals:
                for f in files:
                    if f.storage_reference:  # without a reference, the server did not keep the file
                        z.writestr(f"{names['originals']}/{f.filename}", self.storage.read_file(f.storage_reference))
        return buffer.getvalue()
