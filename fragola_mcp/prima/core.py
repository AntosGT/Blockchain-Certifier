import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mp3", ".wav", ".zip"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
INDEX_DIR = ".prima"
INDEX_FILE = "registro.json"
CERTIFICATES_DIR = "certificados"
METADATA_FILE = "prima-metadata.jsonld"
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
KEY_FILE = "prima.json"
DECLARATION_FILE = "declaration.json"
TIMEOUT = 60

class PrimaError(Exception):
    """Something the user can fix; the message is shown to them as is."""

class PrimaBusy(PrimaError):
    """The server is busy. It is not an error: wait a few minutes."""

@dataclass
class Config:
    api_url: str
    folder: Path
    email: str
    name: str
    country: str
    config_dir: Path
    key: str = ""
    lang: str = "en"
    document_id: str = ""
    max_file_mb: int = 50
    max_video_mb: int = 25  # free service; a paid subscription raises it
    video_height: int = 480

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        missing = [k for k in ("PRIMA_EMAIL", "PRIMA_NAME", "PRIMA_COUNTRY") if not env.get(k)]
        if missing:
            raise PrimaError("Missing configuration: " + ", ".join(missing) + ".")
        cfg = cls(
            api_url=env.get("PRIMA_API_URL", "https://certified.fragola.agency").rstrip("/"),
            folder=Path(env.get("PRIMA_FOLDER") or Path.cwd() / "Certificaciones"), email=env["PRIMA_EMAIL"],
            name=env["PRIMA_NAME"], country=env["PRIMA_COUNTRY"],
            config_dir=Path(env.get("FRAGOLA_MCP_HOME") or Path.home() / ".fragola_mcp"),
            document_id=env.get("PRIMA_DOCUMENT_ID", ""), max_file_mb=int(env.get("PRIMA_MAX_FILE_MB", 50)),
            max_video_mb=int(env.get("PRIMA_MAX_VIDEO_MB", 25)))
        cfg.key = env.get("PRIMA_KEY") or load_key(cfg)
        return cfg

# Client key (obtained once, by confirming the email) --------------------------------------

def load_key(cfg: Config) -> str:
    path = cfg.config_dir / KEY_FILE
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("key", "")
    except Exception:
        return ""

def save_key(cfg: Config, key: str, email: str) -> None:
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.config_dir / KEY_FILE
    path.write_text(json.dumps({"key": key, "email": email}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

def receipt_path(cfg: Config) -> Path:
    return cfg.config_dir / DECLARATION_FILE

def load_receipt(cfg: Config) -> Optional[Dict[str, str]]:
    """The receipt of the last requested declaration, or None."""
    try:
        data = json.loads(receipt_path(cfg).read_text(encoding="utf-8"))
        return data if data.get("version") and data.get("receipt") else None
    except Exception:
        return None

def extract_token(link: str) -> str:
    """The code from the email: accepts the full link (...?t=XXXX) or just the code."""
    link = (link or "").strip()
    token = parse_qs(urlparse(link).query).get("t", [""])[0] if "t=" in link else link
    if not token:
        raise PrimaError("No code found in what was pasted. Paste the full link from the email.")
    return token

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def normalize_orcid(orcid: str) -> str:
    """Optional ORCID: accepts the iD or the https://orcid.org/... link and returns the iD. Empty if not given."""
    code = (orcid or "").strip().replace("https://orcid.org/", "").replace("http://orcid.org/", "").upper()
    if code and not ORCID_RE.match(code):
        raise PrimaError("The ORCID is not valid: it must look like 0000-0002-1825-0097.")
    return code

def project_dir(cfg: Config, project: str) -> Path:
    project = (project or "").strip()
    if not project or project in (".", "..") or any(c in project for c in '/\\:*?"<>|') or project.startswith("."):
        raise PrimaError("The project name is not valid: it cannot be empty or contain / \\ : * ? \" < > |.")
    return cfg.folder / project

# Local record of what was registered ------------------------------------------------------

def load_index(pdir: Path) -> Dict[str, Any]:
    path = pdir / INDEX_DIR / INDEX_FILE
    if not path.exists():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))

def save_index(pdir: Path, index: Dict[str, Any]) -> None:
    (pdir / INDEX_DIR).mkdir(parents=True, exist_ok=True)
    target = pdir / INDEX_DIR / INDEX_FILE
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    save_metadata(pdir, index)

