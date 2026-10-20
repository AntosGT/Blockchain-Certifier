import argparse
import logging
import time
from src.api.services import build_services
from src.core.config import get_settings
from src.core.db import make_engine, prepare_database

def main() -> None:
    parser = argparse.ArgumentParser(description="prima-certified worker: registers, checks Bitcoin and verifies.")
    parser.add_argument("--once", action="store_true", help="Runs a single cycle and exits.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log = logging.getLogger("PROOF.Worker")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    prepare_database(engine, settings.env)
    worker = build_services(settings, engine=engine).worker

    while True:
        try:
            log.info("ciclo: %s", worker.run_cycle())
        except Exception:
            # A database error does not bring the worker down: it is logged and retried on the next cycle
            log.exception("The cycle failed")
            if args.once:
                raise
        if args.once:
            return
        time.sleep(settings.worker_interval_seconds)

if __name__ == "__main__":
    main()
