"""LLM clients for the copilot, behind one small interface.

The copilot needs exactly one thing from a model: turn a sentence into a JSON
object that matches a schema. Anything that can do that plugs in here —
Gemini today (free tier, no card), an OpenAI-compatible server such as GitHub
Models or a local Ollama later — and the unit tests use a scripted stub, so
they cost nothing and need no network.

The model never sees exchange keys and can't call anything: its whole output
is one JSON object, which the copilot then re-validates in code.
"""

from typing import Protocol

import httpx
from pydantic import SecretStr

from backend.shared.exceptions import DomainError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(DomainError):
    """The model could not be reached or gave no usable answer."""

    def __init__(self, message: str):
        super().__init__(message, "LLM_UNAVAILABLE")


class LLMClient(Protocol):
    provider: str
    model: str

    async def extract_json(self, system: str, user_text: str, schema: dict) -> str:
        """Return the model's answer: a JSON object (as text) matching ``schema``."""
        ...


# ── Gemini (pure request/response helpers are unit-tested) ─────────
def gemini_request_body(system: str, user_text: str, schema: dict) -> dict:
    """Body for ``generateContent`` with structured (JSON) output.

    The trader's text goes in its own user message, never into the
    instructions, so to the model it is data to read, not rules to follow.
    """
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0,  # the same sentence should give the same reading
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }


def gemini_response_text(data: dict) -> str:
    """The answer text from a ``generateContent`` response."""
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        raise LLMError("The AI service returned no answer" + (f" (blocked: {block})" if block else ""))
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise LLMError("The AI service returned an empty answer")
    return text


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: SecretStr, model: str, timeout_s: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self.model = model
        self._timeout = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport

    async def extract_json(self, system: str, user_text: str, schema: dict) -> str:
        # The key travels in a header, never in the URL, so it can't end up in
        # an exception message or a log line.
        headers = {"x-goog-api-key": self._key.get_secret_value()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.post(GEMINI_URL.format(model=self.model), headers=headers,
                                         json=gemini_request_body(system, user_text, schema))
        except httpx.TimeoutException as exc:
            raise LLMError("The AI service took too long to answer — try again") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the AI service ({type(exc).__name__})") from exc

        if resp.status_code == 429:
            raise LLMError("The AI service's free-tier limit was reached — try again in a minute")
        if resp.status_code in (401, 403):
            raise LLMError("The AI service rejected the API key — check GEMINI_API_KEY in .env")
        if resp.status_code == 404:
            raise LLMError(f"The AI model '{self.model}' is not available — check GEMINI_MODEL in .env")
        if resp.status_code >= 400:
            raise LLMError(f"The AI service answered HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError("The AI service sent an unreadable response") from exc
        return gemini_response_text(data)


def build_llm_client(settings) -> tuple[LLMClient | None, str | None]:
    """The configured client, or ``(None, why)`` when the copilot is off."""
    if settings.llm_provider == "off":
        return None, "The copilot is turned off (LLM_PROVIDER=off)"
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
            return None, "No GEMINI_API_KEY in .env (free key: https://aistudio.google.com/apikey)"
        return GeminiClient(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_s), None
    return None, f"Unknown LLM_PROVIDER {settings.llm_provider!r}"