# FAIR project metadata: JSON-LD with the schema.org vocabulary, without personal data or keys ----------

def build_metadata(project: str, index: Dict[str, Any]) -> Dict[str, Any]:
    parts = []
    for e in index["entries"]:
        part = {"@type": "MediaObject", "name": e["filename"], "contentUrl": e.get("relative_path") or e["filename"],
                "contentSize": f"{e['size']} bytes", "sha256": e["sha256"]}
        mime = mimetypes.guess_type(e["filename"])[0]
        if mime:
            part["encodingFormat"] = mime
        # Optional details the person chose to add; they stay only on their computer
        if e.get("description"):
            part["description"] = e["description"]
        if e.get("version"):
            part["version"] = e["version"]
        if e.get("orcid"):
            part["creator"] = {"@type": "Person", "identifier": f"https://orcid.org/{e['orcid']}"}
        if e.get("evidence_id"):
            part["identifier"] = e["evidence_id"]
        props = [{"@type": "PropertyValue", "name": name, "value": e[key]}
                 for name, key in (("registered_at", "registered_at"), ("manifest_sha256", "manifest_hash")) if e.get(key)]
        if props:
            part["additionalProperty"] = props
        parts.append(part)
    return {"@context": "https://schema.org", "@type": "Dataset", "name": project,
            "description": "Files registered with PRIMA. The SHA-256 of each file is part of an evidence manifest "
                           "whose hash is timestamped in Bitcoin.",
            "isAccessibleForFree": True, "hasPart": parts}

def save_metadata(pdir: Path, index: Dict[str, Any]) -> None:
    target = pdir / METADATA_FILE
    tmp = pdir / INDEX_DIR / (METADATA_FILE + ".tmp")
    tmp.write_text(json.dumps(build_metadata(pdir.name, index), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)

def find_entry(index: Dict[str, Any], sha256: str) -> Optional[Dict[str, Any]]:
    return next((e for e in index["entries"] if e["sha256"] == sha256), None)

# Free service limits: size only (the server applies the same ones) ---------------------------

def check_limits(cfg: Config, path: Path) -> None:
    size = path.stat().st_size
    if size == 0:
        raise PrimaError(f"The file {path.name} is empty.")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise PrimaError(f"The file type of {path.name} is not accepted.")
    if size > cfg.max_file_mb * 1024 * 1024:
        raise PrimaError(f"Limit exceeded: {path.name} is over the {cfg.max_file_mb} MB of the free service.")
    if path.suffix.lower() in VIDEO_EXTENSIONS and size > cfg.max_video_mb * 1024 * 1024:
        raise PrimaError(
            f"Limit exceeded: the video {path.name} is over the {cfg.max_video_mb} MB of the free service. "
            f"Reduce it to {cfg.video_height}p with the prima_reducir_video tool and register that reduced file; "
            "keep that same file to verify it later.")

def reduce_video(cfg: Config, path: Path) -> Path:
    """Reduces the video to 480p with ffmpeg. Returns the new file; the original is untouched. The reduced one is registered."""
    if not shutil.which("ffmpeg"):
        raise PrimaError("ffmpeg is not installed on this computer. Install it to reduce videos.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise PrimaError("Only .mp4 or .mov videos can be reduced.")
    target = path.with_name(f"{path.stem}_{cfg.video_height}p.mp4")
    for crf in (28, 32):
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-vf", f"scale=-2:{cfg.video_height}", "-c:v", "libx264", "-crf", str(crf),
             "-preset", "medium", "-c:a", "aac", "-b:a", "96k", str(target)], capture_output=True, text=True)
        if proc.returncode != 0:
            detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error."
            raise PrimaError("ffmpeg could not reduce the video: " + detail)
        if target.stat().st_size <= cfg.max_video_mb * 1024 * 1024:
            return target
    raise PrimaError(f"Even reduced, the video is over {cfg.max_video_mb} MB. Trim it or split it and register each part.")

# Server client ----------------------------------------------------------------------------

