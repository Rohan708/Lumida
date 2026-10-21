"""First-run setup in the browser (0.4.1): Lumida's memory (its own Postgres) and the first
admin, so that ``lumida serve`` is the only command anyone has to type.

- **Setup mode**: ``lumida serve`` with no memory configured serves only the setup pages,
  on 127.0.0.1, until setup is done; then it carries on as the normal service.
- **First admin**: when memory is there but nobody can log in yet (e.g. a fresh Docker
  install), the normal app shows the "create the first admin" step.
- Either way the browser must send a **one-time setup code**, printed in the terminal or
  log, so nobody else on the network can take over a fresh install. Too many wrong codes
  lock setup until Lumida restarts.

Everything stays on this machine: connection details go into the local ``.env``.
"""

import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from lumida.accounts.store import create_user
from lumida.db import create_db_engine
from lumida.graph.models import Role, User
from lumida.ingestion.sources import default_tenant
from lumida.service.migrate import upgrade_to_head
from lumida.service.setup import write_env

log = logging.getLogger("lumida.setup")

MAX_CODE_ATTEMPTS = 20  # wrong setup codes before setup locks until a restart
DOCKER_IMAGE = "postgres:16-alpine"
DOCKER_READY_SECONDS = 90
ENV_FILE = Path(".env")


class SetupError(ValueError):
    """Something the person can fix (shown to them as is)."""


class SetupCodeError(PermissionError):
    pass


@dataclass
class SetupState:
    code: str | None  # None: nothing left to set up
    engine: Engine | None = None  # Lumida's memory, once known
    attempts: int = 0
    admin_ready: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, given: str | None) -> None:
        with self.lock:
            if self.code is None:
                raise SetupError("Lumida is already set up")
            if self.attempts >= MAX_CODE_ATTEMPTS:
                raise SetupCodeError("too many wrong setup codes: restart Lumida to try again")
            if not secrets.compare_digest((given or "").strip(), self.code):
                self.attempts += 1
                raise SetupCodeError("wrong setup code (it's printed where Lumida was started)")

    def needs_admin(self) -> bool:
        if self.engine is None:
            return True
        if not self.admin_ready:
            self.admin_ready = admin_exists(self.engine)
        return not self.admin_ready


def new_setup_code() -> str:
    digits = f"{secrets.randbelow(10**8):08d}"
    return f"{digits[:4]}-{digits[4:]}"


def admin_exists(engine: Engine) -> bool:
    with Session(engine) as session:
        return session.scalar(select(User.id).where(User.role == Role.ADMIN).limit(1)) is not None


def create_first_admin(engine: Engine, *, name: str, email: str, password: str) -> str:
    with Session(engine) as session:
        if session.scalar(select(User.id).where(User.role == Role.ADMIN).limit(1)) is not None:
            raise SetupError("an admin already exists: log in instead")
        user = create_user(session, tenant_id=default_tenant(session).id, email=email, name=name,
                           role=Role.ADMIN, password=password, by="setup")  # fmt: skip
        session.commit()
        return user.email


# --- Lumida's memory: a Postgres you have ------------------------------------------------


@dataclass(frozen=True)
class Probe:
    ok: bool
    message: str
    will_create: bool = False  # the database doesn't exist yet; setup will create it


def postgres_url(host: str, port: int, database: str, user: str, password: str) -> str:
    url = URL.create("postgresql+psycopg", username=user, password=password, host=host,
                     port=port, database=database)  # fmt: skip
    return url.render_as_string(hide_password=False)


def probe(host: str, port: int, database: str, user: str, password: str) -> Probe:
    """Can Lumida use this Postgres? Plain-language answers, never the password."""
    target = postgres_url(host, port, database, user, password)
    try:
        _ping(target)
        return Probe(True, f"Connected to {database} on {host}:{port}.")
    except OperationalError as exc:
        reason = _reason(exc)
        if "does not exist" in reason and "database" in reason:
            try:
                _ping(postgres_url(host, port, "postgres", user, password))
            except SQLAlchemyError as inner:
                return Probe(False, _explain(_reason(inner), host, port))
            return Probe(True, f'Connected. The database "{database}" will be created.', True)
        return Probe(False, _explain(reason, host, port))


def prepare_existing(host: str, port: int, database: str, user: str, password: str) -> str:
    """Create the database if needed, bring it up to date, and return its URL."""
    found = probe(host, port, database, user, password)
    if not found.ok:
        raise SetupError(found.message)
    if found.will_create:
        admin_url = postgres_url(host, port, "postgres", user, password)
        engine = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                quoted = '"' + database.replace('"', '""') + '"'
                conn.execute(text(f"CREATE DATABASE {quoted}"))
        except SQLAlchemyError as exc:
            raise SetupError(f'couldn\'t create the database "{database}": {_reason(exc)}') from exc
        finally:
            engine.dispose()
    url = postgres_url(host, port, database, user, password)
    upgrade_to_head(url)
    return url


