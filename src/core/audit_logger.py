import json
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection
from src.models.entities import AuditEventSchema

_COLUMNS = "id, evidence_id, event_type, actor, timestamp, metadata"

def _row_to_event(row) -> AuditEventSchema:
    return AuditEventSchema(
        id=str(row.id), evidence_id=row.evidence_id, event_type=row.event_type,
        actor=row.actor, timestamp=row.timestamp, metadata=row.metadata
    )

class AuditLoggerManager:
    """
    Audit log in PostgreSQL:
    - Insert only: database triggers block UPDATE, DELETE and TRUNCATE.
    - `log_event` accepts an open connection to record the event in the same transaction as the audited change.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def log_event(self, evidence_id: str, event_type: str, actor: str, metadata: Dict[str, Any] = None,
                  conn: Optional[Connection] = None) -> AuditEventSchema:
        sql = text(
            "INSERT INTO audit_events (evidence_id, event_type, actor, metadata) "
            "VALUES (:e, :t, :a, CAST(:m AS jsonb)) RETURNING " + _COLUMNS
        )
        params = {"e": evidence_id, "t": event_type, "a": actor, "m": json.dumps(metadata or {})}
        if conn is not None:
            return _row_to_event(conn.execute(sql, params).one())
        with self.engine.begin() as own:
            return _row_to_event(own.execute(sql, params).one())

    def get_events_for_evidence(self, evidence_id: str) -> List[AuditEventSchema]:
        """Events of an evidence in chronological insertion order."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT " + _COLUMNS + " FROM audit_events WHERE evidence_id = :e ORDER BY seq"),
                {"e": evidence_id}
            ).all()
        return [_row_to_event(r) for r in rows]

    def get_all_events(self) -> List[AuditEventSchema]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT " + _COLUMNS + " FROM audit_events ORDER BY seq")).all()
        return [_row_to_event(r) for r in rows]