class PrimaClient:
    def __init__(self, cfg: Config, session: Any = None):
        self.cfg = cfg
        self.session = session or requests.Session()

    def _raise(self, response: Any) -> None:
        try:
            error = response.json().get("error", {})
        except Exception:
            error = {}
        message = error.get("message") or f"The server answered {response.status_code}."
        if error.get("code") == "busy":
            raise PrimaBusy(message)
        raise PrimaError(message)

    def connect(self) -> Dict[str, Any]:
        r = self.session.post(f"{self.cfg.api_url}/api/mcp/conexion", json={"email": self.cfg.email, "lang": self.cfg.lang},
                              timeout=TIMEOUT)
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    def confirm(self, token: str) -> Dict[str, Any]:
        r = self.session.post(f"{self.cfg.api_url}/api/mcp/conexion/confirmar", json={"t": token}, timeout=TIMEOUT)
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    def require_key(self) -> None:
        if not self.cfg.key:
            raise PrimaError("Not connected yet. Use prima_conectar to receive the email and prima_confirmar_conexion "
                             "to paste the link.")

    def declaration(self) -> Dict[str, str]:
        """Requests the declaration from the server. Returns version, text and the signed receipt of the request."""
        self.require_key()
        r = self.session.get(f"{self.cfg.api_url}/api/mcp/declaracion?lang={self.cfg.lang}",
                             headers={"Authorization": f"Bearer {self.cfg.key}"}, timeout=TIMEOUT)
        if r.status_code != 200:
            self._raise(r)
        d = r.json()
        return {"version": d["version"], "text": d["text"], "receipt": d["receipt"]}

    def register(self, project: str, title: str, category: str, description: str, filename: str, size: int,
                 sha256: str, declaration_version: str, declaration_receipt: str) -> Dict[str, Any]:
        self.require_key()
        payload = {"title": title, "category": category, "description": description, "name": self.cfg.name,
                   "country": self.cfg.country, "document_id": self.cfg.document_id, "lang": self.cfg.lang,
                   "declaration_version": declaration_version, "declaration_accepted": True,
                   "declaration_receipt": declaration_receipt, "project": project,
                   "files": [{"filename": filename, "size": size, "sha256": sha256}]}
        r = self.session.post(f"{self.cfg.api_url}/api/mcp/registros", json=payload,
                              headers={"Authorization": f"Bearer {self.cfg.key}"}, timeout=TIMEOUT)
        if r.status_code != 201:
            self._raise(r)
        return r.json()

    def status(self, request_id: str, access_token: str) -> Dict[str, Any]:
        r = self.session.get(f"{self.cfg.api_url}/api/solicitudes/{request_id}?t={access_token}", timeout=TIMEOUT)
        if r.status_code != 200:
            self._raise(r)
        return r.json()

# Operations ---------------------------------------------------------------------------------

def connect(cfg: Config, client: PrimaClient) -> Dict[str, Any]:
    """Requests the email with the confirmation link. The email is confirmed only once."""
    sent = client.connect()
    return {"sent_to": sent["sent_to"], "link_hours": sent.get("link_hours"),
            "next": "Ask the person to copy the link from the email and paste it to Claude (prima_confirmar_conexion)."}

def confirm_connection(cfg: Config, client: PrimaClient, link: str) -> Dict[str, Any]:
    """Uses up the emailed link, receives the key and stores it on this computer. The key is never shown."""
    data = client.confirm(extract_token(link))
    cfg.key = data["key"]
    save_key(cfg, data["key"], data["email"])
    return {"email": data["email"], "message": "Connected. Files can be registered now."}

def show_declaration(cfg: Config, client: PrimaClient) -> Dict[str, str]:
    """Requests the declaration and stores the receipt on this computer. Without it the server refuses to register."""
    data = client.declaration()
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    receipt_path(cfg).write_text(json.dumps({"version": data["version"], "receipt": data["receipt"]}), encoding="utf-8")
    return {"version": data["version"], "text": data["text"],
            "instruction": "Show this full text to the person and ask for their explicit acceptance before registering."}

