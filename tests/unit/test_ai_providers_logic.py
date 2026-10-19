"""The customer's own AI provider (any OpenAI-compatible service, or Gemini), offline
mode, and the promise behind "your data stays with you": only two places in Lumida's
code can open a connection to the internet, and offline mode switches both off."""

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from lumida.alerting.notifier import ConsoleNotifier, notifier_for
from lumida.config import Settings, get_settings
from lumida.graph.models import DataSource, SourceKind
from lumida.reasoning.llm import (
    GeminiClient,
    LLMNotConfiguredError,
    OpenAICompatibleClient,
    llm_from_settings,
    parse_openai_reply,
    strip_code_fence,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "lumida"
# The only files allowed to open outbound connections, and why (opt-in, off by default).
OUTBOUND_ALLOWED = {
    "alerting/notifier.py": "Slack alerts, when a webhook is configured",
    "reasoning/llm.py": "the customer's own AI provider, when one is configured",
}
OUTBOUND_PATTERN = re.compile(
    r"^\s*(import|from)\s+(urllib\.request|http\.client|socket|requests|httpx|aiohttp|"
    r"smtplib|ftplib|websockets?)\b",
    re.MULTILINE,
)


def _settings(**values: Any) -> Settings:
    # Explicit values win over a developer's own .env (which may turn AI on).
    base = {"database_url": "postgresql://x", "offline": False, "llm_api_key": None,
            "llm_base_url": None, "llm_model": None}  # fmt: skip
    return Settings.model_validate({**base, **values})


def test_only_two_files_can_reach_the_internet() -> None:
    """If this fails, new code opens connections: decide whether that's acceptable for a
    product whose promise is that customer data never leaves, then update the list."""
    found = {
        str(path.relative_to(SRC)).replace("\\", "/")
        for path in SRC.rglob("*.py")
        if OUTBOUND_PATTERN.search(path.read_text(encoding="utf-8"))
    }
    assert found == set(OUTBOUND_ALLOWED)


def test_any_openai_compatible_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> Any:
        sent.update(url=request.full_url, headers=dict(request.header_items()),
                    body=json.loads(request.data))  # fmt: skip
        reply = {"model": "llama3.1", "choices": [{"message": {"content": '{"rules": []}'}}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 3}}  # fmt: skip
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    local = llm_from_settings(_settings(llm_provider="openai", llm_model="llama3.1",
                                        llm_base_url="http://localhost:11434/v1/"))  # fmt: skip
    reply = local.complete_json("You propose rules.", "the schema")
    assert sent["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in sent["headers"]  # a local model needs no key
    assert sent["body"]["model"] == "llama3.1"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert (reply.text, reply.provider, reply.input_tokens) == ('{"rules": []}', "openai", 10)

    hosted = llm_from_settings(_settings(llm_provider="openai", llm_model="some-model",
                                         llm_api_key="sk-test"))  # fmt: skip
    hosted.complete_json("s", "p")
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"  # in a header, never the URL
    assert isinstance(hosted, OpenAICompatibleClient)


def test_what_each_provider_needs() -> None:
    with pytest.raises(LLMNotConfiguredError, match="LUMIDA_LLM_MODEL"):
        llm_from_settings(_settings(llm_provider="openai", llm_api_key="k"))
    with pytest.raises(LLMNotConfiguredError, match="LUMIDA_LLM_API_KEY"):
        llm_from_settings(_settings(llm_provider="openai", llm_model="m"))
    with pytest.raises(LLMNotConfiguredError, match="https"):
        llm_from_settings(_settings(llm_provider="openai", llm_model="m", llm_base_url="ftp://x"))
    assert isinstance(
        llm_from_settings(_settings(llm_provider="gemini", llm_api_key="k")), GeminiClient
    )
    with pytest.raises(LLMNotConfiguredError, match="AI features are off"):
        llm_from_settings(_settings(llm_provider="none"))


def test_offline_mode_switches_everything_off(monkeypatch: pytest.MonkeyPatch) -> None:
    offline = _settings(offline=True, llm_provider="openai", llm_model="m", llm_api_key="k")
    assert offline.ai_enabled is False
    with pytest.raises(LLMNotConfiguredError, match="offline"):
        llm_from_settings(offline)

    source = DataSource(name="shop", kind=SourceKind.POSTGRES, alert_webhook_ref="SLACK_HOOK")
    monkeypatch.setenv("SLACK_HOOK", "https://hooks.slack.com/services/x")
    monkeypatch.setenv("LUMIDA_OFFLINE", "true")
    get_settings.cache_clear()
    try:
        assert isinstance(notifier_for(source), ConsoleNotifier)  # never Slack when offline
    finally:
        get_settings.cache_clear()


def test_replies_are_cleaned_up() -> None:
    fence = "`" * 3
    assert strip_code_fence(f'{fence}json\n{{"a": 1}}\n{fence}') == '{"a": 1}'
    assert strip_code_fence('  {"a": 1} ') == '{"a": 1}'
    with pytest.raises(Exception, match="empty"):
        parse_openai_reply({"choices": [{"message": {"content": " "}}]}, "m")
