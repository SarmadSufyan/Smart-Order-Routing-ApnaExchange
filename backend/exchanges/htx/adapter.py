"""HTX (formerly Huobi) Spot adapter - real money only: HTX has no sandbox.

Market data: ``market.<symbol>.mbp.refresh.20`` (the top 20 levels, a WHOLE snapshot in every message, about one per
second per coin) and ``market.<symbol>.ticker`` on the public WebSocket. Every frame is gzip-compressed; the server
sends ``{"ping": n}`` and expects ``{"pong": n}`` back. Because each book message replaces the book, there is no
sequence chain that can break: a message older than the book we hold (``seqNum``) is simply ignored.

Execution: ccxt ``htx``. Our IOC LIMIT children become ``buy-ioc`` / ``sell-ioc``; our client id travels as
``client-order-id`` and finds the order again after a network error.

HTX has no test environment, so with ``EXCHANGE_MODE=testnet`` it is a DATA-ONLY venue (like Coinbase): its live
book counts in the market view and the NBBO, and the router never sends it an order. On real money it trades like
the others, once HTX itself confirms the key is trade-only and IP-locked (``key_permissions.parse_htx``).

Chosen 2026-10-24 (Phase D, decision A2 widened): HTX holds a PVARA No-Objection Certificate in Pakistan and
answers from the real-money server's address in Zurich; its public stream verified live the same day.
"""

import gzip
import json
from typing import Any

from websockets.asyncio.client import ClientConnection

from backend.exchanges.base import TickerStats
from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.shared.utils.logger import get_logger

logger = get_logger("htx_adapter")

WS_URL = "wss://api.huobi.pro/ws"
BOOK_CHANNEL = "mbp.refresh.20"


def decode_frame(raw: str | bytes) -> dict | None:
    """One WebSocket frame -> its JSON (HTX gzips every frame). None for anything unreadable."""
    try:
        text = gzip.decompress(raw) if isinstance(raw, bytes) else raw
        msg = json.loads(text)
    except (OSError, EOFError, ValueError):
        return None
    return msg if isinstance(msg, dict) else None


def _levels(raw: Any) -> list[tuple[float, float]]:
    return [(float(level[0]), float(level[1])) for level in raw or []]


class HtxAdapter(CcxtVenueAdapter):
    exchange_id = "htx"
    ccxt_id = "htx"
    ccxt_options = {"defaultType": "spot"}
    test_environment_name = "none"  # HTX has no sandbox: data-only outside real money

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_mode = "mainnet"  # the only market there is
        self._ws: ClientConnection | None = None

    # ── no sandbox: data-only on testnet ────────────────────────────
    @property
    def execution_available(self) -> bool:
        return self.mode == "mainnet"

    @property
    def execution_note(self) -> str | None:
        if not self.execution_available:
            return f"{self.name} has no sandbox - it trades on real money only (its live order book still counts here)"
        return super().execution_note

    @property
    def has_credentials(self) -> bool:
        return self.mode == "mainnet" and bool(self._credentials)

    def _make_client(self, *, sandbox: bool, with_keys: bool) -> Any:
        # There is no HTX sandbox to switch to: outside real money the client is the real market's, without keys.
        return super()._make_client(sandbox=False, with_keys=with_keys and self.mode == "mainnet")

    # ── market data ─────────────────────────────────────────────────
    async def _run_feed(self) -> None:
        await self._ws_loop(WS_URL, self._on_open, self._on_message)

    async def _on_open(self, ws: ClientConnection) -> None:
        self._ws = ws
        for i, rules in enumerate(self._rules.values()):
            await ws.send(json.dumps({"sub": f"market.{rules.venue_symbol}.{BOOK_CHANNEL}", "id": f"b{i}"}))
            await ws.send(json.dumps({"sub": f"market.{rules.venue_symbol}.ticker", "id": f"t{i}"}))

    @staticmethod
    async def _pong(ws: ClientConnection, ping: Any) -> None:
        try:
            await ws.send(json.dumps({"pong": ping}))
        except Exception:  # noqa: BLE001 — a closed connection is rebuilt by the feed loop
            pass

    def _on_message(self, raw: str | bytes) -> bool:
        msg = decode_frame(raw)
        if msg is None:
            return True
        if "ping" in msg:  # HTX closes a connection that misses two pings
            if self._ws is not None:
                self._spawn(self._pong(self._ws, msg["ping"]), f"{self.venue_id}-pong")
            return True
        if msg.get("status") == "error":
            logger.warning("ws_request_failed", venue_id=self.venue_id,
                           code=msg.get("err-code"), message=str(msg.get("err-msg"))[:200])
            return True
        channel, tick = msg.get("ch"), msg.get("tick")
        if not isinstance(channel, str) or not isinstance(tick, dict):
            return True
        parts = channel.split(".")  # market.btcusdt.mbp.refresh.20 / market.btcusdt.ticker
        symbol = self._by_venue_symbol.get(parts[1]) if len(parts) > 2 else None
        if symbol is None:
            return True
        if parts[2] == "mbp":
            book = self._books.get(symbol)
            if book is None:
                return True
            seq = int(tick.get("seqNum") or 0)
            if book.synced and seq and seq < book.last_update_id:
                return True  # an older snapshot than the one we hold
            book.load_snapshot(_levels(tick.get("bids")), _levels(tick.get("asks")), seq)
        elif parts[2] == "ticker":
            last = tick.get("lastPrice") or tick.get("close")
            if last:
                self._tickers[symbol] = TickerStats(
                    last_price=float(last),
                    volume_base=float(tick.get("amount") or 0.0),
                    volume_quote=float(tick.get("vol") or 0.0),
                )
        return True
