import unittest
import uuid
from sqlalchemy import text
from src.core.db import make_engine, apply_migrations
from src.core.users import UserManager
from src.models.entities import UserSchema

def deliver_notifications(svc):
    """Runs the local notification consumer once: sends whatever is pending through the test mailer."""
    stats = {"lifted": 0, "advanced": 0, "failed": 0, "deferred": 0}
    svc.delivery.deliver_pending(stats)
    return stats


def link_token(svc, email="ana@example.com", lang="es"):
    """Token of the emailed link for that email, without going through sending the email (the form opens with it)."""
    return svc.request_service.verifications.create(email, 24, lang)[1]


class PostgresTestCase(unittest.TestCase):
    """Each test runs in its own Postgres schema (created, migrated and dropped), without touching other data."""

    def setUp(self):
        self.schema = "t_" + uuid.uuid4().hex[:12]
        admin = make_engine()
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {self.schema}"))
        admin.dispose()
        self.engine = make_engine(schema=self.schema)
        self.addCleanup(self._drop_schema)
        apply_migrations(self.engine)
        self.users = UserManager(self.engine)
        self.user = self.users.create_user(UserSchema(email="owner@example.com", name="Test Owner", country="AR"))

    def _drop_schema(self):
        self.engine.dispose()
        admin = make_engine()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE"))
        admin.dispose()