def register_file(cfg: Config, client: PrimaClient, project: str, path: str, title: str = "", category: str = "documento",
                  description: str = "", declaration_accepted: bool = False, version: str = "",
                  orcid: str = "") -> Dict[str, Any]:
    """Registers a file. The original stays in the project folder, unchanged; only its fingerprint goes to the server.
    version and orcid are optional and stay on this computer (local record and metadata): never sent, never in the manifest."""
    orcid = normalize_orcid(orcid)
    if not declaration_accepted:
        raise PrimaError("To register, the person has to accept the declaration. Show it with prima_ver_declaracion and ask for confirmation.")
    client.require_key()  # connect first; only then does requesting the declaration make sense
    receipt = load_receipt(cfg)
    if receipt is None:
        raise PrimaError("Before registering, request the declaration with prima_ver_declaracion, show it to the person and "
                         "get their acceptance.")
    pdir = project_dir(cfg, project)
    source = Path(path).expanduser()
    if not source.is_file():
        raise PrimaError(f"File not found: {path}.")
    pdir.mkdir(parents=True, exist_ok=True)
    inside = pdir.resolve() in source.resolve().parents
    target = source if inside else pdir / source.name
    if target.resolve() == (pdir / METADATA_FILE).resolve():
        raise PrimaError(f"{METADATA_FILE} is the project metadata file: it is generated automatically and is not registered.")
    if not inside:
        if target.exists() and sha256_file(target) != sha256_file(source):
            raise PrimaError(f"The project already has another file named {source.name}. Rename it and try again.")
        if not target.exists():
            shutil.copy2(source, target)
    check_limits(cfg, target)
    digest = sha256_file(target)
    index = load_index(pdir)
    existing = find_entry(index, digest)
    if existing:
        return dict(existing, already_registered=True)
    result = client.register(project.strip(), title or target.stem, category, description, target.name,
                             target.stat().st_size, digest, receipt["version"], receipt["receipt"])
    entry = {"filename": target.name, "relative_path": str(target.relative_to(pdir)).replace("\\", "/"), "sha256": digest,
             "size": target.stat().st_size, "project": project.strip(), "title": title or target.stem,
             "request_id": result["id"], "access_token": result["access_token"], "evidence_id": result.get("evidence_id"),
             "status": result.get("status"), "evidence_status": None,
             "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "certificate_saved": False}
    entry.update({k: v for k, v in (("description", description.strip()), ("version", version.strip()), ("orcid", orcid)) if v})
    index["entries"].append(entry)
    save_index(pdir, index)
    return entry

def process_pending(cfg: Config, client: PrimaClient, project: Optional[str] = None, category: str = "documento",
                    declaration_accepted: bool = False) -> Dict[str, Any]:
    """Pending queue: registers every file in the Certificaciones folder that is not in the local record yet."""
    if not cfg.folder.is_dir():
        return {"registered": [], "skipped": [], "message": f"The folder {cfg.folder} does not exist yet."}
    projects = [project] if project else sorted(p.name for p in cfg.folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    registered, skipped = [], []
    for name in projects:
        pdir = project_dir(cfg, name)
        if not pdir.is_dir():
            skipped.append({"project": name, "reason": "The project does not exist."})
            continue
        for path in sorted(pdir.rglob("*")):
            rel = path.relative_to(pdir)
            if not path.is_file() or rel.parts[0] in (INDEX_DIR, CERTIFICATES_DIR) or rel == Path(METADATA_FILE):
                continue
            try:
                if find_entry(load_index(pdir), sha256_file(path)):
                    continue
                entry = register_file(cfg, client, name, str(path), declaration_accepted=declaration_accepted)
                registered.append({"project": name, "filename": entry["filename"], "request_id": entry["request_id"]})
            except PrimaBusy:
                raise
            except PrimaError as e:
                skipped.append({"project": name, "filename": str(rel), "reason": str(e)})
    return {"registered": registered, "skipped": skipped}

def refresh_status(cfg: Config, client: PrimaClient, project: str) -> List[Dict[str, Any]]:
    """Asks the server about the progress of each registration in the project and updates the local record."""
    pdir = project_dir(cfg, project)
    index = load_index(pdir)
    for entry in index["entries"]:
        data = client.status(entry["request_id"], entry["access_token"])
        entry["status"] = data.get("status")
        entry["evidence_id"] = data.get("evidence_id") or entry.get("evidence_id")
        entry["evidence_status"] = data.get("evidence_text") or None
        entry["manifest_hash"] = data.get("manifest_hash") or entry.get("manifest_hash")
    save_index(pdir, index)
    return [{k: e.get(k) for k in ("filename", "title", "status", "evidence_status", "evidence_id", "registered_at")}
            for e in index["entries"]]
