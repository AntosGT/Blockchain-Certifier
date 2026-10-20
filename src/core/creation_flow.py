import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from src.models.entities import UserSchema, EvidenceSchema
from src.core.hasher import generate_salt_hex
from src.core.manifest import generate_manifest_bundle
from src.core.text_evidence import create_text_evidence_file
from src.core.storage import PrivateStorageManager
from src.core.state_machine import EvidenceState
from src.core.evidence_manager import EvidenceManager
from src.core.countries import COUNTRY_CODES
from src.core.i18n import tr
from src.core.legal import LegalVersionManager
from src.core.payments import PaymentGate
from src.core.ots_worker import EvidenceRecord, OTSWorkerService

# File types accepted in the MVP (PRD section 3).
ALLOWED_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".zip": "application/zip",
}

# The category is metadata; the evidentiary mechanism is identical (PRD section 4).
CATEGORIES = frozenset({
    "idea", "concepto_creativo", "formato_audiovisual", "guion", "tratamiento", "diseno", "investigacion",
    "proyecto", "propuesta_comercial", "codigo", "documento", "fotografia", "video", "audio", "composicion",
    "version_obra", "dataset", "logica_de_sistema", "sistema", "otro",
})

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")

DECLARATION_KIND = "declaration"
CERTIFICATION_LEVEL = "Estándar: sello de tiempo respaldado por Bitcoin"


class EvidenceValidationError(ValueError):
    """
    Invalid input in one of steps 1 to 4. Portal errors carry a code from the catalog
    (`coded`): the Spanish text comes from the catalog and the portal shows it in the client's language with `message(lang)`.
    """

    def __init__(self, message: str, code: Optional[str] = None, **params):
        super().__init__(message)
        self.code = code
        self.params = params

    @classmethod
    def coded(cls, code: str, **params) -> "EvidenceValidationError":
        return cls(tr("es", code, **params), code, **params)

    def message(self, lang: str) -> str:
        return tr(lang, self.code, **self.params) if self.code else str(self)


def validate_filename(fname: str) -> str:
    """Validates the name and extension of an uploaded file. Returns the extension in lowercase."""
    if not fname or fname != os.path.basename(fname) or "\\" in fname or fname in (".", ".."):
        raise EvidenceValidationError.coded("file.name_invalid", name=repr(fname))
    if len(fname) > 255:
        raise EvidenceValidationError.coded("file.name_too_long", name=repr(fname[:40]))
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_MIME_BY_EXTENSION:
        raise EvidenceValidationError.coded("file.type_not_accepted", name=repr(fname))
    return ext


def validate_hashed_file(h: Dict[str, Any]) -> None:
    """Validates a file that arrives only as a fingerprint (MCP): name, hexadecimal SHA-256 and positive size."""
    validate_filename(h["filename"])
    if not _SHA256_HEX.match(str(h.get("sha256") or "")):
        raise EvidenceValidationError.coded("file.hash_invalid", name=repr(h["filename"]))
    if isinstance(h.get("size"), bool) or not isinstance(h.get("size"), int) or h["size"] <= 0:
        raise EvidenceValidationError.coded("file.empty", name=repr(h["filename"]))


