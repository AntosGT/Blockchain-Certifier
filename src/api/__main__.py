import uvicorn
from src.api.app import create_app
from src.core.config import get_settings
from src.core.db import make_engine, prepare_database

def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    applied = prepare_database(engine, settings.env)
    if applied:
        print("Migraciones aplicadas:", ", ".join(applied))
    from src.api.services import build_services
    app = create_app(settings, build_services(settings, engine=engine))
    uvicorn.run(app, host=settings.host, port=settings.port)

if __name__ == "__main__":
    main()
