"""``lumida setup``: get a pip-installed Lumida running on this machine in a few questions.

1. Where Lumida keeps what it learns: a Postgres database (any Postgres: an existing
   server with a spare database, or ``docker run postgres``). Checked, then migrated.
2. Writes the answer to ``.env`` in the current folder (other lines are kept).
3. Creates the first admin, unless one exists.
4. Says what to run next: ``lumida serve``, then open the web app.

Nothing leaves the machine: no account, no registration, no telemetry.
``--database-url``, ``--admin-email`` and ``--password-stdin`` make it scriptable.
"""

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lumida.accounts.cli import read_password
from lumida.accounts.store import create_user
from lumida.db import create_db_engine
from lumida.graph.models import Role, User
from lumida.ingestion.sources import default_tenant
from lumida.service.migrate import upgrade_to_head

ENV_FILE = Path(".env")
DEFAULT_URL = "postgresql+psycopg://lumida:<password>@localhost:5432/lumida"
DOCKER_HINT = (
    "No Postgres yet? One command starts one (needs Docker):\n"
    "  docker run -d --name lumida-metadata -e POSTGRES_USER=lumida "
    "-e POSTGRES_PASSWORD=<password> -e POSTGRES_DB=lumida -p 5432:5432 "
    "-v lumida-metadata:/var/lib/postgresql/data postgres:16-alpine"
)


def add_setup_parser(subcommands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = subcommands.add_parser("setup", help="first-time setup on this machine (writes .env)")
    p.add_argument("--database-url", help="Postgres for Lumida's own memory (asked if missing)")
    p.add_argument("--admin-email", help="the first admin's email (asked if missing)")
    p.add_argument(
        "--password-stdin", action="store_true", help="read the admin password from stdin"
    )


def run_setup_command(args: argparse.Namespace) -> int:
    print("Lumida setup. Everything stays on this machine.\n")
    url = args.database_url or _ask_url()
    try:
        revision = _prepare_store(url)
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", exc) or exc).splitlines()[0]
        print(f"error: can't use that database: {reason}", file=sys.stderr)
        print(DOCKER_HINT, file=sys.stderr)
        return 1
    print(f"  ok: database ready (migration {revision})")
    write_env(ENV_FILE, {"LUMIDA_DATABASE_URL": url})
    print(f"  ok: saved to {ENV_FILE.resolve()}")

    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            tenant = default_tenant(session)
            if session.scalar(select(User.id).where(User.role == Role.ADMIN)) is not None:
                print("  ok: an admin already exists")
            else:
                email = args.admin_email or input("Your email (the first admin): ").strip()
                create_user(session, tenant_id=tenant.id, email=email, name="", role=Role.ADMIN,
                            password=read_password(args.password_stdin),
                            by=f"cli:{getpass.getuser()}")  # fmt: skip
                print(f"  ok: admin {email.strip().lower()} created")
            session.commit()
    finally:
        engine.dispose()

    print(
        "\nDone. Next:\n"
        "  lumida serve                 runs Lumida (keep it running; nightly checks happen here)\n"
        "  open http://127.0.0.1:8000   log in, then Databases -> Add a database\n"
        "Database connection URLs go in .env under a name you choose, e.g.\n"
        "  SHOP_DB_URL=postgresql+psycopg://readonly_user:<password>@db-host:5432/shop"
    )
    return 0


def _ask_url() -> str:
    print("Lumida keeps what it learns in a Postgres database (not the one you'll monitor).")
    print(DOCKER_HINT + "\n")
    while True:
        url = input(f"Postgres URL [{DEFAULT_URL}]: ").strip()
        if url and "<password>" not in url:
            return url
        print("  please enter the full URL, with the real password")


def _prepare_store(url: str) -> str:
    engine = create_db_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    return upgrade_to_head(url) or "none"


def write_env(path: Path, values: dict[str, str]) -> None:
    """Set ``values`` in a .env file, keeping every other line (comments included)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name in remaining and not line.lstrip().startswith("#"):
            out.append(f"{name}={remaining.pop(name)}")
        else:
            out.append(line)
    out += [f"{name}={value}" for name, value in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
