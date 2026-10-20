from typing import Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.evidence_manager import EvidenceManager
from src.core.notifications import NotificationManager
from src.core.request_service import RequestService
from src.core.state_machine import EvidenceState

class AdminActions:
    """
    Manual admin actions on what got stuck. None of them edits an evidence or its hash:
    they only send an operation back to the queue where it failed, or requeue a notification. They are kept in the audit log.
    """

    def __init__(self, engine: Engine, evidence: EvidenceManager, notifications: NotificationManager,
                 request_service: RequestService):
        self.engine = engine
        self.evidence = evidence
        self.notifications = notifications
        self.request_service = request_service

    def requeue_notice(self, notification_id: str) -> bool:
        return self.notifications.requeue(notification_id)

    def retry_seal(self, evidence_id: str, admin_email: str) -> None:
        """Sends an evidence that failed to request its timestamp back to the Registrar queue."""
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT e.status, p.id AS proof_id, (p.ots_data IS NOT NULL) AS has_seal FROM evidences e "
                "LEFT JOIN proofs p ON p.evidence_id = e.id WHERE e.id = :i"), {"i": evidence_id}).first()
        if not row:
            raise KeyError(f"Evidencia {evidence_id} no encontrada.")
        if row.status != EvidenceState.FAILED.value:
            raise ValueError("Solo se puede reintentar el sellado de una evidencia fallida.")
        if not row.proof_id or row.has_seal:
            raise ValueError("Esta falla no es de sellado: no se reintenta desde acá.")
        self.evidence.update_evidence_status(evidence_id, EvidenceState.HASHED, actor=admin_email)
        self.evidence.audit_logger.log_event(evidence_id, "ADMIN_RETRY_SEAL", admin_email, {})

    def retry_certificate(self, evidence_id: str, admin_email: str) -> None:
        """Sends a verified evidence whose certificate could not be issued back to the Certifier queue."""
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "SELECT e.status, (SELECT count(*) FROM certificates c WHERE c.evidence_id = e.id) AS certs "
                "FROM evidences e WHERE e.id = :i"), {"i": evidence_id}).first()
            if not row:
                raise KeyError(f"Evidencia {evidence_id} no encontrada.")
            if row.status != EvidenceState.VERIFIED.value or row.certs:
                raise ValueError("Solo se puede reintentar el certificado de una evidencia verificada sin certificado.")
            self.evidence.audit_logger.log_event(evidence_id, "CERTIFICATE_RETRY", admin_email, {}, conn=conn)
            conn.execute(text(
                "UPDATE evidence_requests SET status = 'PROCESSED', error = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE evidence_id = :i AND status = 'FAILED'"), {"i": evidence_id})
