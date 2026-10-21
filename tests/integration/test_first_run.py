"""0.4.1 first-run setup against real Postgres: setup mode (memory from an existing
Postgres, created if missing, then the first admin) and the normal app's "first admin"
step for installs whose memory already exists (Docker). The setup code is required,
one-time, and nothing is reachable after setup."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from lumida.api import setup_routes
from lumida.api.app import create_app, create_setup_app
from lumida.db import create_db_engine
from lumida.service import first_run
from lumida.service.first_run import SetupState
from lumida.service.migrate import upgrade_to_head

PASSWORD = "a long test password"


def test_setup_mode_with_an_existing_postgres(
    make_database: Callable[[], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(first_run, "ENV_FILE", tmp_path / ".env")
    for module in (first_run, setup_routes):  # pretend Docker isn't running
        monkeypatch.setattr(module, "docker_available", lambda: False)
    server = make_url(make_database())  # only for its host, port, user and password
    existing = {"host": server.host, "port": server.port, "database": "lumida_fresh",
                "user": server.username, "password": server.password}  # fmt: skip
    state = SetupState(code="1234-5678")
    code = {"X-Lumida-Setup-Code": "1234-5678"}
    with TestClient(create_setup_app(state)) as client:
        status = client.get("/api/setup/status").json()
        assert status == {"ready": False, "needs_memory": True, "needs_admin": True,
                          "setup_mode": True, "docker": False}  # fmt: skip
        assert client.post("/api/setup/memory/test", json=existing).status_code == 403  # no code
        wrong = {**existing, "password": "nope"}
        refused = client.post("/api/setup/memory/test", json=wrong, headers=code).json()
        assert (refused["ok"], refused["message"]) == (False, "The user name or password is wrong.")
        found = client.post("/api/setup/memory/test", json=existing, headers=code).json()
        assert (found["ok"], found["will_create"]) == (True, True)
        docker = client.post("/api/setup/memory", json={"mode": "docker"}, headers=code)
        assert docker.status_code == 409  # Docker isn't running in this test
        assert "Docker isn't running" in docker.json()["detail"]

        chosen = client.post("/api/setup/memory", json={"mode": "existing", "existing": existing},
                             headers=code)  # fmt: skip
        assert chosen.status_code == 200, chosen.text
        assert (chosen.json()["needs_memory"], chosen.json()["needs_admin"]) == (False, True)
        assert "LUMIDA_DATABASE_URL=postgresql+psycopg://" in (tmp_path / ".env").read_text()

        weak = client.post("/api/setup/admin", json={"email": "a@b.test", "password": "short"},
                           headers=code)  # fmt: skip
        assert weak.status_code == 422
        admin = {"name": "Ana", "email": "Ana@Shop.Test", "password": PASSWORD}
        done = client.post("/api/setup/admin", json=admin, headers=code)
        assert done.status_code == 200, done.text
        assert done.json()["ready"] is False  # setup mode still has to hand over to the app
        assert state.finished.is_set()
        again = client.post("/api/setup/admin", json={**admin, "email": "b@shop.test"},
                            headers=code)  # fmt: skip
        assert again.status_code == 409  # one-time: the code no longer works
    assert state.engine is not None
    state.engine.dispose()


def test_first_admin_in_the_normal_app(make_database: Callable[[], str]) -> None:
    url = make_database()
    upgrade_to_head(url)
    engine = create_db_engine(url)
    setup = SetupState(code="2468-1357", engine=engine)
    try:
        with TestClient(create_app(engine, setup=setup)) as client:
            status = client.get("/api/setup/status").json()
            assert (status["ready"], status["needs_admin"], status["setup_mode"]) == (
                False, True, False,
            )  # fmt: skip
            memory = client.post("/api/setup/memory", json={"mode": "docker"},
                                 headers={"X-Lumida-Setup-Code": "2468-1357"})  # fmt: skip
            assert memory.status_code == 409  # memory is already there
            admin = {"email": "first@shop.test", "password": PASSWORD}
            made = client.post("/api/setup/admin", json=admin,
                               headers={"X-Lumida-Setup-Code": "2468-1357"})  # fmt: skip
            assert made.json()["ready"] is True
            login = client.post("/api/v1/auth/login", json=admin)
            assert login.status_code == 200
            assert login.json()["user"]["role"] == "admin"
    finally:
        engine.dispose()


def test_an_installed_app_has_nothing_to_set_up(make_database: Callable[[], str]) -> None:
    url = make_database()
    upgrade_to_head(url)
    engine = create_db_engine(url)
    try:
        first_run.create_first_admin(engine, name="", email="admin@shop.test", password=PASSWORD)
        with TestClient(create_app(engine)) as client:  # no setup code: none was printed
            assert client.get("/api/setup/status").json()["ready"] is True
            refused = client.post("/api/setup/admin",
                                  json={"email": "x@shop.test", "password": PASSWORD},
                                  headers={"X-Lumida-Setup-Code": "0000-0000"})  # fmt: skip
            assert refused.status_code == 409
    finally:
        engine.dispose()
