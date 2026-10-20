from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.core.evidence_requests import hash_token, new_token

class EmailVerificationManager:
    """
    Email confirmation before the form. A registration is requested with an email, a link arrives by email and the link
    opens the form. The link works until the form is submitted (used_at) or it expires. Only the token hash is stored.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, email: str, ttl_hours: int, lang: str = "en") -> Tuple[str, str]:
        """
        Creates a new link for the email. Returns (id, token); the token only exists here and in the email.
        An email has a single live link: requesting a new one invalidates the unused previous ones for that email.
        """
        token = new_token()
        expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE email_verifications SET expires_at = CURRENT_TIMESTAMP "
                     "WHERE email = :e AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP"),
                {"e": email})
            row = conn.execute(
                text("INSERT INTO email_verifications (email, token_hash, expires_at, lang) VALUES (:e, :h, :x, :l) RETURNING id"),
                {"e": email, "h": hash_token(token), "x": expires, "l": "es" if lang == "es" else "en"}).one()
        return str(row.id), token

    def open(self, token: str) -> Optional[str]:
        """Opens the link: if it works (exists, unused and not expired) it confirms the email and returns it. Otherwise None."""
        if not token:
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE email_verifications SET confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP) "
                     "WHERE token_hash = :h AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP RETURNING email"),
                {"h": hash_token(token)}).first()
        return row.email if row else None

    def lang_of(self, token: str, default: str = "es") -> str:
        """Language in which the client requested the registration ('en' or 'es'), even if the link already expired or was used. If unknown, `default`."""
        if not token:
            return default
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT lang FROM email_verifications WHERE token_hash = :h"), {"h": hash_token(token)}).first()
        return row.lang if row else default

    def peek(self, token: str) -> Optional[str]:
        """Returns the email of a link that still works, without using it up (to validate the form before submitting it)."""
        if not token:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT email FROM email_verifications "
                     "WHERE token_hash = :h AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP"),
                {"h": hash_token(token)}).first()
        return row.email if row else None

    def consume(self, token: str) -> Optional[str]:
        """Uses up the link when the form is submitted, atomically: two submissions with the same link cannot both go through."""
        if not token:
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE email_verifications SET used_at = CURRENT_TIMESTAMP "
                     "WHERE token_hash = :h AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP RETURNING email"),
                {"h": hash_token(token)}).first()
        return row.email if row else None

    def release(self, token: str) -> None:
        """Gives back a used link that did not end up registering anything (saving failed). If it expired or was replaced, it stays unusable."""
        if not token:
            return
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE email_verifications SET used_at = NULL WHERE token_hash = :h"), {"h": hash_token(token)})
