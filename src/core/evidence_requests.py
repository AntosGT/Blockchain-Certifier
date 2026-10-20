import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.models.entities import EvidenceRequestSchema

_COLUMNS = ("id, email, name, country, document_id, title, category, description, text_title, text_content, "
            "text_authors, declaration_version, declaration_accepted_at, status, verification_expires_at, "
            "evidence_id, error, created_at, updated_at, processed_at, lang")

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def new_token() -> str:
    return secrets.token_urlsafe(32)

def _row_to_request(row) -> EvidenceRequestSchema:
    data = {c.strip(): getattr(row, c.strip()) for c in _COLUMNS.split(",")}
    data["id"] = str(data["id"])
    return EvidenceRequestSchema(**data)

class RequestManager:
    """Certification requests: they exist before the evidence and wait for email verification (and payment)."""

    def __init__(self, engine: Engine, storage_root: str, access_secret: str = "dev-only-session-secret"):
        self.engine = engine
        self._access_secret = access_secret.encode("utf-8")
        self.staging_root = os.path.join(os.path.abspath(storage_root), "_requests")

    def access_token_for(self, request_id: str) -> str:
        """Access token for the status page. Derived from the id and a secret, so notifications can rebuild the link
        without storing the token in clear (only its hash is kept in the database)."""
        digest = hmac.new(self._access_secret, f"access:{request_id}".encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def staging_bytes(self) -> int:
        """Bytes of files in transit (staging folder). Used to stop new requests if the temporary space fills up."""
        total = 0
        for folder, _, names in os.walk(self.staging_root):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
        return total

    def stage_path(self, request_id: str, filename: str) -> str:
        directory = os.path.join(self.staging_root, request_id)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)

    def create(self, request_id: str, fields: Dict[str, Any], files: List[Dict[str, Any]],
               verification_ttl_hours: int, source: str = "portal",
               project: Optional[str] = None) -> Tuple[EvidenceRequestSchema, str, str]:
        """Inserts the request. Returns (request, verification token, access token); only their hash is stored.
        `source` says where it came in (portal or mcp) and `project` is the client's project (MCP only)."""
        verification_token, access_token = new_token(), self.access_token_for(request_id)
        expires = datetime.now(timezone.utc) + timedelta(hours=verification_ttl_hours)
        with self.engine.begin() as conn:
            row = conn.execute(
                text("INSERT INTO evidence_requests (id, email, name, country, document_id, title, category, description, "
                     "text_title, text_content, text_authors, declaration_version, declaration_accepted_at, "
                     "verification_token_hash, verification_expires_at, access_token_hash, lang, source, project) "
                     "VALUES (:id, :email, :name, :country, :doc, :title, :category, :description, :tt, :tc, :ta, :dv, "
                     "CURRENT_TIMESTAMP, :vh, :ve, :ah, :lg, :src, :prj) RETURNING " + _COLUMNS),
                {"id": request_id, "email": fields["email"], "name": fields["name"], "country": fields["country"],
                 "doc": fields.get("document_id"), "title": fields["title"], "category": fields["category"],
                 "description": fields.get("description", ""), "tt": fields.get("text_title"),
                 "tc": fields.get("text_content"), "ta": fields.get("text_authors"),
                 "dv": fields["declaration_version"], "vh": hash_token(verification_token), "ve": expires,
                 "ah": hash_token(access_token), "lg": "en" if fields.get("lang") == "en" else "es",
                 "src": source, "prj": project}
            ).one()
            for f in files:
                conn.execute(
                    text("INSERT INTO request_files (request_id, filename, size, storage_path) VALUES (:r, :f, :s, :p)"),
                    {"r": request_id, "f": f["filename"], "s": f["size"], "p": f["storage_path"]}
                )
        return _row_to_request(row), verification_token, access_token

    def get(self, request_id: str) -> Optional[EvidenceRequestSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT " + _COLUMNS + " FROM evidence_requests WHERE id = :i"), {"i": request_id}).first()
        return _row_to_request(row) if row else None

    def get_with_access_token(self, request_id: str, access_token: str) -> Optional[EvidenceRequestSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM evidence_requests WHERE id = :i AND access_token_hash = :h"),
                {"i": request_id, "h": hash_token(access_token)}
            ).first()
        return _row_to_request(row) if row else None

    def get_by_verification_token(self, token: str) -> Optional[EvidenceRequestSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM evidence_requests WHERE verification_token_hash = :h"),
                {"h": hash_token(token)}
            ).first()
        return _row_to_request(row) if row else None

    def claim_for_processing(self, request_id: str) -> bool:
        """Takes the request atomically: two clicks or two processes do not create two evidences."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE evidence_requests SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = :i AND status = 'PENDING_EMAIL' AND verification_expires_at > CURRENT_TIMESTAMP "
                     "RETURNING id"),
                {"i": request_id}
            ).first()
        return row is not None

    def mark_expired(self, request_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE evidence_requests SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = :i AND status = 'PENDING_EMAIL'"), {"i": request_id})

    def mark_processed(self, request_id: str, evidence_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE evidence_requests SET status = 'PROCESSED', evidence_id = :e, error = NULL, "
                     "processed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = :i"), {"e": evidence_id, "i": request_id})

    def mark_failed(self, request_id: str, error: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE evidence_requests SET status = 'FAILED', error = :err, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = :i"), {"err": error[:2000], "i": request_id})

    def list_requests(self, status: Optional[str] = None, limit: int = 200) -> List[EvidenceRequestSchema]:
        sql = "SELECT " + _COLUMNS + " FROM evidence_requests"
        params: Dict[str, Any] = {"n": limit}
        if status:
            sql += " WHERE status = :s"
            params["s"] = status
        sql += " ORDER BY created_at DESC LIMIT :n"
        with self.engine.connect() as conn:
            return [_row_to_request(r) for r in conn.execute(text(sql), params).all()]

    def get_files(self, request_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT filename, size, storage_path FROM request_files WHERE request_id = :r ORDER BY filename"),
                {"r": request_id}).all()
        return [{"filename": r.filename, "size": r.size, "storage_path": r.storage_path} for r in rows]
