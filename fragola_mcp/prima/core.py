import hashlib
import json
import os
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
KEY_FILE = "prima.json"
DECLARATION_FILE = "declaration.json"
TIMEOUT = 60

class PrimaError(Exception):
    """Algo que el usuario puede corregir; el mensaje se le muestra tal cual."""

class PrimaBusy(PrimaError):
    """El servidor está saturado. No es un error: hay que esperar unos minutos."""

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
    max_video_mb: int = 25  # servicio gratuito; con suscripción paga se amplía
    video_height: int = 480

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        missing = [k for k in ("PRIMA_EMAIL", "PRIMA_NAME", "PRIMA_COUNTRY") if not env.get(k)]
        if missing:
            raise PrimaError("Falta configurar: " + ", ".join(missing) + ".")
        cfg = cls(
            api_url=env.get("PRIMA_API_URL", "https://certified.fragola.agency").rstrip("/"),
            folder=Path(env.get("PRIMA_FOLDER") or Path.cwd() / "Certificaciones"), email=env["PRIMA_EMAIL"],
            name=env["PRIMA_NAME"], country=env["PRIMA_COUNTRY"],
            config_dir=Path(env.get("FRAGOLA_MCP_HOME") or Path.home() / ".fragola_mcp"),
            document_id=env.get("PRIMA_DOCUMENT_ID", ""), max_file_mb=int(env.get("PRIMA_MAX_FILE_MB", 50)),
            max_video_mb=int(env.get("PRIMA_MAX_VIDEO_MB", 25)))
        cfg.key = env.get("PRIMA_KEY") or load_key(cfg)
        return cfg

# Clave del cliente (se obtiene una sola vez confirmando el email) --------------------------

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
    """El comprobante de la última declaración pedida, o None."""
    try:
        data = json.loads(receipt_path(cfg).read_text(encoding="utf-8"))
        return data if data.get("version") and data.get("receipt") else None
    except Exception:
        return None

def extract_token(link: str) -> str:
    """El código del correo: acepta el enlace completo (…?t=XXXX) o solo el código."""
    link = (link or "").strip()
    token = parse_qs(urlparse(link).query).get("t", [""])[0] if "t=" in link else link
    if not token:
        raise PrimaError("No encuentro el código en lo que pegaste. Pegá el enlace completo del correo.")
    return token

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def project_dir(cfg: Config, project: str) -> Path:
    project = (project or "").strip()
    if not project or project in (".", "..") or any(c in project for c in '/\\:*?"<>|') or project.startswith("."):
        raise PrimaError("El nombre del proyecto no es válido: no puede estar vacío ni llevar / \\ : * ? \" < > |.")
    return cfg.folder / project

# Registro local de lo certificado --------------------------------------------------------

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

def find_entry(index: Dict[str, Any], sha256: str) -> Optional[Dict[str, Any]]:
    return next((e for e in index["entries"] if e["sha256"] == sha256), None)

# Límites del servicio gratuito: solo peso (el servidor aplica el mismo) ------------------------

def check_limits(cfg: Config, path: Path) -> None:
    size = path.stat().st_size
    if size == 0:
        raise PrimaError(f"El archivo {path.name} está vacío.")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise PrimaError(f"El tipo de archivo de {path.name} no está aceptado.")
    if size > cfg.max_file_mb * 1024 * 1024:
        raise PrimaError(f"Excediste el límite: {path.name} supera los {cfg.max_file_mb} MB del servicio gratuito.")
    if path.suffix.lower() in VIDEO_EXTENSIONS and size > cfg.max_video_mb * 1024 * 1024:
        raise PrimaError(
            f"Excediste el límite: el video {path.name} supera los {cfg.max_video_mb} MB del servicio gratuito. "
            f"Reducilo a {cfg.video_height}p con la herramienta prima_reducir_video y registrá ese archivo reducido; "
            "conservá ese mismo archivo para verificarlo después.")

def reduce_video(cfg: Config, path: Path) -> Path:
    """Reduce el video a 480p con ffmpeg. Devuelve el archivo nuevo; el original no se toca. Hay que registrar el reducido."""
    if not shutil.which("ffmpeg"):
        raise PrimaError("ffmpeg no está instalado en esta PC. Instalalo para poder reducir videos.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise PrimaError("Solo se reducen videos .mp4 o .mov.")
    target = path.with_name(f"{path.stem}_{cfg.video_height}p.mp4")
    for crf in (28, 32):
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-vf", f"scale=-2:{cfg.video_height}", "-c:v", "libx264", "-crf", str(crf),
             "-preset", "medium", "-c:a", "aac", "-b:a", "96k", str(target)], capture_output=True, text=True)
        if proc.returncode != 0:
            detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "error desconocido."
            raise PrimaError("ffmpeg no pudo reducir el video: " + detail)
        if target.stat().st_size <= cfg.max_video_mb * 1024 * 1024:
            return target
    raise PrimaError(f"Aun reducido, el video pesa más de {cfg.max_video_mb} MB. Recortalo o dividilo y registrá cada parte.")

