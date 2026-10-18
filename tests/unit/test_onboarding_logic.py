"""Stage 4.5 without a database: which settings are offered as connections (names only,
never Lumida's own store), connection URLs never reach a job's error, setup scripts."""

from pathlib import Path

import pytest

from lumida.ingestion.connections import available_connections, kind_of_url
from lumida.onboarding.jobs import scrub
from lumida.onboarding.setup_scripts import SETUP_SCRIPTS


def test_the_kind_of_database_from_its_url() -> None:
    assert kind_of_url("postgresql+psycopg://u:p@h/db") == "postgres"
    assert kind_of_url("mysql+pymysql://u:p@h/db") == "mysql"
    assert kind_of_url("mssql+pyodbc://u:p@h/db?driver=x") == "sqlserver"
    assert kind_of_url("oracle+oracledb://u:p@h:1521/?service_name=x") == "oracle"
    assert kind_of_url("snowflake://u@acct/db") == "snowflake"
    assert kind_of_url("https://hooks.slack.com/services/x") is None
    assert kind_of_url("not a url") is None


def test_connections_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "SHOP_DB_URL=postgresql+psycopg://lumida:secret@db/shop\n"
        "LUMIDA_DATABASE_URL=postgresql+psycopg://lumida:secret@metadata/lumida\n"
        "SLACK_ALERTS=https://hooks.slack.com/services/x\n"
        "LUMIDA_LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BILLING_URL", "mysql+pymysql://lumida:secret@billing/db")
    found = available_connections(env_file=env)
    assert found["SHOP_DB_URL"] == "postgres"
    assert found["BILLING_URL"] == "mysql"
    assert "LUMIDA_DATABASE_URL" not in found  # Lumida's own store is never offered
    assert "SLACK_ALERTS" not in found
    assert "secret" not in repr(found)  # names and kinds only


def test_errors_never_carry_a_connection_url() -> None:
    message = (
        "OperationalError: could not connect to "
        "postgresql+psycopg://lumida:hunter2@db.internal:5432/shop: timeout"
    )
    cleaned = scrub(message)
    assert "hunter2" not in cleaned
    assert "<connection URL hidden>" in cleaned
    assert cleaned.startswith("OperationalError: could not connect to")
    assert len(scrub("x" * 2000)) == 500


def test_setup_scripts() -> None:
    assert set(SETUP_SCRIPTS) == {"postgres", "mysql", "sqlserver", "oracle", "snowflake"}
    for kind, script in SETUP_SCRIPTS.items():
        assert "lumida" in script.lower(), kind
        assert "<strong password>" in script or kind == "snowflake", kind
        for forbidden in ("GRANT ALL", "INSERT", "UPDATE", "DELETE", "DROP"):
            assert forbidden not in script.upper(), (kind, forbidden)
