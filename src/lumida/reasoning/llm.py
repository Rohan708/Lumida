"""LLM providers behind one small interface, chosen by ``LUMIDA_LLM_PROVIDER``. The customer
brings their own provider, model and key; Lumida never supplies or sees one.

- ``openai``: **any OpenAI-compatible chat-completions service**, set by
  ``LUMIDA_LLM_BASE_URL``: hosted ones (OpenAI, OpenRouter, Groq, Mistral, Together, Azure
  OpenAI, ...) or a model on the customer's own machines (Ollama, LM Studio, vLLM,
  llama.cpp), so AI features can run without anything leaving their network.
- ``gemini``: Google Gemini's own API.

Each provider takes a system prompt and a user prompt and returns the model's JSON text.
Both are called with the standard library (no SDK dependency). Keys travel in headers,
never in URLs, so they can't leak into logs. Offline mode (``LUMIDA_OFFLINE``) turns AI
off entirely. Tests use a fake client: no real LLM calls in tests.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from lumida.config import Settings

GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Low temperature: proposals should be the model's best reading of the schema, not creative.
PROPOSAL_TEMPERATURE = 0.2


class LLMError(RuntimeError):
    """The LLM could not be reached or gave no usable answer."""


class LLMNotConfiguredError(LookupError):
    """AI features are off or missing a key (shown to the user as one line)."""


@dataclass(frozen=True)
class LLMReply:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(Protocol):
    provider: str
    model: str

    def complete_json(self, system: str, prompt: str) -> LLMReply: ...


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str = GEMINI_DEFAULT_MODEL, timeout: float = 120.0):
        if not api_key:
            raise LLMNotConfiguredError("LUMIDA_LLM_API_KEY is empty")
        self._key = api_key
        self.model = model
        self._timeout = timeout

    def complete_json(self, system: str, prompt: str) -> LLMReply:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": PROPOSAL_TEMPERATURE,
            },
        }
        request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            GEMINI_ENDPOINT.format(model=self.model),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Gemini returned HTTP {exc.code}: {_error_message(exc)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Gemini could not be reached: {exc}") from exc
        return parse_gemini_reply(payload, self.model)


def parse_gemini_reply(payload: dict[str, Any], model: str) -> LLMReply:
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise LLMError(f"Gemini gave no answer ({reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text", "")) for p in parts)
    if not text.strip():
        reason = candidates[0].get("finishReason", "empty")
        raise LLMError(f"Gemini gave an empty answer ({reason})")
    usage = payload.get("usageMetadata") or {}
    return LLMReply(
        text=text,
        provider="gemini",
        model=str(payload.get("modelVersion") or model),
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
    )


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(exc.read())
        return str(detail.get("error", {}).get("message", exc.reason))[:300]
    except (ValueError, OSError):
        return str(exc.reason)


class OpenAICompatibleClient:
    """Any service that speaks the OpenAI chat-completions API."""

    provider = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float = 120.0):
        if not base_url.startswith(("https://", "http://")):
            raise LLMNotConfiguredError("LUMIDA_LLM_BASE_URL must start with https:// or http://")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.model = model
        self._timeout = timeout

    def complete_json(self, system: str, prompt: str) -> LLMReply:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + " Answer with the JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": PROPOSAL_TEMPERATURE,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        request = urllib.request.Request(  # noqa: S310 - http(s) enforced in __init__
            self._url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(
                f"the AI service returned HTTP {exc.code}: {_error_message(exc)}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"the AI service could not be reached: {exc}") from exc
        return parse_openai_reply(payload, self.model)


def parse_openai_reply(payload: dict[str, Any], model: str) -> LLMReply:
    choices = payload.get("choices") or []
    message = (choices[0] if choices else {}).get("message") or {}
    text = str(message.get("content") or "")
    if not text.strip():
        raise LLMError("the AI service gave an empty answer")
    usage = payload.get("usage") or {}
    return LLMReply(
        text=strip_code_fence(text),
        provider="openai",
        model=str(payload.get("model") or model),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


FENCE = "`" * 3


def strip_code_fence(text: str) -> str:
    """Some models wrap JSON in a Markdown code fence; the parsers want the JSON itself."""
    stripped = text.strip()
    if stripped.startswith(FENCE) and stripped.endswith(FENCE) and len(stripped) > 6:
        stripped = stripped[3:-3]
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def llm_from_settings(settings: Settings) -> LLMClient:
    """The customer's own AI provider, or a one-line reason why AI is off."""
    if settings.offline:
        raise LLMNotConfiguredError("offline mode is on (LUMIDA_OFFLINE): AI features are off")
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    model = settings.llm_model or None
    timeout = settings.llm_timeout_seconds
    if settings.llm_provider == "gemini":
        legacy = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
        if not (key or legacy):
            raise LLMNotConfiguredError(
                "LUMIDA_LLM_PROVIDER is gemini but LUMIDA_LLM_API_KEY is not set (add it to .env)"
            )
        return GeminiClient(key or legacy or "", model or GEMINI_DEFAULT_MODEL, timeout)
    if settings.llm_provider == "openai":
        if not model:
            raise LLMNotConfiguredError(
                "LUMIDA_LLM_PROVIDER is openai: set LUMIDA_LLM_MODEL to the model's name "
                "(as your provider or local server lists it)"
            )
        base = settings.llm_base_url or OPENAI_DEFAULT_BASE_URL
        if base == OPENAI_DEFAULT_BASE_URL and not key:
            raise LLMNotConfiguredError(
                "set LUMIDA_LLM_API_KEY, or LUMIDA_LLM_BASE_URL for a local or other service"
            )
        return OpenAICompatibleClient(base, model, key, timeout)
    raise LLMNotConfiguredError(
        "AI features are off: set LUMIDA_LLM_PROVIDER (openai or gemini), LUMIDA_LLM_MODEL "
        "and LUMIDA_LLM_API_KEY in .env"
    )
