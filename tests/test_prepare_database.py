import unittest
import uuid
from sqlalchemy import text
from src.core.db import MIGRATIONS_DIR, make_engine, pending_migrations, prepare_database
from db_helper import PostgresTestCase

ALL = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))

class TestPrepareDatabase(PostgresTestCase):
    """In staging and production the app does not apply migrations (it does not own the tables): if one is missing, it does not start."""

    def empty_schema(self):
        schema = "t_" + uuid.uuid4().hex[:12]
        admin = make_engine()
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        admin.dispose()
        engine = make_engine(schema=schema)

        def drop():
            engine.dispose()
            cleanup = make_engine()
            with cleanup.begin() as conn:
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            cleanup.dispose()
        self.addCleanup(drop)
        return engine

    def test_with_everything_applied_staging_and_production_start(self):
        self.assertEqual(pending_migrations(self.engine), [])
        for env in ("staging", "production"):
            self.assertEqual(prepare_database(self.engine, env), [])

    def test_a_missing_migration_stops_staging_and_production_without_touching_the_base(self):
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM schema_migrations WHERE name = :n"), {"n": ALL[-1]})
        for env in ("staging", "production"):
            with self.assertRaises(SystemExit) as stop:
                prepare_database(self.engine, env)
            self.assertIn(ALL[-1], str(stop.exception))
            self.assertIn("prima-migrate", str(stop.exception))
        self.assertEqual(pending_migrations(self.engine), [ALL[-1]])

    def test_an_empty_base_has_everything_pending_and_development_applies_it(self):
        engine = self.empty_schema()
        self.assertEqual(pending_migrations(engine), ALL)
        with self.assertRaises(SystemExit):
            prepare_database(engine, "staging")
        self.assertEqual(prepare_database(engine, "development"), ALL)
        self.assertEqual(pending_migrations(engine), [])

if __name__ == "__main__":
    unittest.main()
