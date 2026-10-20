import os
import shutil
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.labels import FINAL_STAGES
from src.core.notification_events import NotificationEvents

_FAILURE_REASONS = {
    "OTS_SUBMISSION_FAILED": "No se pudo pedir el sello a los calendarios.",
    "BITCOIN_VERIFICATION_FAILED": "La prueba no coincide con el bloque de Bitcoin. Es una alarma de integridad.",
    "PROOF_MISSING": "Falta la prueba guardada de la evidencia.",
    "CERTIFICATE_FAILED": "No se pudo emitir el certificado.",
}

def overdue_operations(engine: Engine, deadlines: Dict[str, int]) -> List[Dict[str, Any]]:
    """Operations that have been in their stage longer than the configured deadline. The delay is computed, never stored."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT request_id, evidence_id, title, name, email, stage, stage_since, "
            "EXTRACT(EPOCH FROM (now() - stage_since)) / 60 AS minutes FROM operation_stage")).all()
    result = []
    for r in rows:
        deadline = deadlines.get(r.stage)
        if r.stage in FINAL_STAGES or deadline is None or r.stage_since is None:
            continue
        if r.minutes > deadline:
            op = dict(r._mapping)
            op["minutes"] = int(r.minutes)
            op["deadline"] = deadline
            result.append(op)
    return result


class ControlProcess:
    """
    Control: watches every queue.
    - Expires the requests whose verification link has already expired.
    - Notifies the admin of failed operations and of those past their stage deadline.
    Each notification is created only once per stage entry: it is not repeated on every run.
    """

    def __init__(self, engine: Engine, events: NotificationEvents, deadlines: Dict[str, int],
                 staging_root: Optional[str] = None, purge_after_hours: int = 168):
        self.engine = engine
        self.events = events
        self.deadlines = deadlines
        self.staging_root = staging_root
        self.purge_after_hours = purge_after_hours

    def run(self, summary: Dict[str, int], stats: Dict[str, int]) -> None:
        stats["advanced"] += self._expire()
        self._purge_expired_files()
        failed = self._failed_operations()
        stats["lifted"] = failed["reviewed"]
        stats["failed"] += failed["alerted"]
        for op in overdue_operations(self.engine, self.deadlines):
            if self.events.overdue(op, op["minutes"], op["deadline"]):
                stats["deferred"] += 1

    def _expire(self) -> int:
        with self.engine.begin() as conn:
            rows = conn.execute(text(
                "UPDATE evidence_requests SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP "
                "WHERE status = 'PENDING_EMAIL' AND verification_expires_at < CURRENT_TIMESTAMP RETURNING id")).all()
        return len(rows)

    def _purge_expired_files(self) -> None:
        """Deletes the uploaded files of requests that expired without confirming the email (they do not pile up on disk)."""
        if not self.staging_root:
            return
        with self.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(text(
                "SELECT id FROM evidence_requests WHERE status = 'EXPIRED' "
                "AND updated_at < now() - make_interval(hours => :h)"), {"h": self.purge_after_hours})]
        for request_id in ids:
            request_id = str(request_id)
            if os.sep in request_id or "/" in request_id or ".." in request_id:
                continue
            shutil.rmtree(os.path.join(self.staging_root, request_id), ignore_errors=True)

    def _failed_operations(self) -> Dict[str, int]:
        with self.engine.connect() as conn:
            ops = [dict(r._mapping) for r in conn.execute(text(
                "SELECT request_id, evidence_id, title, name, stage, stage_since FROM operation_stage "
                "WHERE stage = 'fallida'")).all()]
        alerted = 0
        for op in ops:
            if self.events.failure(op, self._reason(op)):
                alerted += 1
        with self.engine.connect() as conn:
            reviewed = conn.execute(text("SELECT count(*) FROM operation_stage WHERE stage NOT IN ('entregada', 'expirada')")).scalar()
        return {"alerted": alerted, "reviewed": reviewed}

    def _reason(self, op: Dict[str, Any]) -> str:
        with self.engine.connect() as conn:
            request_error = conn.execute(text("SELECT error FROM evidence_requests WHERE id = :i"),
                                         {"i": op["request_id"]}).scalar()
            event = None
            if op.get("evidence_id"):
                event = conn.execute(text(
                    "SELECT event_type, metadata FROM audit_events WHERE evidence_id = :e AND event_type = ANY(:t) "
                    "ORDER BY seq DESC LIMIT 1"), {"e": op["evidence_id"], "t": list(_FAILURE_REASONS)}).first()
        if request_error:
            return request_error
        if event:
            detail = (event.metadata or {}).get("error")
            return _FAILURE_REASONS[event.event_type] + (f" ({detail})" if detail else "")
        return "Sin detalle registrado."
