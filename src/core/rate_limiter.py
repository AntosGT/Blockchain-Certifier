import hashlib
from sqlalchemy import text
from sqlalchemy.engine import Engine

class RateLimiter:
    """
    Request limit per key over a one-hour window, stored in Postgres: it survives restarts and applies to
    every process. Each limit has a name, so the same key (an IP) is counted separately in each one.
    The key is stored hashed. The time is set by the database, not by the process.
    """

    WINDOW = "1 hour"

    def __init__(self, engine: Engine, name: str, max_per_hour: int):
        self.engine = engine
        self.name = name
        self.max = max_per_hour

    def allow(self, key: str) -> bool:
        """Counts the request and returns True if it fits in the limit. If the cap was already reached, it does not count it and returns False."""
        key_hash = hashlib.sha256(f"{self.name}:{key}".encode("utf-8")).hexdigest()
        with self.engine.begin() as conn:
            # One request at a time per key: without this, two simultaneous requests could both pass right at the cap
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key_hash})
            # Prune expired entries of any key (one-off keys do not pile up); SKIP LOCKED avoids waiting for another request
            conn.execute(text(
                f"DELETE FROM rate_limits WHERE id IN (SELECT id FROM rate_limits "
                f"WHERE hit_at < CURRENT_TIMESTAMP - interval '{self.WINDOW}' FOR UPDATE SKIP LOCKED)"))
            used = conn.execute(
                text(f"SELECT count(*) FROM rate_limits WHERE key_hash = :k AND hit_at >= CURRENT_TIMESTAMP - interval '{self.WINDOW}'"),
                {"k": key_hash}).scalar()
            if used >= self.max:
                return False
            conn.execute(text("INSERT INTO rate_limits (key_hash) VALUES (:k)"), {"k": key_hash})
        return True
