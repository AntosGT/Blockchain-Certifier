from src.core.config import get_settings
from src.core.db import apply_migrations, make_engine

def main() -> None:
    """
    Applies the pending migrations. In production it runs with the user that owns the tables (`prima_owner`), which the
    app does not use. See your own deployment's runbook for the exact command.
    """
    applied = apply_migrations(make_engine(get_settings().database_url))
    print("Migraciones aplicadas:", ", ".join(applied) if applied else "ninguna pendiente")

if __name__ == "__main__":
    main()
