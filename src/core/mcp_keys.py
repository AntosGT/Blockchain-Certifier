import secrets
from typing import Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.core.evidence_requests import hash_token

KEY_PREFIX = "prima_"

class McpKeyManager:
    """
    MCP keys. The clear key only exists when it is created (the client receives it once); the database keeps its hash.
    A key identifies an already confirmed email. Creating a new one for the same email revokes the previous ones.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def create_key(self, email: str) -> str:
        email = email.strip().lower()
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE mcp_keys SET revoked_at = CURRENT_TIMESTAMP WHERE email = :e AND revoked_at IS NULL"), {"e": email})
            conn.execute(text("INSERT INTO mcp_keys (email, key_hash) VALUES (:e, :h)"), {"e": email, "h": hash_token(key)})
        return key

    def authenticate(self, key: str) -> Optional[str]:
        """The email that owns an active key, or None."""
        if not key.startswith(KEY_PREFIX):
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE mcp_keys SET last_used_at = CURRENT_TIMESTAMP WHERE key_hash = :h AND revoked_at IS NULL RETURNING email"),
                {"h": hash_token(key)}).first()
        return row.email if row else None
