import json
from typing import Any, Callable, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

# Notification types. The prefix says who it goes to: client or admin.
CONFIRMACION_EMAIL = "cliente.confirmacion_email"
RECEPCION_SOLICITUD = "cliente.recepcion_solicitud"
RECEPCION_PAGO = "cliente.recepcion_pago"  # no longer sent (2026-10-08); the type stays to show old notifications
CONFIRMACION_REGISTRO = "cliente.confirmacion_registro"
SOLICITUD_NUEVA = "admin.solicitud_nueva"
FALLA = "admin.falla"
ETAPA_ATRASADA = "admin.etapa_atrasada"
KINDS = {CONFIRMACION_EMAIL, RECEPCION_SOLICITUD, RECEPCION_PAGO, CONFIRMACION_REGISTRO, SOLICITUD_NUEVA, FALLA, ETAPA_ATRASADA}

# Wait between retries, in minutes, by attempt number. After the last one it is marked as failed.
BACKOFF_MINUTES = [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
LEASE_MINUTES = 5

_COLUMNS = ("id, request_id, evidence_id, kind, channel, recipient_name, recipient_email, payload, dedupe_key, "
            "status, attempts, last_error, created_at, next_attempt_at, sent_at")

def _row(r) -> Dict[str, Any]:
    d = dict(r._mapping)
    d["id"] = str(d["id"])
    d["request_id"] = str(d["request_id"]) if d["request_id"] else None
    return d


class NotificationManager:
    """
    Notification queue. Each notification is born when a process completes its step, and the queue consumer sends it.
    - Unique per (type, key): queueing the same notification twice does not duplicate it.
    - Whoever takes it "leases" it for a few minutes: if delivery is not confirmed, it reappears. At-least-once delivery.
    - Failed after exhausting the retries: it stays visible and can be resent manually.
    """

    def __init__(self, engine: Engine, max_attempts: int = 10, wake: Optional[Callable[[], None]] = None):
        self.engine = engine
        self.max_attempts = min(max_attempts, len(BACKOFF_MINUTES))
        self.wake = wake  # tells the queue consumer there is a new one, so it does not wait for its clock

    def enqueue(self, kind: str, dedupe_key: str, recipient_email: Optional[str], recipient_name: Optional[str],
                payload: Dict[str, Any], request_id: Optional[str] = None, evidence_id: Optional[str] = None,
                channel: str = "email", conn: Optional[Connection] = None) -> Optional[str]:
        """Queues a notification. Returns its id, or None if an identical one already existed."""
        if kind not in KINDS:
            raise ValueError(f"Tipo de aviso desconocido: {kind}")
        sql = text(
            "INSERT INTO notifications (request_id, evidence_id, kind, channel, recipient_name, recipient_email, payload, dedupe_key) "
            "VALUES (:r, :e, :k, :c, :n, :m, CAST(:p AS jsonb), :d) ON CONFLICT (kind, dedupe_key) DO NOTHING RETURNING id")
        params = {"r": request_id, "e": evidence_id, "k": kind, "c": channel, "n": recipient_name, "m": recipient_email,
                  "p": json.dumps(payload, ensure_ascii=False), "d": dedupe_key}
        if conn is not None:
            row = conn.execute(sql, params).first()
        else:
            with self.engine.begin() as own:
                row = own.execute(sql, params).first()
            if row and self.wake:
                self.wake()  # it is already committed in the database: when n8n asks for it, it finds it
        return str(row.id) if row else None

    def lease_pending(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Takes the notifications ready to send and reserves them for a few minutes, so two consumers do not send the same one."""
        with self.engine.begin() as conn:
            rows = conn.execute(text(
                "UPDATE notifications SET next_attempt_at = now() + make_interval(mins => :lease) WHERE id IN ("
                " SELECT id FROM notifications WHERE status = 'pending' AND next_attempt_at <= now() "
                " ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED) RETURNING " + _COLUMNS),
                {"lease": LEASE_MINUTES, "n": limit}).all()
        return sorted((_row(r) for r in rows), key=lambda d: d["created_at"])

    def get(self, notification_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text("SELECT " + _COLUMNS + " FROM notifications WHERE id = :i"), {"i": notification_id}).first()
        except Exception:
            return None  # id with an invalid format
        return _row(row) if row else None

    def mark_sent(self, notification_id: str) -> bool:
        """Idempotent: confirming the same delivery twice changes nothing."""
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "UPDATE notifications SET status = 'sent', sent_at = COALESCE(sent_at, now()), last_error = NULL "
                "WHERE id = :i RETURNING id"), {"i": notification_id}).first()
        return row is not None

    def mark_failed(self, notification_id: str, error: str) -> Optional[str]:
        """Records a failed attempt. Returns the resulting status: 'pending' (retried) or 'failed' (retries exhausted)."""
        with self.engine.begin() as conn:
            current = conn.execute(text("SELECT attempts, status FROM notifications WHERE id = :i FOR UPDATE"),
                                   {"i": notification_id}).first()
            if not current or current.status == "sent":
                return None
            attempts = current.attempts + 1
            if attempts >= self.max_attempts:
                conn.execute(text("UPDATE notifications SET attempts = :a, status = 'failed', last_error = :e WHERE id = :i"),
                             {"a": attempts, "e": error[:2000], "i": notification_id})
                return "failed"
            delay = BACKOFF_MINUTES[attempts - 1]
            conn.execute(text("UPDATE notifications SET attempts = :a, last_error = :e, "
                              "next_attempt_at = now() + make_interval(mins => :d) WHERE id = :i"),
                         {"a": attempts, "e": error[:2000], "d": delay, "i": notification_id})
            return "pending"

    def requeue(self, notification_id: str) -> bool:
        """Manual action: puts a failed notification back in the queue."""
        with self.engine.begin() as conn:
            row = conn.execute(text(
                "UPDATE notifications SET status = 'pending', attempts = 0, last_error = NULL, next_attempt_at = now() "
                "WHERE id = :i AND status = 'failed' RETURNING id"), {"i": notification_id}).first()
        return row is not None

    def list(self, status: Optional[str] = None, kind: Optional[str] = None, request_id: Optional[str] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT " + _COLUMNS + " FROM notifications WHERE 1 = 1"
        params: Dict[str, Any] = {"n": limit}
        if status:
            sql += " AND status = :s"
            params["s"] = status
        if kind:
            sql += " AND kind = :k"
            params["k"] = kind
        if request_id:
            sql += " AND request_id = :r"
            params["r"] = request_id
        sql += " ORDER BY created_at DESC LIMIT :n"
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(text(sql), params)]

    def counts(self) -> Dict[str, int]:
        with self.engine.connect() as conn:
            return {r.status: r.n for r in conn.execute(text("SELECT status, count(*) AS n FROM notifications GROUP BY status"))}
