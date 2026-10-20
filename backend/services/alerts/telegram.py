"""Alerts on the owner's phone (Telegram), and a stop button (Phase D plan, D1-6; decision A4).

What it sends: gateway started / stopped, the kill switch engaged or released, the trading-loss limit, a refused
kill-switch release, a venue blacklisted, an exchange that refused part of an order, and - on real money only -
every order that filled (the lab server's bots trade too often for that). Plus a daily summary. The words are the
audit log's own summaries: one description of each event, wherever it is read.

What it obeys: ``/stop`` engages the kill switch, ``/status`` answers with the summary - and only from the one chat
in ``TELEGRAM_CHAT_ID``. Releasing the kill switch is NOT possible from the phone: stopping is always safe,
restarting needs the screen (the same asymmetry as RAFT's tighten-at-once / loosen-with-a-majority rule). Commands
sent before the gateway started are ignored, so an old ``/stop`` never fires at a restart.

The bot token sits in the URL of every Telegram call (that is Telegram's API), so no URL and no raw error text is
ever logged - only the error's type.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from backend.shared.events.event_bus import event_bus
from backend.shared.utils.logger import get_logger

logger = get_logger("alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs request URLs at INFO: the token is in them

API = "https://api.telegram.org/bot{token}/{method}"
FORWARDED_AUDIT = {
    "gateway_started": "🟢",
    "gateway_stopped": "⚪",
    "kill_switch_release_refused": "⛔",
    "venue_blacklisted": "🚫",
    "consensus_unreachable": "⚠️",
    "bots_auto_resumed": "🤖",
}
REPEAT_AFTER_S = {"CONCENTRATION": 3600.0}  # the same concentration warning at most once an hour
MAX_LEN = 3500  # Telegram's limit is 4096 characters


def format_event(channel: str, data: dict, real_money: bool) -> str | None:
    """The phone message for one internal event, or None when it isn't worth a notification. Pure."""
    if channel == "audit":
        icon = FORWARDED_AUDIT.get(data.get("event_type", ""))
        return f"{icon} {data['summary']}" if icon and data.get("summary") else None
    if channel == "kill_switch":
        if "venue_orders_swept" in data:
            return None  # the clean-up after an engage: the engage itself was already sent
        operator = data.get("operator") or "someone"
        if data.get("active"):
            return f"🛑 Kill switch ENGAGED by {operator}: {data.get('reason') or 'no reason given'}"
        return f"✅ Kill switch released by {operator}"
    if channel == "risk_alert":
        kind = data.get("type", "")
        icon = "🛑" if kind == "LOSS_LIMIT" else "⚠️"
        return f"{icon} {kind.replace('_', ' ').title()}: {data.get('message', '')}"
    if not real_money:
        return None  # fills and refusals: real money only
    if channel == "order_update" and data.get("new_status") in ("FILLED", "PARTIALLY_FILLED"):
        status = data["new_status"].replace("_", " ").lower()
        return f"💰 Real order {status}: {data.get('symbol', '')} (order {str(data.get('order_id', ''))[:8]})"
    if channel == "execution_report" and data.get("exec_type") == "REJECTED":
        return (f"❗ {data.get('venue_id', 'An exchange')} refused part of a real order on {data.get('symbol', '')}: "
                f"{data.get('rejection_reason') or 'no reason given'}")
    return None