# Cliente del servidor ----------------------------------------------------------------------

class PrimaClient:
    def __init__(self, cfg: Config, session: Any = None):
        self.cfg = cfg
        self.session = session or requests.Session()

    def _raise(self, response: Any) -> None:
        try:
            error = response.json().get("error", {})
        except Exception:
            error = {}
        message = error.get("message") or f"El servidor respondió {response.status_code}."
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
            raise PrimaError("Todavía no estás conectado. Usá prima_conectar para recibir el correo y prima_confirmar_conexion "
                             "para pegar el enlace.")

    def declaration(self) -> Dict[str, str]:
        """Pide la declaración al servidor. Devuelve versión, texto y el comprobante firmado de haberla pedido."""
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

# Operaciones --------------------------------------------------------------------------------

def connect(cfg: Config, client: PrimaClient) -> Dict[str, Any]:
    """Pide el correo con el enlace de confirmación. El email se confirma una sola vez."""
    sent = client.connect()
    return {"sent_to": sent["sent_to"], "link_hours": sent.get("link_hours"),
            "siguiente": "Pedile a la persona que copie el enlace del correo y se lo pegue a Claude (prima_confirmar_conexion)."}

def confirm_connection(cfg: Config, client: PrimaClient, link: str) -> Dict[str, Any]:
    """Gasta el enlace del correo, recibe la clave y la guarda en esta PC. La clave no se muestra."""
    data = client.confirm(extract_token(link))
    cfg.key = data["key"]
    save_key(cfg, data["key"], data["email"])
    return {"email": data["email"], "message": "Conectado. Ya se pueden registrar archivos."}

def show_declaration(cfg: Config, client: PrimaClient) -> Dict[str, str]:
    """Pide la declaración y guarda el comprobante en esta PC. Sin ese comprobante el servidor no deja registrar."""
    data = client.declaration()
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    receipt_path(cfg).write_text(json.dumps({"version": data["version"], "receipt": data["receipt"]}), encoding="utf-8")
    return {"version": data["version"], "text": data["text"],
            "instruccion": "Mostrale este texto completo a la persona y pedile que lo acepte de forma explícita antes de registrar."}

def register_file(cfg: Config, client: PrimaClient, project: str, path: str, title: str = "", category: str = "documento",
                  description: str = "", declaration_accepted: bool = False) -> Dict[str, Any]:
    """Registra un archivo. El original queda en la carpeta del proyecto, tal cual; al servidor solo viaja la huella."""
    if not declaration_accepted:
        raise PrimaError("Para registrar, la persona tiene que aceptar la declaración. Mostrásela con prima_ver_declaracion y pedile confirmación.")
    client.require_key()  # primero conectarse; recién después tiene sentido pedir la declaración
    receipt = load_receipt(cfg)
    if receipt is None:
        raise PrimaError("Antes de registrar hay que pedir la declaración con prima_ver_declaracion, mostrársela a la persona y "
                         "obtener su aceptación.")
    pdir = project_dir(cfg, project)
    source = Path(path).expanduser()
    if not source.is_file():
        raise PrimaError(f"No encuentro el archivo {path}.")
    pdir.mkdir(parents=True, exist_ok=True)
    inside = pdir.resolve() in source.resolve().parents
    target = source if inside else pdir / source.name
    if not inside:
        if target.exists() and sha256_file(target) != sha256_file(source):
            raise PrimaError(f"En el proyecto ya hay otro archivo llamado {source.name}. Cambiale el nombre y probá de nuevo.")
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
    index["entries"].append(entry)
    save_index(pdir, index)
    return entry

def process_pending(cfg: Config, client: PrimaClient, project: Optional[str] = None, category: str = "documento",
                    declaration_accepted: bool = False) -> Dict[str, Any]:
    """Cola de pendientes: registra todo archivo de la carpeta Certificaciones que todavía no esté en el registro local."""
    if not cfg.folder.is_dir():
        return {"registered": [], "skipped": [], "message": f"La carpeta {cfg.folder} todavía no existe."}
    projects = [project] if project else sorted(p.name for p in cfg.folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    registered, skipped = [], []
    for name in projects:
        pdir = project_dir(cfg, name)
        if not pdir.is_dir():
            skipped.append({"project": name, "reason": "El proyecto no existe."})
            continue
        for path in sorted(pdir.rglob("*")):
            rel = path.relative_to(pdir)
            if not path.is_file() or rel.parts[0] in (INDEX_DIR, CERTIFICATES_DIR):
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
    """Consulta al servidor cómo va cada registro del proyecto y actualiza el registro local."""
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
