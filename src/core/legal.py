from typing import Optional, Dict
from sqlalchemy import text
from sqlalchemy.engine import Engine

class LegalVersionManager:
    """Legal versions (declaration, terms, privacy). Append only; the database blocks editing or deleting."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def add_version(self, kind: str, version: str, body: str, lang: str = "es") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO legal_versions (kind, version, text, lang) VALUES (:k, :v, :t, :l)"),
                {"k": kind, "v": version, "t": body, "l": lang}
            )

    def get(self, kind: str, version: str) -> Optional[Dict[str, str]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kind, version, text FROM legal_versions WHERE kind = :k AND version = :v"),
                {"k": kind, "v": version}
            ).first()
        return {"kind": row.kind, "version": row.version, "text": row.text} if row else None

    def latest(self, kind: str, lang: Optional[str] = None) -> Optional[Dict[str, str]]:
        """Latest version of the kind. With `lang` it only looks at versions written in that language; without `lang`, all of them (the certificate)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kind, version, text FROM legal_versions WHERE kind = :k AND (CAST(:l AS TEXT) IS NULL OR lang = :l) "
                     "ORDER BY created_at DESC, version DESC LIMIT 1"),
                {"k": kind, "l": lang}
            ).first()
        return {"kind": row.kind, "version": row.version, "text": row.text} if row else None
