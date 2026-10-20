import argparse
import getpass
import os
import sys
from src.core.config import get_settings
from src.core.db import make_engine, prepare_database
from src.core.users import UserManager

def _password(args) -> str:
    """The password is typed at the keyboard (it is not kept in the shell history). In scripts, PROOF_ADMIN_PASSWORD."""
    env = os.environ.get("PROOF_ADMIN_PASSWORD")
    if env:
        return env
    if not sys.stdin.isatty():
        sys.exit("No interactive terminal: set PROOF_ADMIN_PASSWORD.")
    first = getpass.getpass("Password (at least 12 characters): ")
    if first != getpass.getpass("Repeat the password: "):
        sys.exit("The passwords do not match.")
    return first

def main() -> None:
    parser = argparse.ArgumentParser(description="prima-certified dashboard administrators.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Crea un administrador.")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    setpw = sub.add_parser("set-password", help="Changes an administrator's password.")
    setpw.add_argument("--email", required=True)
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    prepare_database(engine, settings.env)
    users = UserManager(engine)
    if args.command == "create-admin":
        users.create_admin(args.email, args.name, _password(args))
        print(f"Administrador creado: {args.email.lower()}")
    else:
        users.set_password(args.email, _password(args))
        print(f"Contraseña actualizada: {args.email.lower()}")

if __name__ == "__main__":
    main()
