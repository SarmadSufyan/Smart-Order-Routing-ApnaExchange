"""Phone alerts through Discord (Telegram is blocked in Pakistan): the same messages and commands, owner-only."""

import asyncio
import json
import time
import unittest
import unittest.mock

import httpx
import structlog.testing

from backend.services.alerts import DiscordAlerts, discord_command, snowflake_time

TOKEN = "MTIz.discord-SECRET.token"
CHANNEL = "1300000000000000001"
OWNER = "700000000000000001"
DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake(at: float, seq: int = 0) -> str:
    """A Discord message id written at ``at`` (seconds since 1970)."""
    return str(((int(at * 1000) - DISCORD_EPOCH_MS) << 22) + seq)


class FakeDiscord:
    """Discord's REST API, recorded: what was posted, and the channel's messages to read."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.channel: list[dict] = []  # newest first, as Discord lists them

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            self.posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": snowflake(time.time())})
        after = int(request.url.params.get("after", "0"))
        return httpx.Response(200, json=[m for m in self.channel if int(m["id"]) > after])


class PureTest(unittest.TestCase):
    def test_commands_with_a_slash_or_an_exclamation_mark(self) -> None:
        self.assertEqual(discord_command("/stop the market is crazy"), ("stop", "the market is crazy"))
        self.assertEqual(discord_command("!status"), ("status", ""))
        self.assertIsNone(discord_command("stop"))
        self.assertIsNone(discord_command("!sell everything"))

    def test_a_message_id_says_when_it_was_written(self) -> None:
        self.assertAlmostEqual(snowflake_time(snowflake(1_761_400_000.5)), 1_761_400_000.5, places=2)


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.discord = FakeDiscord()
        self.engaged: list[tuple[str, str]] = []

        async def engage(reason: str, operator: str) -> None:
            self.engaged.append((reason, operator))

        async def status() -> str:
            return "REAL MONEY - all quiet"

        self.alerts = DiscordAlerts(TOKEN, CHANNEL, OWNER, real_money=True, engage_kill_switch=engage,
                                    status=status, transport=httpx.MockTransport(self.discord.handler),
                                    send_interval_s=0)

    def message(self, text: str, author: str = OWNER, age_s: float = 0.0, bot: bool = False, seq: int = 0) -> dict:
        return {"id": snowflake(time.time() - age_s, seq), "content": text,
                "author": {"id": author, "username": "someone", **({"bot": True} if bot else {})}}

    async def drain(self) -> None:
        task = asyncio.create_task(self.alerts._send_loop())
        expected = len(self.discord.posted) + self.alerts._queue.qsize()
        for _ in range(500):
            if len(self.discord.posted) >= expected:
                break
            await asyncio.sleep(0.01)
        task.cancel()

    async def test_stop_from_the_owner_engages_the_kill_switch(self) -> None:
        await self.alerts.handle_message(self.message("/stop testing the brakes"))
        self.assertEqual(self.engaged, [("from the phone (Discord): testing the brakes", "discord")])
        await self.drain()
        self.assertIn("ENGAGED", self.discord.posted[0]["content"])
        self.assertEqual(self.discord.posted[0]["allowed_mentions"], {"parse": []})  # it never pings anyone
        self.assertTrue(str(self.discord.requests[0].url).endswith(f"/channels/{CHANNEL}/messages"))

    async def test_others_bots_and_old_messages_are_ignored_silently(self) -> None:
        await self.alerts.handle_message(self.message("/stop", author="800000000000000009"))  # someone else
        await self.alerts.handle_message(self.message("/stop", bot=True))  # a bot, ours included
        await self.alerts.handle_message(self.message("/stop", age_s=3600))  # before this gateway started
        self.assertEqual(self.engaged, [])
        self.assertTrue(self.alerts._queue.empty())  # no answer at all

    async def test_status_with_an_exclamation_mark(self) -> None:
        await self.alerts.handle_message(self.message("!status"))
        await self.drain()
        self.assertEqual([m["content"] for m in self.discord.posted], ["REAL MONEY - all quiet"])

    async def test_the_poll_reads_new_messages_oldest_first_and_only_once(self) -> None:
        self.discord.channel = [self.message("/status", seq=2), self.message("/stop first", seq=1)]  # newest first
        with unittest.mock.patch("asyncio.sleep", fast_sleep):
            task = asyncio.create_task(self.alerts._poll_loop())
            for _ in range(50):
                await _real_sleep(0)
            task.cancel()
        self.assertEqual(self.engaged, [("from the phone (Discord): first", "discord")])  # once, though polled again
        self.assertEqual(self.alerts._after, self.discord.channel[0]["id"])

    async def test_the_token_is_only_in_the_authorization_header(self) -> None:
        self.alerts.notify("hello")
        await self.drain()
        request = self.discord.requests[0]
        self.assertEqual(request.headers["Authorization"], f"Bot {TOKEN}")
        self.assertTrue(str(request.url).startswith("https://discord.com/api/v10/"))
        self.assertNotIn(TOKEN, str(request.url))
        self.assertNotIn(TOKEN, json.dumps(self.discord.posted))

    async def test_a_failed_send_logs_the_error_type_never_the_token(self) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url} with {request.headers}", request=request)

        self.alerts._transport = httpx.MockTransport(down)
        with structlog.testing.capture_logs() as logs, unittest.mock.patch("asyncio.sleep", fast_sleep):
            self.alerts.notify("hello")
            task = asyncio.create_task(self.alerts._send_loop())
            await _real_sleep(0.05)
            task.cancel()
        failures = [e for e in logs if e["event"] == "discord_send_failed"]
        self.assertEqual([e["error"] for e in failures][:1], ["ConnectError"])
        self.assertNotIn(TOKEN, json.dumps(logs, default=str))


_real_sleep = asyncio.sleep


async def fast_sleep(seconds: float) -> None:
    await _real_sleep(0)


if __name__ == "__main__":
    unittest.main()