def _ping(url: str) -> None:
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def _reason(exc: Exception) -> str:
    original = getattr(exc, "orig", None) or exc
    return str(original).strip().splitlines()[0] if str(original).strip() else type(exc).__name__


def _explain(reason: str, host: str, port: int) -> str:
    lowered = reason.lower()
    if "password authentication failed" in lowered or "authentication failed" in lowered:
        return "The user name or password is wrong."
    if "connection refused" in lowered or "could not connect" in lowered or "timeout" in lowered:
        return f"Can't reach {host}:{port}. Is Postgres running there?"
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return f"Can't find a server called {host}."
    if "permission denied" in lowered:
        return "This user isn't allowed to do that. Use a user who may create databases."
    return f"Postgres said: {reason[:200]}"


# --- Lumida's memory: start a private Postgres with Docker -------------------------------


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        done = subprocess.run(["docker", "info"], capture_output=True, timeout=15, check=False)  # noqa: S607
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def start_docker_postgres() -> str:
    """Start a private Postgres for Lumida (its data in a Docker volume) and return its URL."""
    if not docker_available():
        raise SetupError("Docker isn't running. Start Docker Desktop, or use a Postgres you have.")
    suffix = secrets.token_hex(3)
    name, password = f"lumida-metadata-{suffix}", secrets.token_urlsafe(18)
    command = [
        "docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
        "-e", "POSTGRES_USER=lumida", "-e", f"POSTGRES_PASSWORD={password}",
        "-e", "POSTGRES_DB=lumida", "-p", "127.0.0.1::5432",  # Docker picks a free port
        "-v", f"{name}:/var/lib/postgresql/data", DOCKER_IMAGE,
    ]  # fmt: skip
    started = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)  # noqa: S603
    if started.returncode != 0:
        raise SetupError(f"Docker couldn't start Postgres: {started.stderr.strip()[:200]}")
    port = _published_port(name)
    url = postgres_url("127.0.0.1", port, "lumida", "lumida", password)
    deadline = time.monotonic() + DOCKER_READY_SECONDS
    while True:
        try:
            _ping(url)
            break
        except SQLAlchemyError:
            if time.monotonic() > deadline:
                raise SetupError("Postgres started but didn't become ready in time") from None
            time.sleep(1.5)
    upgrade_to_head(url)
    log.info("started a private Postgres for Lumida: container %s, port %s", name, port)
    return url


def _published_port(container: str) -> int:
    command = ["docker", "port", container, "5432/tcp"]
    shown = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    try:  # e.g. "127.0.0.1:55001"
        return int(shown.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise SetupError("Docker started Postgres but didn't say on which port") from exc


# --- finishing -----------------------------------------------------------------------------


def remember_memory(state: SetupState, url: str, env_file: Path | None = None) -> None:
    """Save the memory's URL to .env (only on this machine) and start using it."""
    write_env(env_file or ENV_FILE, {"LUMIDA_DATABASE_URL": url})
    state.engine = create_db_engine(url)
    state.admin_ready = admin_exists(state.engine)


# --- setup mode (`lumida serve` with no memory yet) ----------------------------------------


def missing_memory(exc: Exception) -> bool:
    """Whether settings failed only because Lumida's memory isn't configured yet."""
    errors: list[dict[str, Any]] = list(getattr(exc, "errors", list)())
    return bool(errors) and all(
        e.get("type") == "missing" and tuple(e.get("loc", ())) == ("database_url",) for e in errors
    )


def setup_port(env_file: Path | None = None) -> int:
    from dotenv import dotenv_values

    env_file = env_file or ENV_FILE
    values = dotenv_values(env_file) if env_file.exists() else {}
    raw = os.environ.get("LUMIDA_API_PORT") or values.get("LUMIDA_API_PORT") or "8000"
    return int(raw)


def run_setup_mode() -> bool:
    """Serve the setup steps on 127.0.0.1 until memory and the first admin exist.
    Returns False if the person stopped it (Ctrl+C) first."""
    from lumida.api.app import api_server, create_setup_app, start_api_thread

    state = SetupState(code=new_setup_code())
    port = setup_port()
    server = api_server(create_setup_app(state), "127.0.0.1", port)
    thread = start_api_thread(server)
    lines = [
        "",
        "Lumida isn't set up yet. Finish in your browser:",
        f"  open   http://127.0.0.1:{port}",
        f"  code   {state.code}",
        "(The code proves it's you. Setup is only reachable from this machine.)",
        "",
    ]
    print("\n".join(lines), flush=True)
    try:
        while not state.finished.wait(0.5):
            pass
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=10)
        return False
    time.sleep(1.0)  # let the browser get its answer
    server.should_exit = True
    thread.join(timeout=10)
    print("Setup done. Starting Lumida...", flush=True)
    return True
