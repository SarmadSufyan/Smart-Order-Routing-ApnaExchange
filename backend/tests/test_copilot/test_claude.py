"""Claude client: schema conversion, request shape, refusals and error mapping — with a stand-in SDK, no network."""

import importlib.util
import json
import unittest
from types import SimpleNamespace

from pydantic import SecretStr

from backend.services.agents.llm import (
    CLAUDE_FALLBACK_BETA,
    ClaudeClient,
    LLMError,
    build_llm_client,
    claude_error_message,
    claude_request,
    claude_response_text,
    claude_schema,
)
from backend.shared.models.copilot import INTENT_SCHEMA

KEY = "sk-test-claude"


def message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="thinking", thinking=""),
                                                             SimpleNamespace(type="text", text=text)])


class FakeSDK:
    """Records the request; answers with a scripted message or raises."""

    def __init__(self, answer) -> None:
        self.answer, self.requests = answer, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class SchemaTest(unittest.TestCase):
    def test_nullable_fields_become_any_of_and_objects_are_closed(self) -> None:
        s = claude_schema(INTENT_SCHEMA)
        self.assertIs(s["additionalProperties"], False)
        self.assertEqual(s["properties"]["side"],
                         {"anyOf": [{"type": "string", "enum": ["BUY", "SELL"]}, {"type": "null"}]})
        self.assertEqual(s["properties"]["quantity"], {"anyOf": [{"type": "number"}, {"type": "null"}]})
        self.assertEqual(s["properties"]["action"]["type"], "string")  # not nullable: unchanged
        self.assertEqual(s["required"], INTENT_SCHEMA["required"])
        self.assertNotIn("anyOf", json.dumps(INTENT_SCHEMA))  # the original (Gemini's) is untouched

    def test_request_shape(self) -> None:
        r = claude_request("RULES", "buy btc", {"type": "object", "properties": {}}, "claude-opus-5-5", "low")
        self.assertEqual(r["model"], "claude-opus-5-5")
        self.assertEqual(r["system"], "RULES")
        self.assertEqual(r["messages"], [{"role": "user", "content": "buy btc"}])  # the trader's text is data
        self.assertEqual(r["output_config"]["effort"], "low")
        self.assertEqual(r["output_config"]["format"]["type"], "json_schema")
        self.assertIs(r["output_config"]["format"]["schema"]["additionalProperties"], False)
        self.assertEqual((r["betas"], r["fallbacks"]), ([CLAUDE_FALLBACK_BETA], "default"))
        self.assertNotIn("thinking", r)  # adaptive by default on this model; disabling it is a 400


class ResponseTest(unittest.TestCase):
    def test_text_is_read_past_thinking_blocks(self) -> None:
        self.assertEqual(claude_response_text(message('{"a": 1}')), '{"a": 1}')

    def test_refusal_and_empty_answers_are_errors(self) -> None:
        with self.assertRaises(LLMError):
            claude_response_text(message("", stop_reason="refusal"))
        with self.assertRaises(LLMError):
            claude_response_text(SimpleNamespace(stop_reason="end_turn", content=[]))

    def test_errors_become_plain_english_without_the_key(self) -> None:
        cases = {"AuthenticationError": "COPILOT_CLAUDE_API_KEY", "RateLimitError": "rate limit",
                 "NotFoundError": "COPILOT_CLAUDE_MODEL", "APITimeoutError": "too long",
                 "APIConnectionError": "internet"}
        for name, expected in cases.items():
            with self.subTest(name=name):
                exc = type(name, (Exception,), {})(f"boom {KEY}")
                text = claude_error_message(exc, "claude-opus-5-5")
                self.assertIn(expected, text)
                self.assertNotIn(KEY, text)


class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_extract_json_goes_through_the_beta_endpoint(self) -> None:
        sdk = FakeSDK(message('{"answer": "hi"}'))
        c = ClaudeClient(SecretStr(KEY), "claude-opus-5-5", "low", client=sdk)
        self.assertEqual(await c.extract_json("RULES", "hello", {"type": "object"}), '{"answer": "hi"}')
        [req] = sdk.requests
        self.assertEqual(req["messages"][0]["content"], "hello")
        self.assertNotIn(KEY, json.dumps(req))  # the key travels in the SDK's header, not in the body

    async def test_sdk_errors_become_llm_errors(self) -> None:
        sdk = FakeSDK(type("RateLimitError", (Exception,), {})("slow down"))
        c = ClaudeClient(SecretStr(KEY), "m", client=sdk)
        with self.assertRaises(LLMError) as err:
            await c.extract_json("RULES", "hi", {})
        self.assertIn("rate limit", err.exception.message)

    def test_key_never_appears_in_the_client_repr(self) -> None:
        self.assertNotIn(KEY, repr(vars(ClaudeClient(SecretStr(KEY), "m"))))


class BuildTest(unittest.TestCase):
    def settings(self, **kw) -> SimpleNamespace:
        base = dict(llm_provider="claude", copilot_claude_api_key=SecretStr(KEY), copilot_claude_model="claude-opus-5-5",
                    copilot_claude_effort="low", llm_timeout_s=30, gemini_api_key=None, gemini_model="g")
        return SimpleNamespace(**{**base, **kw})

    # The lab server installs from requirements.lock, which leaves the SDK out on purpose (imported only when
    # LLM_PROVIDER=claude): there this test has nothing to build.
    @unittest.skipUnless(importlib.util.find_spec("anthropic"), "the anthropic package is not installed")
    def test_claude_needs_its_own_key(self) -> None:
        llm, why = build_llm_client(self.settings())
        self.assertIsInstance(llm, ClaudeClient)
        self.assertEqual((llm.provider, llm.model, why), ("claude", "claude-opus-5-5", None))
        llm, why = build_llm_client(self.settings(copilot_claude_api_key=None))
        self.assertIsNone(llm)
        self.assertIn("COPILOT_CLAUDE_API_KEY", why)


if __name__ == "__main__":
    unittest.main()
