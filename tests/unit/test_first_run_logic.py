"""0.4.1 first-run setup without a database: the one-time setup code (and its lockout),
recognising "memory not configured yet", connection URLs, plain-language errors."""

import contextlib

import pytest
from pydantic import ValidationError

from lumida.config import Settings
from lumida.service.first_run import (
    MAX_CODE_ATTEMPTS,
    SetupCodeError,
    SetupError,
    SetupState,
    _explain,
    missing_memory,
    new_setup_code,
    postgres_url,
)


def test_setup_codes() -> None:
    code = new_setup_code()
    assert len(code) == 9
    assert code[4] == "-"
    assert code.replace("-", "").isdigit()
    state = SetupState(code=code)
    state.check(f"  {code} ")  # spaces around it are fine
    with pytest.raises(SetupCodeError, match="wrong setup code"):
        state.check("0000-0000")
    for _ in range(MAX_CODE_ATTEMPTS):
        with contextlib.suppress(SetupCodeError):
            state.check("1111-1111")
    with pytest.raises(SetupCodeError, match="too many"):
        state.check(code)  # locked, even with the right code, until a restart
    with pytest.raises(SetupError, match="already set up"):
        SetupState(code=None).check(code)


def test_memory_not_configured_is_recognised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    for name in ("LUMIDA_DATABASE_URL", "AIDE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]  # no .env here
    with pytest.raises(ValidationError) as missing:
        Settings()
    assert missing_memory(missing.value)
    with pytest.raises(ValidationError) as other:
        Settings.model_validate({"database_url": "x", "api_port": "not a port"})
    assert not missing_memory(other.value)


def test_connection_details() -> None:
    url = postgres_url("db.local", 5432, "lumida", "lumida", "p@ss:w/rd")
    assert url.startswith("postgresql+psycopg://lumida:")
    assert url.endswith("@db.local:5432/lumida")
    assert "p@ss:w/rd" not in url  # escaped, so the URL stays valid


def test_plain_language_errors() -> None:
    wrong = 'password authentication failed for user "lumida"'
    assert _explain(wrong, "h", 1) == "The user name or password is wrong."
    refused = "connection failed: Connection refused"
    assert (
        _explain(refused, "db.local", 5432)
        == "Can't reach db.local:5432. Is Postgres running there?"
    )
    assert "isn't allowed" in _explain("permission denied to create database", "h", 1)
