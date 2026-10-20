"""Phone alerts (Telegram): which events become messages, the /stop command, and that nothing leaks the token."""

import asyncio
import json
import time
import unittest
import unittest.mock
from datetime import UTC, datetime

import httpx
import structlog.testing

from backend.services.alerts import TelegramAlerts, format_event, next_summary_at, parse_command

TOKEN = "123456:SECRET-token"
CHAT = "4242"


class FormatTest(unittest.TestCase):
    def test_the_audit_summaries_that_matter_are_forwarded(self) -> None:
        started = {"event_type": "gateway_started", "summary": "Gateway started (mainnet; venues binance)"}
        self.assertEqual(format_event("audit", started, True), "🟢 Gateway started (mainnet; venues binance)")
        self.assertIsNone(format_event("audit", {"event_type": "login", "summary": "admin logged in"}, True))

    def test_kill_switch_and_loss_limit(self) -> None:
        engaged = format_event("kill_switch", {"active": True, "operator": "risk-monitor", "reason": "loss limit"}, False)
        self.assertEqual(engaged, "🛑 Kill switch ENGAGED by risk-monitor: loss limit")
        self.assertIn("released by admin", format_event("kill_switch", {"active": False, "operator": "admin"}, False))
        self.assertIsNone(format_event("kill_switch", {"active": True, "venue_orders_swept": 0}, False))  # clean-up
        self.assertTrue(format_event("risk_alert", {"type": "LOSS_LIMIT", "message": "-$11"}, False).startswith("🛑"))

    def test_fills_and_refusals_only_on_real_money(self) -> None:
        fill = {"order_id": "abcdef12-0000", "new_status": "FILLED", "symbol": "BTC/USDT"}
        self.assertIsNone(format_event("order_update", fill, real_money=False))  # the lab's bots: too many
        self.assertEqual(format_event("order_update", fill, True), "💰 Real order filled: BTC/USDT (order abcdef12)")
        refused = {"exec_type": "REJECTED", "venue_id": "V4", "symbol": "ETH/USDT", "rejection_reason": "band"}
        self.assertIn("V4 refused part of a real order", format_event("execution_report", refused, True))
        self.assertIsNone(format_event("order_update", {**fill, "new_status": "NEW"}, True))

    def test_commands(self) -> None:
        self.assertEqual(parse_command("/stop market is wild"), ("stop", "market is wild"))
        self.assertEqual(parse_command("/STOP@ApnaSorBot"), ("stop", ""))
        self.assertEqual(parse_command("/status"), ("status", ""))
        self.assertIsNone(parse_command("stop"))
        self.assertIsNone(parse_command("/release"))  # no such command: releasing needs the screen

    def test_next_summary(self) -> None:
        self.assertEqual(next_summary_at(datetime(2026, 10, 20, 2, 0, tzinfo=UTC), 3),
                         datetime(2026, 10, 20, 3, 0, tzinfo=UTC))
        self.assertEqual(next_summary_at(datetime(2026, 10, 20, 3, 0, tzinfo=UTC), 3),
                         datetime(2026, 10, 21, 3, 0, tzinfo=UTC))


class FakeTelegram:
    """Telegram's bot API, recorded."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "sendMessage":
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(200, json={"ok": True, "result": []})


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.telegram = FakeTelegram()
        self.engaged: list[tuple[str, str]] = []

        async def engage(reason: str, operator: str) -> None:
            self.engaged.append((reason, operator))

        async def status() -> str:
            return "REAL MONEY - all quiet"

        self.alerts = TelegramAlerts(TOKEN, CHAT, real_money=True, engage_kill_switch=engage, status=status,
                                     transport=httpx.MockTransport(self.telegram.handler), send_interval_s=0)

    def update(self, text: str, chat: str = CHAT, age_s: float = 0.0) -> dict:
        return {"update_id": 1, "message": {"chat": {"id": int(chat)}, "date": int(time.time() - age_s), "text": text}}

    async def drain(self) -> None:
        """Run the sender until everything queued has gone out - no race with a wall clock (the gap is 0 here)."""
        task = asyncio.create_task(self.alerts._send_loop())
        expected = len(self.telegram.sent) + self.alerts._queue.qsize()
        for _ in range(500):
            if len(self.telegram.sent) >= expected:
                break
            await asyncio.sleep(0.01)
        task.cancel()

    async def test_stop_from_the_owners_chat_engages_the_kill_switch(self) -> None:
        await self.alerts.handle_update(self.update("/stop testing the brakes"))
        self.assertEqual(self.engaged, [("from the phone (Telegram): testing the brakes", "telegram")])
        await self.drain()
        self.assertIn("ENGAGED", self.telegram.sent[0]["text"])
        self.assertEqual(self.telegram.sent[0]["chat_id"], CHAT)

    async def test_strangers_and_old_messages_are_ignored_silently(self) -> None:
        await self.alerts.handle_update(self.update("/stop", chat="999"))
        await self.alerts.handle_update(self.update("/stop", age_s=3600))  # sent before this gateway started
        self.assertEqual(self.engaged, [])
        self.assertTrue(self.alerts._queue.empty())  # no answer at all

    async def test_status_and_the_same_warning_not_twice_an_hour(self) -> None:
        await self.alerts.handle_update(self.update("/status"))
        self.alerts.notify("⚠️ Concentration: BTC 70%", key="CONCENTRATION")
        self.alerts.notify("⚠️ Concentration: BTC 70%", key="CONCENTRATION")
        await self.drain()
        self.assertEqual([m["text"] for m in self.telegram.sent], ["REAL MONEY - all quiet", "⚠️ Concentration: BTC 70%"])

    async def test_the_token_is_only_in_telegrams_own_url(self) -> None:
        self.alerts.notify("hello")
        await self.drain()
        self.assertTrue(all(u.startswith("https://api.telegram.org/bot") for u in self.telegram.urls))
        self.assertNotIn(TOKEN, json.dumps(self.telegram.sent))  # never in a message body

    async def test_a_failed_send_logs_the_error_type_never_the_url(self) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        self.alerts._transport = httpx.MockTransport(down)
        with structlog.testing.capture_logs() as logs, unittest.mock.patch("asyncio.sleep", fast_sleep):
            self.alerts.notify("hello")
            task = asyncio.create_task(self.alerts._send_loop())
            await _real_sleep(0.05)
            task.cancel()
        failures = [e for e in logs if e["event"] == "telegram_send_failed"]
        self.assertEqual([e["error"] for e in failures][:1], ["ConnectError"])
        self.assertNotIn(TOKEN, json.dumps(logs, default=str))


_real_sleep = asyncio.sleep


async def fast_sleep(seconds: float) -> None:
    await _real_sleep(0)


if __name__ == "__main__":
    unittest.main()
