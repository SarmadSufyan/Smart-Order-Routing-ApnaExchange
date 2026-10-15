"""LLM clients for the copilot, behind one small interface.

The copilot needs exactly one thing from a model: read a text and answer with a
JSON object that matches a schema (the reading of a request, or the wording of an
answer from facts the code computed). Two providers plug in, chosen in .env with
LLM_PROVIDER: Gemini (free tier, no card; the default) and Claude (Anthropic's
API, paid per use; stronger reasoning). The unit tests use a scripted stub, so
they cost nothing and need no network.

The model never sees exchange keys and can't call anything: its whole output
is one JSON object, which the copilot then re-validates in code.
"""

from typing import Protocol

import httpx
from pydantic import SecretStr

from backend.shared.exceptions import DomainError
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_RETRIES = 1  # extra attempts after a timeout or a 5xx answer


class LLMError(DomainError):
    """The model could not be reached or gave no usable answer."""

    def __init__(self, message: str):
        super().__init__(message, "LLM_UNAVAILABLE")


class _Retryable(Exception):
    """A failure worth one more attempt (the service was slow or briefly down)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


# ── Claude (Anthropic's official SDK, imported only when Claude is chosen) ──
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Set explicitly: the SDK would otherwise follow an ANTHROPIC_BASE_URL inherited from the environment (a developer
# tool on the same machine sets one), sending the product's requests somewhere else.
CLAUDE_API_URL = "https://api.anthropic.com"


def claude_schema(schema):
    """The same schema in the form Claude's structured outputs take.

    Every object gets ``additionalProperties: false``, and a nullable field written as
    ``{"type": [X, "null"]}`` (with ``None`` in an enum) becomes ``anyOf`` of the plain type and null.
    """
    if isinstance(schema, list):
        return [claude_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: claude_schema(v) for k, v in schema.items()}
    kind = out.get("type")
    if isinstance(kind, list) and "null" in kind:
        rest = [k for k in kind if k != "null"]
        inner = {k: v for k, v in out.items() if k != "type"}
        inner["type"] = rest[0] if len(rest) == 1 else rest
        if "enum" in inner:
            inner["enum"] = [e for e in inner["enum"] if e is not None]
        if inner.get("type") == "object":
            inner["additionalProperties"] = False
        return {"anyOf": [inner, {"type": "null"}]}
    if kind == "object":
        out["additionalProperties"] = False
    return out


def claude_request(system: str, user_text: str, schema: dict, model: str, effort: str) -> dict:
    """Arguments for ``client.beta.messages.create``: structured JSON output, refusal fallback on.

    The user's text is its own user message, never part of the instructions. ``fallbacks: "default"``
    lets the API re-run a request a safety classifier declined on Anthropic's recommended model.
    """
    return {
        "model": model,
        "max_tokens": 16_000,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": claude_schema(schema)}},
        "betas": [CLAUDE_FALLBACK_BETA],
        "fallbacks": "default",
    }


def claude_response_text(message) -> str:
    """The JSON text from a Claude response; a refusal or an empty answer is an LLMError."""
    if getattr(message, "stop_reason", None) == "refusal":
        raise LLMError("The AI service declined this request — try rephrasing it")
    text = "".join(getattr(b, "text", "") for b in getattr(message, "content", None) or []
                   if getattr(b, "type", None) == "text")
    if not text.strip():
        raise LLMError("The AI service returned an empty answer")
    return text


def claude_error_message(exc: Exception, model: str) -> str:
    """A plain message for an SDK error, classified by its class name and status (no SDK import needed)."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 403):
        return "The AI service rejected the API key — check COPILOT_CLAUDE_API_KEY in .env"
    if name == "RateLimitError" or status == 429:
        return "The AI service's rate limit was reached — try again in a minute"
    if name == "NotFoundError" or status == 404:
        return f"The AI model '{model}' is not available — check COPILOT_CLAUDE_MODEL in .env"
    if name == "APITimeoutError":
        return "The AI service took too long to answer — try again"
    if name == "APIConnectionError":
        return "Could not reach the AI service — check the internet connection"
    if status:
        return f"The AI service answered HTTP {status}"
    return f"The AI service failed ({name})"


class ClaudeClient:
    provider = "claude"

    def __init__(self, api_key: SecretStr, model: str, effort: str = "low", timeout_s: float = 60.0,
                 client=None) -> None:
        self._key = api_key
        self.model = model
        self._effort = effort
        self._timeout = timeout_s
        self._client = client  # tests inject a stand-in with .beta.messages.create

    def _sdk(self):
        if self._client is None:
            import anthropic  # only machines that use Claude need the package

            self._client = anthropic.AsyncAnthropic(api_key=self._key.get_secret_value(), base_url=CLAUDE_API_URL,
                                                    timeout=self._timeout, max_retries=2)
        return self._client

    async def extract_json(self, system: str, user_text: str, schema: dict) -> str:
        try:
            message = await self._sdk().beta.messages.create(
                **claude_request(system, user_text, schema, self.model, self._effort))
        except Exception as exc:  # noqa: BLE001 — a plain message; the key never appears in it
            raise LLMError(claude_error_message(exc, self.model)) from exc
        return claude_response_text(message)


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: SecretStr, model: str, timeout_s: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self.model = model
        self._timeout = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport

    async def extract_json(self, system: str, user_text: str, schema: dict) -> str:
        # The free tier now and then stalls or answers 5xx for one request (seen live 2026-10-15: one of
        # eight requests timed out, the same text answered in 4 s right after) — one retry hides that.
        for attempt in range(1 + GEMINI_RETRIES):
            try:
                return await self._extract_once(system, user_text, schema)
            except _Retryable as exc:
                if attempt == GEMINI_RETRIES:
                    raise LLMError(exc.message) from exc
                logger.info("gemini_retry", reason=exc.message)
        raise AssertionError("unreachable")

    async def _extract_once(self, system: str, user_text: str, schema: dict) -> str:
        # The key travels in a header, never in the URL, so it can't end up in
        # an exception message or a log line.
        headers = {"x-goog-api-key": self._key.get_secret_value()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.post(GEMINI_URL.format(model=self.model), headers=headers,
                                         json=gemini_request_body(system, user_text, schema))
        except httpx.TimeoutException as exc:
            raise _Retryable("The AI service took too long to answer — try again") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the AI service ({type(exc).__name__})") from exc

        if resp.status_code in (500, 502, 503, 504):
            raise _Retryable(f"The AI service answered HTTP {resp.status_code}")
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
    if settings.llm_provider == "claude":
        key = settings.copilot_claude_api_key
        if not key or not key.get_secret_value().strip():
            return None, "No COPILOT_CLAUDE_API_KEY in .env (create a key at https://console.anthropic.com)"
        try:
            import anthropic  # noqa: F401 — a missing package becomes a clear message, not a crash
        except ImportError:
            return None, "LLM_PROVIDER=claude needs the anthropic package: pip install -r backend/requirements.txt"
        return ClaudeClient(key, settings.copilot_claude_model, settings.copilot_claude_effort,
                            max(settings.llm_timeout_s, 60.0)), None
    return None, f"Unknown LLM_PROVIDER {settings.llm_provider!r}"