def parse_command(text: str) -> tuple[str, str] | None:
    """``"/stop the market is crazy"`` -> ``("stop", "the market is crazy")``. Pure; None for anything else."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    command = head[1:].split("@", 1)[0].lower()  # "/stop@ApnaSorBot" in a group
    return (command, rest.strip()) if command in ("stop", "status", "help") else None


def next_summary_at(now: datetime, hour_utc: int) -> datetime:
    """The next daily-summary time after ``now``. Pure."""
    at = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


class TelegramAlerts:
    """Sends alerts to one Telegram chat and takes ``/stop`` / ``/status`` from it."""

    channels = ("audit", "kill_switch", "risk_alert", "order_update", "execution_report")

    def __init__(self, token: str, chat_id: str, *, real_money: bool,
                 engage_kill_switch: Callable[[str, str], Awaitable[Any]],
                 status: Callable[[], Awaitable[str]], summary_hour_utc: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None, poll_timeout_s: int = 25) -> None:
        self._token = token
        self._chat = str(chat_id).strip()
        self._real_money = real_money
        self._engage = engage_kill_switch
        self._status = status
        self._summary_hour = summary_hour_utc
        self._transport = transport
        self._poll_timeout = poll_timeout_s
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._tasks: list[asyncio.Task] = []
        self._last_sent: dict[str, float] = {}
        self._started_at = time.time()
        self._offset: int | None = None
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
        on_event.__name__ = f"telegram_{channel}"
        return on_event

    async def start(self) -> None:
        self._started_at = time.time()
        self._tasks = [asyncio.create_task(self._send_loop(), name="telegram-send"),
                       asyncio.create_task(self._poll_loop(), name="telegram-poll"),
                       asyncio.create_task(self._summary_loop(), name="telegram-summary")]
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
            logger.warning("telegram_queue_full")  # the phone is far behind: drop rather than block the gateway

    async def _call(self, method: str, payload: dict, timeout: float = 20.0) -> Any:
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            resp = await client.post(API.format(token=self._token, method=method), json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} answered {resp.status_code}: {str(data.get('description'))[:120]}")
        return data.get("result")

    async def _send_loop(self) -> None:
        while True:
            text = await self._queue.get()
            for attempt in range(3):
                try:
                    await self._call("sendMessage", {"chat_id": self._chat, "text": text,
                                                     "disable_web_page_preview": True})
                    break
                except Exception as exc:  # noqa: BLE001 — never the URL: it holds the token
                    logger.warning("telegram_send_failed", attempt=attempt + 1, error=type(exc).__name__)
                    await asyncio.sleep(2 * (attempt + 1))
            await asyncio.sleep(1.1)  # well under Telegram's rate limits

    # ── commands from the phone ─────────────────────────────────────
    async def _poll_loop(self) -> None:
        while True:
            try:
                updates = await self._call("getUpdates", {"timeout": self._poll_timeout,
                                                          **({"offset": self._offset} if self._offset else {})},
                                           timeout=self._poll_timeout + 10)
                for update in updates or []:
                    self._offset = update["update_id"] + 1
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_poll_failed", error=type(exc).__name__)
                await asyncio.sleep(10)

    async def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        if message.get("date", 0) < self._started_at - 5:
            return  # sent before this gateway started: never act on an old /stop
        command = parse_command(message.get("text", ""))
        if command is None:
            return
        if chat != self._chat:
            if chat not in self._strangers:
                self._strangers.add(chat)
                logger.warning("telegram_command_from_unknown_chat", chat=chat)
            return  # no answer: a stranger learns nothing
        name, rest = command
        if name == "stop":
            reason = f"from the phone (Telegram){': ' + rest if rest else ''}"
            try:
                await self._engage(reason, "telegram")
                self.notify("🛑 Done: the kill switch is ENGAGED - nothing trades. Release it from the screen "
                            "(Kill Switch page) when you have looked.")
            except Exception as exc:  # noqa: BLE001
                logger.error("telegram_stop_failed", error=type(exc).__name__)
                self.notify(f"❗ The kill switch could NOT be engaged ({type(exc).__name__}) - use the screen now.")
        elif name == "status":
            self.notify(await self._status())
        else:
            self.notify("/stop [reason] - engage the kill switch (stops all trading)\n/status - the summary\n"
                        "Releasing the kill switch is done on the screen only.")

    # ── daily summary ───────────────────────────────────────────────
    async def _summary_loop(self) -> None:
        while True:
            now = datetime.now(UTC)
            await asyncio.sleep((next_summary_at(now, self._summary_hour) - now).total_seconds())
            try:
                self.notify("📋 Daily summary\n" + await self._status())
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_summary_failed", error=type(exc).__name__)
