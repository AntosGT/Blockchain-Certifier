from typing import Any, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

def _like(term: str) -> str:
    """ILIKE pattern with the user's wildcards escaped: searching '%' does not return everything."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

def _rows(result) -> List[Dict[str, Any]]:
    return [dict(r._mapping) for r in result.all()]

class AdminQueries:
    """Read-only queries for the admin dashboard. Nothing here modifies data."""

    def __init__(self, engine: Engine):
        self.engine = engine

    # Resumen ----------------------------------------------------------------------

    def request_counts(self) -> Dict[str, int]:
        with self.engine.connect() as conn:
            return {r.status: r.n for r in conn.execute(
                text("SELECT status, count(*) AS n FROM evidence_requests GROUP BY status"))}

    def evidence_counts(self) -> Dict[str, int]:
        with self.engine.connect() as conn:
            return {r.status: r.n for r in conn.execute(
                text("SELECT status, count(*) AS n FROM evidences GROUP BY status"))}

    def total_users(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM users WHERE role = 'user'")).scalar()

    def list_clients(self) -> List[Dict[str, Any]]:
        """Known clients (by email), with their name and how many requests they have. Includes those who have not confirmed yet."""
        with self.engine.connect() as conn:
            return _rows(conn.execute(text(
                "SELECT email, (array_agg(name ORDER BY created_at DESC))[1] AS name, count(*) AS requests "
                "FROM evidence_requests GROUP BY email ORDER BY lower((array_agg(name ORDER BY created_at DESC))[1]), email")))

    def stale_pending_notices(self, minutes: int = 10) -> int:
        """Notifications pending for longer than usual: a sign that nobody is consuming them."""
        with self.engine.connect() as conn:
            return conn.execute(text(
                "SELECT count(*) FROM notifications WHERE status = 'pending' AND created_at < now() - make_interval(mins => :m)"),
                {"m": minutes}).scalar()

    # Stages and processes -----------------------------------------------------------------

    def stage_counts(self) -> Dict[str, Dict[str, Any]]:
        """Operations per stage, with the time of the oldest one in each."""
        with self.engine.connect() as conn:
            return {r.stage: {"n": r.n, "oldest": r.oldest} for r in conn.execute(text(
                "SELECT stage, count(*) AS n, min(stage_since) AS oldest FROM operation_stage GROUP BY stage"))}

    def search_operations(self, stage: Optional[str] = None, q: Optional[str] = None, limit: int = 200,
                          client: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT request_id, evidence_id, title, category, name, email, stage, stage_since, created_at "
               "FROM operation_stage WHERE 1 = 1")
        params: Dict[str, Any] = {"n": limit}
        if stage:
            sql += " AND stage = :stage"
            params["stage"] = stage
        if client:
            sql += " AND email = :client"
            params["client"] = client.lower()
        if q:
            sql += " AND (title ILIKE :q ESCAPE '\\' OR name ILIKE :q ESCAPE '\\' OR email ILIKE :q ESCAPE '\\' OR evidence_id ILIKE :q ESCAPE '\\')"
            params["q"] = _like(q)
        sql += " ORDER BY created_at DESC LIMIT :n"
        with self.engine.connect() as conn:
            return _rows(conn.execute(text(sql), params))

    def last_runs(self) -> Dict[str, Dict[str, Any]]:
        """
        The last recorded run of each worker process, plus what it did in the last 24 hours. The worker runs
        every minute and almost every run is empty: without the totals, the panel would drop back to zero 60 seconds
        after working and it would look as if it never did anything.
        """
        with self.engine.connect() as conn:
            runs = {r["process"]: r for r in _rows(conn.execute(text(
                "SELECT DISTINCT ON (process) process, started_at, lifted, advanced, failed, deferred, error "
                "FROM worker_runs ORDER BY process, started_at DESC")))}
            totals = _rows(conn.execute(text(
                "SELECT process, "
                "coalesce(sum(advanced) FILTER (WHERE started_at > now() - interval '24 hours'), 0) AS advanced_24h, "
                "coalesce(sum(failed) FILTER (WHERE started_at > now() - interval '24 hours'), 0) AS failed_24h, "
                "max(started_at) FILTER (WHERE advanced > 0 OR failed > 0) AS last_work_at "
                "FROM worker_runs GROUP BY process")))
        for t in totals:
            if t["process"] in runs:
                runs[t["process"]].update(advanced_24h=int(t["advanced_24h"]), failed_24h=int(t["failed_24h"]),
                                          last_work_at=t["last_work_at"])
        return runs

    # Solicitudes ------------------------------------------------------------------

    def search_requests(self, status: Optional[str] = None, q: Optional[str] = None, limit: int = 200,
                        client: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT r.id, r.title, r.category, r.name, r.email, r.status, r.created_at, r.evidence_id, "
               "(SELECT count(*) FROM request_files f WHERE f.request_id = r.id) AS files_count "
               "FROM evidence_requests r WHERE 1 = 1")
        params: Dict[str, Any] = {"n": limit}
        if status:
            sql += " AND r.status = :status"
            params["status"] = status
        if client:
            sql += " AND r.email = :client"
            params["client"] = client.lower()
        if q:
            sql += " AND (r.title ILIKE :q ESCAPE '\\' OR r.name ILIKE :q ESCAPE '\\' OR r.email ILIKE :q ESCAPE '\\')"
            params["q"] = _like(q)
        sql += " ORDER BY r.created_at DESC LIMIT :n"
        with self.engine.connect() as conn:
            return _rows(conn.execute(text(sql), params))

    def request_detail(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, email, name, country, document_id, title, category, description, text_title, "
                "text_authors, (text_content IS NOT NULL) AS has_text, declaration_version, declaration_accepted_at, "
                "status, verification_expires_at, evidence_id, error, created_at, updated_at, processed_at "
                "FROM evidence_requests WHERE id = :i"), {"i": request_id}).first()
            if not row:
                return None
            detail = dict(row._mapping)
            stage = conn.execute(text("SELECT stage, stage_since FROM operation_stage WHERE request_id = :i"), {"i": request_id}).first()
            detail["stage"], detail["stage_since"] = (stage.stage, stage.stage_since) if stage else (None, None)
            detail["files"] = _rows(conn.execute(text(
                "SELECT filename, size FROM request_files WHERE request_id = :i ORDER BY filename"), {"i": request_id}))
        return detail

    # Evidencias -------------------------------------------------------------------

    def search_evidences(self, status: Optional[str] = None, category: Optional[str] = None,
                         q: Optional[str] = None, limit: int = 200, client: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT e.id, e.title, e.category, e.status, e.created_at, e.confirmed_at, e.manifest_hash, "
               "u.name AS owner_name, u.email AS owner_email, p.verification_status AS proof_status, "
               "p.bitcoin_block_height, (p.ots_data IS NOT NULL) AS has_seal, "
               "(SELECT a.metadata->>'method' FROM audit_events a WHERE a.evidence_id = e.id "
               " AND a.event_type = 'PAYMENT_CONFIRMED' ORDER BY a.seq LIMIT 1) AS payment_method "
               "FROM evidences e JOIN users u ON u.id = e.user_id LEFT JOIN proofs p ON p.evidence_id = e.id WHERE 1 = 1")
        params: Dict[str, Any] = {"n": limit}
        if status:
            sql += " AND e.status = :status"
            params["status"] = status
        if category:
            sql += " AND e.category = :category"
            params["category"] = category
        if client:
            sql += " AND u.email = :client"
            params["client"] = client.lower()
        if q:
            sql += (" AND (e.id ILIKE :q ESCAPE '\\' OR e.title ILIKE :q ESCAPE '\\' OR u.name ILIKE :q ESCAPE '\\' "
                    "OR u.email ILIKE :q ESCAPE '\\' OR e.manifest_hash ILIKE :q ESCAPE '\\')")
            params["q"] = _like(q)
        sql += " ORDER BY e.created_at DESC LIMIT :n"
        with self.engine.connect() as conn:
            return _rows(conn.execute(text(sql), params))

    def evidence_detail(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT e.id, e.title, e.category, e.description, e.status, e.privacy, e.manifest_hash, "
                "e.previous_evidence_id, e.created_at, e.confirmed_at, e.declaration_version, e.declaration_accepted_at, "
                "u.name AS owner_name, u.email AS owner_email, u.country AS owner_country, u.document_id AS owner_document "
                "FROM evidences e JOIN users u ON u.id = e.user_id WHERE e.id = :i"), {"i": evidence_id}).first()
            if not row:
                return None
            detail = dict(row._mapping)
            proof = conn.execute(text(
                "SELECT manifest_json, verification_status, bitcoin_block_height, bitcoin_tx_hash, verified_at, bitcoin_block_time, "
                "(ots_data IS NOT NULL) AS has_seal FROM proofs WHERE evidence_id = :i"), {"i": evidence_id}).first()
            detail["proof"] = dict(proof._mapping) if proof else None
            cert = conn.execute(text(
                "SELECT template_version, issued_at, pdf_sha256, bitcoin_block_height, bitcoin_block_time "
                "FROM certificates WHERE evidence_id = :i"), {"i": evidence_id}).first()
            detail["certificate"] = dict(cert._mapping) if cert else None
            detail["files"] = _rows(conn.execute(text(
                "SELECT filename, mime, size, sha256 FROM evidence_files WHERE evidence_id = :i ORDER BY filename"),
                {"i": evidence_id}))
            detail["events"] = _rows(conn.execute(text(
                "SELECT event_type, actor, timestamp, metadata FROM audit_events WHERE evidence_id = :i ORDER BY seq"),
                {"i": evidence_id}))
            detail["chain"] = _rows(conn.execute(text(
                "WITH RECURSIVE chain AS (SELECT id, title, status, previous_evidence_id, 0 AS depth FROM evidences WHERE id = :i "
                "UNION ALL SELECT e.id, e.title, e.status, e.previous_evidence_id, c.depth + 1 "
                "FROM evidences e JOIN chain c ON e.id = c.previous_evidence_id) "
                "SELECT id, title, status FROM chain ORDER BY depth DESC"), {"i": evidence_id}))
            detail["newer_versions"] = _rows(conn.execute(text(
                "SELECT id, title, status FROM evidences WHERE previous_evidence_id = :i ORDER BY created_at"),
                {"i": evidence_id}))
            req = conn.execute(text("SELECT id FROM evidence_requests WHERE evidence_id = :i"), {"i": evidence_id}).first()
            detail["request_id"] = str(req.id) if req else None
        return detail

    def recent_events(self, limit: int = 12) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            return _rows(conn.execute(text(
                "SELECT a.evidence_id, a.event_type, a.actor, a.timestamp, e.title FROM audit_events a "
                "JOIN evidences e ON e.id = a.evidence_id ORDER BY a.seq DESC LIMIT :n"), {"n": limit}))
