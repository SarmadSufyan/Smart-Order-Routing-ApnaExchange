"""Binance Spot adapter.

Market data: one combined WebSocket carrying ``<sym>@depth@100ms`` (diff
depth) and ``<sym>@miniTicker`` for every configured symbol. Each symbol's
book is seeded from a REST snapshot and kept correct by the update-id-chain
synchroniser (resync that symbol on a gap).

Execution, balances and health come from ``CcxtVenueAdapter``.
"""

import asyncio
import json

from websockets.asyncio.client import ClientConnection

from backend.exchanges.base import TickerStats
from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.update_id_sync import DiffResult, UpdateIdChainSynchronizer
from backend.shared.utils.logger import get_logger

logger = get_logger("binance_adapter")

WS_URLS = {
    "testnet": "wss://stream.testnet.binance.vision/stream",
    "mainnet": "wss://stream.binance.com:9443/stream",
}
DEPTH_SUFFIX = "@depth@100ms"
TICKER_SUFFIX = "@miniTicker"


class BinanceAdapter(CcxtVenueAdapter):
    exchange_id = "binance"
    ccxt_id = "binance"
    ccxt_options = {"defaultType": "spot", "fetchMarkets": ["spot"]}

    def __init__(self, *, snapshot_depth: int = 1000, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snapshot_depth = snapshot_depth
        self._syncs = {s: UpdateIdChainSynchronizer(b) for s, b in self._books.items()}
        self._snapshot_tasks: dict[str, asyncio.Task] = {}

    def _on_rules_built(self) -> None:
        self._syncs = {s: self._syncs[s] for s in self._rules}

    def _reset_books(self) -> None:
        for sync in self._syncs.values():
            sync.reset_for_reconnect()

    def _gap_count(self) -> int:
        return sum(s.resyncs for s in self._syncs.values())

    async def _run_feed(self) -> None:
        streams = []
        for rules in self._rules.values():
            s = rules.venue_symbol.lower()
            streams += [s + DEPTH_SUFFIX, s + TICKER_SUFFIX]
        url = f"{WS_URLS[self.data_mode]}?streams={'/'.join(streams)}"
        await self._ws_loop(url, self._on_open, self._on_message)

    async def _on_open(self, ws: ClientConnection) -> None:
        # Streams are selected in the URL; start buffering, then seed each book.
        for symbol in self._rules:
            self._schedule_snapshot(symbol)

    def _on_message(self, raw: str | bytes) -> bool:
        try:
            msg = json.loads(raw)
        except ValueError:
            return True
        data, stream = msg.get("data"), msg.get("stream", "")
        if not isinstance(data, dict):
            return True
        symbol = self._by_venue_symbol.get(data.get("s", ""))
        if symbol is None:
            return True

        if stream.endswith(DEPTH_SUFFIX):
            sync = self._syncs[symbol]
            if sync.on_diff(data) is DiffResult.GAP:
                logger.warning("book_gap_detected", venue_id=self.venue_id, symbol=symbol,
                               local_update_id=sync.book.last_update_id, event_first_id=data.get("U"))
                sync.reset()
                self._schedule_snapshot(symbol)
        elif stream.endswith(TICKER_SUFFIX):
            self._tickers[symbol] = TickerStats(
                last_price=float(data["c"]), volume_base=float(data["v"]), volume_quote=float(data["q"])
            )
        return True  # gaps are repaired per symbol; the connection stays up

    def _schedule_snapshot(self, symbol: str) -> None:
        if symbol in self._snapshot_tasks:
            return  # already re-seeding; that loop retries until synced
        task = self._spawn(self._sync_symbol(symbol), f"{self.venue_id}-{symbol}-snapshot")
        self._snapshot_tasks[symbol] = task
        task.add_done_callback(lambda _t, s=symbol: self._snapshot_tasks.pop(s, None))

    async def _sync_symbol(self, symbol: str) -> None:
        rules, sync = self._rules[symbol], self._syncs[symbol]
        delay = 0.5
        while self._running and self._connected and sync.needs_snapshot:
            try:
                raw = await self._data_client.public_get_depth(
                    {"symbol": rules.venue_symbol, "limit": self._snapshot_depth}
                )
                if sync.load_snapshot(raw) is DiffResult.APPLIED:
                    bids, asks = sync.book.level_count
                    logger.info("book_synced", venue_id=self.venue_id, symbol=symbol,
                                last_update_id=sync.book.last_update_id, bid_levels=bids, ask_levels=asks)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("snapshot_failed", venue_id=self.venue_id, symbol=symbol, error=str(exc)[:200])
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)

    async def _fetch_by_client_id(self, symbol: str, cid: str) -> dict:
        return await self._client.fetch_order(None, symbol, {"origClientOrderId": cid})
