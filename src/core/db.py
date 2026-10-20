import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://prima:prima_dev@127.0.0.1:55432/prima_dev"
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

def get_database_url() -> str:
    return os.environ.get("PROOF_DATABASE_URL", DEFAULT_DATABASE_URL)

def make_engine(url: str = None, schema: str = None) -> Engine:
    """Creates the engine. With `schema` it sets the search_path (isolation in tests)."""
    connect_args = {"options": f"-csearch_path={schema}"} if schema else {}
    return create_engine(url or get_database_url(), connect_args=connect_args, pool_pre_ping=True)

def apply_migrations(engine: Engine) -> list:
    """Applies the pending .sql migrations in order and returns the names applied."""
    applied = []
    with engine.begin() as conn:
        # The app and the worker start at the same time: the lock (released when the transaction closes) makes the second one wait for the first.
        conn.execute(text("SELECT pg_advisory_xact_lock(7420001)"))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        done = {r[0] for r in conn.execute(text("SELECT name FROM schema_migrations"))}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            # Driver cursor without parameters: the migrations SQL uses '%' in trigger messages.
            conn.connection.driver_connection.execute(path.read_text(encoding="utf-8"))
            conn.execute(text("INSERT INTO schema_migrations(name) VALUES (:n)"), {"n": path.name})
            applied.append(path.name)
    return applied

def pending_migrations(engine: Engine) -> list:
    """Names of the .sql migrations not applied yet. Read only: it does not need to own the tables."""
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass('schema_migrations')")).scalar()
        done = {r[0] for r in conn.execute(text("SELECT name FROM schema_migrations"))} if exists else set()
    return [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in done]

def prepare_database(engine: Engine, env: str) -> list:
    """
    In development it applies the pending migrations. In staging and production the app does not own the tables and cannot
    change the structure: migrations are applied by `python -m src.migrate` with the owner user, and if one is missing
    the app does not start.
    """
    if env == "development":
        return apply_migrations(engine)
    pending = pending_migrations(engine)
    if pending:
        raise SystemExit("There are unapplied migrations: " + ", ".join(pending) + ". Run first: "
                         "docker-compose -f docker/docker-compose.prod.yml --env-file .env run --rm prima-migrate")
    return []
