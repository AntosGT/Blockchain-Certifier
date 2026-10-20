from typing import Any, Dict, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core import notifications as kinds
from src.core.config import Settings
from src.core.evidence_requests import RequestManager
from src.core.labels import CATEGORY_LABELS, STAGE_LABELS
from src.core.notifications import NotificationManager
from src.models.entities import EvidenceRequestSchema

def _utc(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else ""

def _entered(operation: Dict[str, Any]) -> str:
    """Mark of when the operation entered its current stage: one notification per entry, not one per run."""
    since = operation.get("stage_since")
    return str(int(since.timestamp())) if since else "0"

class NotificationEvents:
    """
    Which notifications each pipeline step creates and with which data. Only the minimum needed for the email travels:
    name, email, title, identifiers and links. Never files or evidence content.
    """

    def __init__(self, notifications: NotificationManager, settings: Settings, requests: RequestManager, engine: Engine):
        self.notifications = notifications
        self.settings = settings
        self.requests = requests
        self.engine = engine

    # Enlaces ----------------------------------------------------------------------

    def _base(self) -> str:
        return self.settings.public_base_url.rstrip("/")

    def status_url(self, request_id: str) -> str:
        return f"{self._base()}/solicitudes/{request_id}?t={self.requests.access_token_for(request_id)}"

    def admin_url(self, path: str) -> str:
        return f"{self._base()}/admin/{path}"

    def _admin(self, kind: str, dedupe_key: str, payload: Dict[str, Any], request_id: Optional[str],
               evidence_id: Optional[str] = None) -> Optional[str]:
        if not self.settings.admin_email:
            return None  # without an admin address configured there is nobody to notify
        return self.notifications.enqueue(kind, dedupe_key, self.settings.admin_email, "Administración", payload,
                                          request_id=request_id, evidence_id=evidence_id)

    # Cliente ----------------------------------------------------------------------

    def email_confirmation(self, verification_id: str, email: str, token: str, lang: str = "en",
                           purpose: Optional[str] = None) -> None:
        """Registration request: email with the link that confirms the email and opens the form. There is no request yet.
        `purpose` marks what the link was requested for (`mcp`: connect the MCP); without it, it is the portal and the email is unchanged."""
        payload = {"form_url": f"{self._base()}/formulario?t={token}", "expires_hours": self.settings.email_link_ttl_hours,
                   "lang": lang}
        if purpose:
            payload["purpose"] = purpose
        self.notifications.enqueue(kinds.CONFIRMACION_EMAIL, verification_id, email, None, payload)

    def request_received(self, request: EvidenceRequestSchema) -> None:
        """Form received (with the email already confirmed): notice to the admin."""
        self._admin(kinds.SOLICITUD_NUEVA, request.id,
                    {"client_name": request.name, "client_email": request.email, "title": request.title,
                     "category_label": CATEGORY_LABELS.get(request.category, request.category),
                     "admin_url": self.admin_url(f"solicitudes/{request.id}")}, request.id)

    def certified(self, evidence_id: str) -> Optional[str]:
        """Certificate issued: confirmation email with the certificate. Without an associated request there is nobody to notify."""
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT r.id AS request_id, r.name, r.email, r.title, r.lang, r.source, r.project, e.manifest_hash, c.bitcoin_block_height, c.bitcoin_block_time "
                "FROM evidence_requests r JOIN evidences e ON e.id = r.evidence_id JOIN certificates c ON c.evidence_id = e.id "
                "WHERE e.id = :e"), {"e": evidence_id}).first()
        if not row:
            return None
        request_id = str(row.request_id)
        payload = {"title": row.title, "evidence_id": evidence_id, "evidence_hash": row.manifest_hash,
                   "block_height": row.bitcoin_block_height, "block_date": _utc(row.bitcoin_block_time), "has_certificate": True,
                   "status_url": self.status_url(request_id), "lang": row.lang}
        if row.project:  # MCP registration: the email says in which project of their folder to save the certificate
            payload["project"] = row.project
            payload["source"] = row.source
        return self.notifications.enqueue(
            kinds.CONFIRMACION_REGISTRO, request_id, row.email, row.name, payload,
            request_id=request_id, evidence_id=evidence_id)

    # Administration ----------------------------------------------------------------

    def failure(self, operation: Dict[str, Any], reason: str) -> Optional[str]:
        return self._admin(kinds.FALLA, f"{operation['request_id']}:{_entered(operation)}",
                           {"client_name": operation["name"], "title": operation["title"], "stage": operation["stage"],
                            "stage_label": STAGE_LABELS.get(operation["stage"], operation["stage"]), "reason": reason,
                            "admin_url": self.admin_url(f"solicitudes/{operation['request_id']}")},
                           str(operation["request_id"]), operation.get("evidence_id"))

    def overdue(self, operation: Dict[str, Any], minutes_in_stage: int, deadline_minutes: int) -> Optional[str]:
        stage = operation["stage"]
        return self._admin(kinds.ETAPA_ATRASADA, f"{operation['request_id']}:{stage}:{_entered(operation)}",
                           {"client_name": operation["name"], "title": operation["title"], "stage": stage,
                            "stage_label": STAGE_LABELS.get(stage, stage), "minutes_in_stage": minutes_in_stage,
                            "deadline_minutes": deadline_minutes,
                            "admin_url": self.admin_url(f"solicitudes/{operation['request_id']}")},
                           str(operation["request_id"]), operation.get("evidence_id"))
