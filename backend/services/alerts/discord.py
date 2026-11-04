"""Alerts on the owner's phone through Discord, and a stop button - the same alerts as the Telegram bot.

Telegram is blocked in Pakistan (2026-10-25: api.telegram.org does not answer from the team's networks), Discord is
not. The words, the events and the commands are Telegram's (``format_event`` / ``parse_command``): only the
transport differs.

How: the bot posts to one channel of the owner's private Discord server (``DISCORD_CHANNEL_ID``) with the Discord
REST API, and reads that channel's new messages every few seconds for ``/stop`` and ``/status`` - nothing is opened
on our server; every call goes out. Commands count only from the owner's Discord account (``DISCORD_OWNER_ID``,
found by ``deploy.py discord``), never from anyone else in the server, and only if written after this gateway
started. As with Telegram, ``/stop`` engages the kill switch and nothing here can release it.

The bot token travels in the Authorization header of every call: no header and no raw error text is ever logged.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.shared.events.event_bus import event_bus
from backend.shared.utils.logger import get_logger

from .telegram import REPEAT_AFTER_S, block_from_phone, format_event, next_summary_at, parse_command

logger = get_logger("alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://discord.com/api/v10"
MAX_LEN = 1900  # Discord's limit is 2000 characters


def discord_command(text: str) -> tuple[str, str] | None:
    """``"/stop why"`` or ``"!stop why"`` -> ``("stop", "why")``. Pure.

    Discord's own slash-command menu can swallow a typed ``/``, so ``!stop`` works too.
    """
    text = (text or "").strip()
    if text.startswith("!"):
        text = "/" + text[1:]
    return parse_command(text)


def snowflake_time(message_id: str) -> float:
    """When a Discord message was written (seconds since 1970), read from its id. Pure."""
    return ((int(message_id) >> 22) + 1_420_070_400_000) / 1000


class DiscordAlerts:
    """Sends alerts to one Discord channel and takes ``/stop`` / ``/status`` from its owner."""

    channels = ("audit", "kill_switch", "risk_alert", "order_update", "execution_report")

    def __init__(self, token: str, channel_id: str, owner_id: str, *, real_money: bool,
                 engage_kill_switch: Callable[[str, str], Awaitable[Any]],
                 status: Callable[[], Awaitable[str]], summary_hour_utc: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None, poll_every_s: float = 3.0,
                 send_interval_s: float = 1.1,
                 block_venue: Callable[[str, str], Awaitable[str]] | None = None) -> None:
        self._token = token
        self._channel = str(channel_id).strip()
        self._owner = str(owner_id).strip()
        self._real_money = real_money
        self._engage = engage_kill_switch
        self._block = block_venue
        self._status = status
        self._summary_hour = summary_hour_utc
        self._transport = transport
        self._poll_every = poll_every_s
        self._send_interval = send_interval_s  # Discord allows about 5 messages per 5 s in a channel
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._tasks: list[asyncio.Task] = []
        self._last_sent: dict[str, float] = {}
        self._started_at = time.time()
        self._after: str | None = None  # the newest message already read
        self._strangers: set[str] = set()
        self._handlers: list[tuple[str, Callable]] = []

    # ── lifecycle ───────────────────────────────────────────────────
    def subscribe(self) -> None:
        """Listen to the events (before the gateway records "started", so that one is sent too)."""
        self._handlers = [(channel, self._handler(channel)) for channel in self.channels]
        for channel, handler in self._handlers:
            event_bus.subscribe(channel, handler)

    def _handler(self, channel: str):
        async def on_event(data: dict) -> None:
            self.notify(format_event(channel, data, self._real_money), key=data.get("type") or channel)
        on_event.__name__ = f"discord_{channel}"
        return on_event

    async def start(self) -> None:
        self._started_at = time.time()
        self._tasks = [asyncio.create_task(self._send_loop(), name="discord-send"),
                       asyncio.create_task(self._poll_loop(), name="discord-poll"),
                       asyncio.create_task(self._summary_loop(), name="discord-summary")]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        """Send what is queued (up to 5 s - the "gateway stopped" message), then stop."""
        deadline = time.monotonic() + 5
        while not self._queue.empty() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        for channel, handler in self._handlers:
            event_bus.unsubscribe(channel, handler)
        for task in self._tasks:
            task.cancel()

    # ── sending ─────────────────────────────────────────────────────
    def notify(self, text: str | None, key: str = "") -> None:
        if not text:
            return
        wait = REPEAT_AFTER_S.get(key)
        if wait is not None:
            if time.monotonic() - self._last_sent.get(text, -1e9) < wait:
                return
            self._last_sent[text] = time.monotonic()
        try:
            self._queue.put_nowait(text[:MAX_LEN])
        except asyncio.QueueFull:
            logger.warning("discord_queue_full")  # far behind: drop rather than block the gateway

    async def _call(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
            resp = await client.request(method, f"{API}{path}", json=payload, params=params,
                                        headers={"Authorization": f"Bot {self._token}"})
        if resp.status_code == 429:  # rate limited: Discord says how long to wait
            retry = float((resp.json() or {}).get("retry_after", 1.0))
            await asyncio.sleep(min(retry, 30.0))
            raise RuntimeError("rate limited")
        if resp.status_code >= 400:
            raise RuntimeError(f"Discord {method} {path.split('/')[1]} answered {resp.status_code}")
        return resp.json() if resp.content else None

    async def _send_loop(self) -> None:
        while True:
            text = await self._queue.get()
            for attempt in range(3):
                try:
                    await self._call("POST", f"/channels/{self._channel}/messages",
                                     {"content": text, "allowed_mentions": {"parse": []}})
                    break
                except Exception as exc:  # noqa: BLE001 — never the headers: they hold the token
                    logger.warning("discord_send_failed", attempt=attempt + 1, error=type(exc).__name__)
                    await asyncio.sleep(2 * (attempt + 1))
            await asyncio.sleep(self._send_interval)

    # ── commands from the phone ─────────────────────────────────────
    async def _poll_loop(self) -> None:
        while True:
            try:
                params = {"limit": 50, **({"after": self._after} if self._after else {})}
                messages = await self._call("GET", f"/channels/{self._channel}/messages", params=params) or []
                for message in sorted(messages, key=lambda m: int(m["id"])):  # Discord answers newest first
                    self._after = message["id"]
                    await self.handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("discord_poll_failed", error=type(exc).__name__)
                await asyncio.sleep(10)
            await asyncio.sleep(self._poll_every)

    async def handle_message(self, message: dict) -> None:
        author = message.get("author") or {}
        if author.get("bot"):
            return  # our own alerts (and any other bot)
        if snowflake_time(message.get("id", "0")) < self._started_at - 5:
            return  # written before this gateway started: never act on an old /stop
        command = discord_command(message.get("content", ""))
        if command is None:
            return
        if str(author.get("id", "")) != self._owner:
            who = str(author.get("id", ""))
            if who not in self._strangers:
                self._strangers.add(who)
                logger.warning("discord_command_from_someone_else", user=who)
            return  # no answer: only the owner commands it
        name, rest = command
        if name == "stop":
            reason = f"from the phone (Discord){': ' + rest if rest else ''}"
            try:
                await self._engage(reason, "discord")
                self.notify("🛑 Done: the kill switch is ENGAGED - nothing trades. Release it from the screen "
                            "(Kill Switch page) when you have looked.")
            except Exception as exc:  # noqa: BLE001
                logger.error("discord_stop_failed", error=type(exc).__name__)
                self.notify(f"❗ The kill switch could NOT be engaged ({type(exc).__name__}) - use the screen now.")
        elif name == "block":
            self.notify(await block_from_phone(self._block, rest, "discord"))
        elif name == "status":
            self.notify(await self._status())
        else:
            self.notify("/stop [reason] - engage the kill switch (stops all trading)\n"
                        "/block binance - no new orders to that exchange\n/status - the summary\n"
                        "(!stop, !block and !status work too.) Releasing and lifting are done on the screen only.")

    # ── daily summary ───────────────────────────────────────────────
    async def _summary_loop(self) -> None:
        while True:
            now = datetime.now(UTC)
            await asyncio.sleep((next_summary_at(now, self._summary_hour) - now).total_seconds())
            try:
                self.notify("📋 Daily summary\n" + await self._status())
            except Exception as exc:  # noqa: BLE001
                logger.warning("discord_summary_failed", error=type(exc).__name__)