class EvidenceCreationPipeline:
    """
    Evidence creation pipeline in 7 steps (PRD section 5):
    Step 1: title, category and description.
    Step 2: attached files or text record.
    Step 3: declared identity (name, verified email, country, optional document).
    Step 4: versioned declaration; the exact accepted version is stored.
    Step 5: summary before payment.
    Step 6: payment validated by the `PaymentGate` set in the settings (bypass today; Stripe is task 07).
    Step 7: manifest, private storage, OpenTimestamps timestamp.
    """

    def __init__(self, storage_manager: PrivateStorageManager, evidence_manager: EvidenceManager,
                 ots_worker: OTSWorkerService, legal_versions: LegalVersionManager,
                 payment_gate: PaymentGate, price_usd: float, id_prefix: str = "PRIMA", store_originals: bool = True):
        self.store_originals = store_originals  # False: the fingerprint is computed and the file is not kept (policy since 2026-10-10)
        self.storage_manager = storage_manager
        self.evidence_manager = evidence_manager
        self.ots_worker = ots_worker
        self.legal_versions = legal_versions
        self.payment_gate = payment_gate
        self.price_usd = price_usd
        self.id_prefix = id_prefix

    # Validation (steps 1 to 3) -----------------------------------------------------

    @staticmethod
    def validate_inputs(
        user: UserSchema,
        title: str,
        category: str,
        description: str,
        files_input: List[Tuple[str, bytes]],
        text_input: Optional[Dict[str, str]] = None,
        hashed_files: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        # Step 1
        if not title or not title.strip():
            raise EvidenceValidationError("Title is required.")
        if len(title.strip()) > 255:
            raise EvidenceValidationError("Title cannot exceed 255 characters.")
        if category not in CATEGORIES:
            raise EvidenceValidationError(f"Invalid category: {category!r}.")

        # Step 2
        if not files_input and not text_input and not hashed_files:
            raise EvidenceValidationError("At least one file or text entry is required.")
        seen = set()
        for fname, fbytes in files_input:
            validate_filename(fname)
            if not fbytes:
                raise EvidenceValidationError(f"File {fname!r} is empty.")
            if fname.lower() in seen:
                raise EvidenceValidationError(f"Duplicate filename: {fname!r}.")
            seen.add(fname.lower())
        # Files the server never received: they arrive with the fingerprint computed on the client's computer (MCP)
        for h in hashed_files or []:
            validate_hashed_file(h)
            if h["filename"].lower() in seen:
                raise EvidenceValidationError(f"Duplicate filename: {h['filename']!r}.")
            seen.add(h["filename"].lower())
        if text_input and not (text_input.get("content") or "").strip():
            raise EvidenceValidationError("The text entry has no content.")

        # Step 3
        if not user.name or len(user.name.strip()) < 2:
            raise EvidenceValidationError("Full name is required.")
        if not user.email_verified:
            raise PermissionError("The user's email must be verified (Step 3).")
        if user.country not in COUNTRY_CODES:
            raise EvidenceValidationError("Country must be a 2-letter uppercase ISO code.")

    # Step 5 -----------------------------------------------------------------------

    def build_summary(
        self,
        title: str,
        category: str,
        description: str,
        user: UserSchema,
        files_input: List[Tuple[str, bytes]],  # (filename, content_bytes)
        declaration_version: str,
        text_input: Optional[Dict[str, str]] = None,  # {"title", "content", "authors"}
    ) -> Dict[str, Any]:
        """Summary before payment: content, files, declared identity, date, price and certification level."""
        self.validate_inputs(user, title, category, description, files_input, text_input)
        declaration = self.legal_versions.get(DECLARATION_KIND, declaration_version)
        if not declaration:
            raise EvidenceValidationError(f"Declaration version {declaration_version!r} does not exist.")

        files_summary = [{"filename": fname, "size": len(fbytes)} for fname, fbytes in files_input]
        if text_input:
            files_summary.append({
                "filename": f"text_entry_{text_input['title']}.md",
                "size": len((text_input.get("content") or "").encode("utf-8"))
            })

        return {
            "title": title.strip(),
            "category": category,
            "description": description,
            "owner": {"name": user.name, "email": user.email, "country": user.country, "document_id": user.document_id},
            "declaration_version": declaration["version"],
            "declaration_text": declaration["text"],
            "files_count": len(files_summary),
            "files": files_summary,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "price_usd": self.price_usd,
            "certification_level": CERTIFICATION_LEVEL
        }

    # Steps 6 and 7 -----------------------------------------------------------------

    def execute_creation_flow(
        self,
        user: UserSchema,
        title: str,
        category: str,
        description: str,
        files_input: List[Tuple[str, bytes]],
        declaration_accepted: bool,
        declaration_version: str,
        text_input: Optional[Dict[str, str]] = None,
        previous_evidence_id: Optional[str] = None,
        payment_reference: Optional[str] = None,
        defer_timestamp: bool = False,
        hashed_files: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[EvidenceSchema, Dict[str, Any], str]:
        """
        Runs steps 4, 6 and 7. Returns (evidence, manifest_dict, manifest_hash).
        Every validation runs before anything is created. If the timestamp fails after payment, the evidence
        stays in HASHED with its proof stored and the OTS_SUBMISSION_FAILED event, and the worker retries.
        With `defer_timestamp` it is not stamped here: the evidence stays in HASHED and the worker stamps it (web portal).
        """
        self.validate_inputs(user, title, category, description, files_input, text_input, hashed_files)
        if not declaration_accepted:
            raise PermissionError("The user must accept the declaration before generating the evidence (Step 4).")
        declaration = self.legal_versions.get(DECLARATION_KIND, declaration_version)
        if not declaration:
            raise EvidenceValidationError(f"Declaration version {declaration_version!r} does not exist.")

        payment = self.payment_gate.verify(user, self.price_usd, payment_reference)
        if not payment.confirmed:
            raise PermissionError("Payment must be confirmed before generating the evidence package (Step 6).")

        evidence_id = f"{self.id_prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        title = title.strip()

        # Base record, with the exact version of the accepted declaration
        self.evidence_manager.create_evidence(
            evidence_id=evidence_id,
            user_id=user.id,
            title=title,
            category=category,
            description=description,
            previous_evidence_id=previous_evidence_id,
            actor=user.email,
            declaration_version=declaration["version"]
        )

        self.evidence_manager.audit_logger.log_event(
            evidence_id=evidence_id, event_type="PAYMENT_CONFIRMED", actor="system",
            metadata={"method": payment.method, "reference": payment.reference, "amount_usd": payment.amount_usd}
        )

        files_metadata = []

        for fname, fbytes in files_input:
            if self.store_originals:
                storage_ref, sha256_hex = self.storage_manager.store_file(evidence_id, fname, fbytes)
            else:
                storage_ref, sha256_hex = "", hashlib.sha256(fbytes).hexdigest()
            files_metadata.append({
                "filename": fname,
                "mime": ALLOWED_MIME_BY_EXTENSION[os.path.splitext(fname)[1].lower()],
                "sha256": sha256_hex,
                "size": len(fbytes),
                "salt_hex": generate_salt_hex(32),
                "storage_reference": storage_ref
            })

        for h in hashed_files or []:
            files_metadata.append({
                "filename": h["filename"],
                "mime": ALLOWED_MIME_BY_EXTENSION[os.path.splitext(h["filename"])[1].lower()],
                "sha256": h["sha256"].lower(),
                "size": h["size"],
                "salt_hex": generate_salt_hex(32),
                "storage_reference": ""
            })

        if text_input:
            txt_path, txt_sha256, txt_size = create_text_evidence_file(
                title=text_input.get("title", title),
                content=text_input.get("content", ""),
                description=description,
                authors=text_input.get("authors", user.name),
                output_dir=os.path.join(self.storage_manager.storage_root, evidence_id)
            )
            txt_filename = os.path.basename(txt_path)
            txt_ref = f"{evidence_id}/{txt_filename}"
            if not self.store_originals:
                # Policy (2026-10-12): the server does not keep user files; the text record is discarded after computing its fingerprint
                os.remove(txt_path)
                try:
                    os.rmdir(os.path.dirname(txt_path))
                except OSError:
                    pass
                txt_ref = ""
            files_metadata.append({
                "filename": txt_filename,
                "mime": "text/markdown",
                "sha256": txt_sha256,
                "size": txt_size,
                "salt_hex": generate_salt_hex(32),
                "storage_reference": txt_ref
            })

        for meta in files_metadata:
            self.evidence_manager.add_file(evidence_id, meta, actor=user.email)

        # Canonical manifest (RFC 8785) with the text and version of the accepted declaration
        manifest_dict, canonical_bytes, manifest_hash = generate_manifest_bundle(
            evidence_id=evidence_id,
            title=title,
            category=category,
            owner_name=user.name,
            owner_email=user.email,
            owner_country=user.country,
            files_metadata=files_metadata,
            declaration_text=declaration["text"],
            declaration_version=declaration["version"]
        )

        self.evidence_manager.set_manifest_hash(evidence_id, manifest_hash, actor="system")
        self.evidence_manager.update_evidence_status(evidence_id, EvidenceState.HASHED, actor="system")

        if defer_timestamp:
            self.evidence_manager.save_proof(evidence_id, canonical_bytes.decode("utf-8"), manifest_hash, ots_bytes=None, actor="system")
            return self.evidence_manager.get_evidence(evidence_id), manifest_dict, manifest_hash

        # Timestamp: the final status comes from the worker's actual result
        record = EvidenceRecord(evidence_id=evidence_id, manifest_hash=manifest_hash, status=EvidenceState.HASHED)
        submitted = self.ots_worker.process_submission(record)
        manifest_json = canonical_bytes.decode("utf-8")

        if submitted and record.status == EvidenceState.PENDING_BITCOIN and record.ots_bytes:
            self.evidence_manager.save_proof(evidence_id, manifest_json, manifest_hash, ots_bytes=record.ots_bytes, actor="system")
            self.evidence_manager.update_evidence_status(evidence_id, EvidenceState.SUBMITTED, actor="system")
            self.evidence_manager.update_evidence_status(evidence_id, EvidenceState.PENDING_BITCOIN, actor="system")
        else:
            self.evidence_manager.save_proof(evidence_id, manifest_json, manifest_hash, ots_bytes=None, actor="system")
            self.evidence_manager.audit_logger.log_event(
                evidence_id=evidence_id, event_type="OTS_SUBMISSION_FAILED", actor="system",
                metadata={"error": record.last_error, "retry_count": record.retry_count}
            )
            if record.status == EvidenceState.FAILED:
                self.evidence_manager.update_evidence_status(evidence_id, EvidenceState.FAILED, actor="system")

        return self.evidence_manager.get_evidence(evidence_id), manifest_dict, manifest_hash
