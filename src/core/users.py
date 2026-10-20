from typing import Optional, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.core.admin_auth import MIN_PASSWORD_LENGTH, hash_password
from src.models.entities import UserSchema, UserRole

_COLUMNS = "id, email, email_verified, name, country, role, document_id, identity_level, created_at"

def _row_to_user(row) -> UserSchema:
    return UserSchema(
        id=str(row.id), email=row.email, email_verified=row.email_verified, name=row.name,
        country=row.country, role=row.role, document_id=row.document_id,
        identity_level=row.identity_level, created_at=row.created_at
    )

class UserManager:
    """Users persisted in PostgreSQL."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def create_user(self, user: UserSchema) -> UserSchema:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email, email_verified, name, country, role, document_id, identity_level, created_at) "
                     "VALUES (:id, :email, :ev, :name, :country, :role, :doc, :level, :created_at)"),
                {"id": user.id, "email": user.email, "ev": user.email_verified, "name": user.name,
                 "country": user.country, "role": user.role, "doc": user.document_id,
                 "level": user.identity_level, "created_at": user.created_at}
            )
        return user

    def get_user(self, user_id: str) -> Optional[UserSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT " + _COLUMNS + " FROM users WHERE id = :i"), {"i": user_id}).first()
        return _row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[UserSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT " + _COLUMNS + " FROM users WHERE email = :e"), {"e": email}).first()
        return _row_to_user(row) if row else None

    def mark_email_verified(self, user_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE users SET email_verified = TRUE WHERE id = :i"), {"i": user_id})

    # Administradores ------------------------------------------------------------------

    def create_admin(self, email: str, name: str, password: str, country: str = "AR") -> UserSchema:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
        user = UserSchema(email=email.lower(), name=name, country=country, email_verified=True, role=UserRole.ADMIN)
        self.create_user(user)
        self.set_password(user.email, password)
        return user

    def set_password(self, email: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
        with self.engine.begin() as conn:
            result = conn.execute(
                text("UPDATE users SET password_hash = :h WHERE email = :e AND role = 'admin'"),
                {"h": hash_password(password), "e": email.lower()})
            if result.rowcount == 0:
                raise KeyError(f"No hay un administrador con el email {email}.")

    def get_admin_credentials(self, email: str) -> Optional[Tuple[UserSchema, str]]:
        """Returns (user, hash) only if it is an admin with a password. The hash never travels inside UserSchema."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + _COLUMNS + ", password_hash FROM users "
                     "WHERE email = :e AND role = 'admin' AND password_hash IS NOT NULL"),
                {"e": email.lower()}).first()
        return (_row_to_user(row), row.password_hash) if row else None

    def get_admin(self, user_id: str) -> Optional[UserSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM users WHERE id = :i AND role = 'admin' AND password_hash IS NOT NULL"),
                {"i": user_id}).first()
        return _row_to_user(row) if row else None
