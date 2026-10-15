"""Gemini client: request shape, key handling and error mapping — against a mock transport, no network."""

import json
import unittest
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from backend.services.agents.llm import (
    GeminiClient,
    LLMError,
    build_llm_client,
    gemini_request_body,
    gemini_response_text,
)

KEY = "test-key-123"


def ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def client(handler) -> GeminiClient:
    return GeminiClient(SecretStr(KEY), "test-model", timeout_s=5, transport=httpx.MockTransport(handler))


class GeminiHelpersTest(unittest.TestCase):
    def test_trader_text_goes_in_its_own_user_message(self) -> None:
        body = gemini_request_body("RULES", "buy $50 of BTC", {"type": "object"})
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "RULES")
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "buy $50 of BTC"}]}])
        cfg = body["generationConfig"]
        self.assertEqual((cfg["temperature"], cfg["responseMimeType"]), (0, "application/json"))
        self.assertEqual(cfg["responseJsonSchema"], {"type": "object"})

    def test_response_text(self) -> None:
        self.assertEqual(gemini_response_text(ok('{"a": 1}')), '{"a": 1}')
        with self.assertRaisesRegex(LLMError, "blocked: SAFETY"):
            gemini_response_text({"promptFeedback": {"blockReason": "SAFETY"}})
        with self.assertRaisesRegex(LLMError, "empty"):
            gemini_response_text(ok("  "))


class GeminiClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_carries_the_key_in_a_header_only(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=ok('{"action": "clarify"}'))

        answer = await client(handler).extract_json("RULES", "hello", {"type": "object"})
        self.assertEqual(answer, '{"action": "clarify"}')
        [req] = seen
        self.assertTrue(req.url.path.endswith("/models/test-model:generateContent"))
        self.assertEqual(req.headers["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, str(req.url))
        self.assertEqual(json.loads(req.content)["contents"][0]["parts"][0]["text"], "hello")

    async def test_http_errors_become_plain_english(self) -> None:
        cases = {429: "free-tier limit", 401: "rejected the API key", 403: "rejected the API key",
                 404: "'test-model' is not available", 500: "HTTP 500"}
        for status, expected in cases.items():
            with self.subTest(status=status):
                c = client(lambda request, s=status: httpx.Response(s, json={"error": {}}))
                with self.assertRaisesRegex(LLMError, expected):
                    await c.extract_json("RULES", "hi", {})

    async def test_timeouts_and_network_errors(self) -> None:
        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        def down(request):
            raise httpx.ConnectError("no route", request=request)

        with self.assertRaisesRegex(LLMError, "too long"):
            await client(timeout).extract_json("RULES", "hi", {})
        with self.assertRaisesRegex(LLMError, "Could not reach"):
            await client(down).extract_json("RULES", "hi", {})

    async def test_one_retry_after_a_timeout_or_a_5xx_but_not_after_a_4xx(self) -> None:
        for first in ("timeout", 503):
            with self.subTest(first=first):
                calls: list[int] = []

                def flaky(request, first=first, calls=calls):
                    calls.append(1)
                    if len(calls) == 1:
                        if first == "timeout":
                            raise httpx.ReadTimeout("slow", request=request)
                        return httpx.Response(first, json={})
                    return httpx.Response(200, json=ok('{"a": 1}'))

                self.assertEqual(await client(flaky).extract_json("RULES", "hi", {}), '{"a": 1}')
                self.assertEqual(len(calls), 2)
        calls = []

        def refused(request):
            calls.append(1)
            return httpx.Response(401, json={})

        with self.assertRaises(LLMError):
            await client(refused).extract_json("RULES", "hi", {})
        self.assertEqual(len(calls), 1)  # a refused key won't change on a second try

    def test_key_never_appears_in_the_client_repr(self) -> None:
        c = GeminiClient(SecretStr(KEY), "m")
        self.assertNotIn(KEY, repr(vars(c)))


class BuildClientTest(unittest.TestCase):
    def settings(self, **kw) -> SimpleNamespace:
        base = dict(llm_provider="gemini", gemini_api_key=SecretStr(KEY), gemini_model="m", llm_timeout_s=5)
        return SimpleNamespace(**{**base, **kw})

    def test_build(self) -> None:
        llm, why = build_llm_client(self.settings())
        self.assertIsInstance(llm, GeminiClient)
        self.assertIsNone(why)
        llm, why = build_llm_client(self.settings(gemini_api_key=None))
        self.assertIsNone(llm)
        self.assertIn("GEMINI_API_KEY", why)
        llm, why = build_llm_client(self.settings(llm_provider="off"))
        self.assertIsNone(llm)
        self.assertIn("turned off", why)


if __name__ == "__main__":
    unittest.main()
