"""Gate Spot adapter (execution on the Gate TestNet).

Market data: ``spot.order_book_update`` (100 ms diffs with ``U``/``u``) +
``spot.tickers`` on the public WebSocket, seeded from the REST snapshot
``/spot/order_book?with_id=true``. That is the same update-id-chain model as
Binance (verified live: U == previous u + 1), so the same synchroniser keeps
the books correct.

Measured 2026-09-28: Gate's TestNet spot books track the real market within
~0-10 bps on all five pairs, so execution there is coherent with decisions
made on real-market data. Only a mainnet WebSocket is used for data.
"""

import asyncio
import json
import time

import httpx
from websockets.asyncio.client import ClientConnection

from backend.exchanges.base import TickerStats
from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.update_id_sync import DiffResult, UpdateIdChainSynchronizer
from backend.shared.utils.logger import get_logger

logger = get_logger("gate_adapter")

WS_URL = "wss://api.gateio.ws/ws/v4/"
REST_URLS = {
    "mainnet": "https://api.gateio.ws/api/v4",
    "testnet": "https://api-testnet.gateapi.io/api/v4",
}
SNAPSHOT_LEVELS = 100


class GateAdapter(CcxtVenueAdapter):
    exchange_id = "gate"
    ccxt_id = "gate"
    ccxt_options = {"defaultType": "spot"}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        if self.data_mode != "mainnet":
            # Gate's spot testnet has no public WebSocket feed we can use.
            logger.warning("gate_data_forced_mainnet", requested=self.data_mode)
            self.data_mode = "mainnet"
        self._syncs = {s: UpdateIdChainSynchronizer(b) for s, b in self._books.items()}
        self._snapshot_tasks: dict[str, asyncio.Task] = {}
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(base_url=REST_URLS[self.data_mode], timeout=15.0)
        await super().start()

    async def stop(self) -> None:
        await super().stop()
        if self._http:
            await self._http.aclose()

    def _on_rules_built(self) -> None:
        self._syncs = {s: self._syncs[s] for s in self._rules}

    def _reset_books(self) -> None:
        for sync in self._syncs.values():
            sync.reset_for_reconnect()

    def _gap_count(self) -> int:
        return sum(s.resyncs for s in self._syncs.values())

    async def _run_feed(self) -> None:
        await self._ws_loop(WS_URL, self._on_open, self._on_message)

    async def _on_open(self, ws: ClientConnection) -> None:
        now = int(time.time())
        pairs = [r.venue_symbol for r in self._rules.values()]
        for pair in pairs:  # order_book_update takes one pair per subscription
            await ws.send(json.dumps({"time": now, "channel": "spot.order_book_update",
                                      "event": "subscribe", "payload": [pair, "100ms"]}))
        await ws.send(json.dumps({"time": now, "channel": "spot.tickers", "event": "subscribe", "payload": pairs}))
        for symbol in self._rules:
            self._schedule_snapshot(symbol)

    def _on_message(self, raw: str | bytes) -> bool:
        try:
            msg = json.loads(raw)
        except ValueError:
            return True
        if msg.get("error"):
            logger.warning("ws_request_failed", venue_id=self.venue_id, error=str(msg["error"])[:200])
            return True
        if msg.get("event") != "update":
            return True
        channel, result = msg.get("channel"), msg.get("result")
        if not isinstance(result, dict):
            return True

        if channel == "spot.order_book_update":
            symbol = self._by_venue_symbol.get(result.get("s", ""))
            sync = self._syncs.get(symbol) if symbol else None
            if sync is None:
                return True
            if sync.on_diff(result) is DiffResult.GAP:
                logger.warning("book_gap_detected", venue_id=self.venue_id, symbol=symbol, event_first_id=result.get("U"))
                sync.reset()
                self._schedule_snapshot(symbol)
        elif channel == "spot.tickers":
            symbol = self._by_venue_symbol.get(result.get("currency_pair", ""))
            if symbol and result.get("last"):
                self._tickers[symbol] = TickerStats(
                    last_price=float(result["last"]),
                    volume_base=float(result.get("base_volume") or 0.0),
                    volume_quote=float(result.get("quote_volume") or 0.0),
                )
        return True

    def _schedule_snapshot(self, symbol: str) -> None:
        if symbol in self._snapshot_tasks:
            return
        task = self._spawn(self._sync_symbol(symbol), f"{self.venue_id}-{symbol}-snapshot")
        self._snapshot_tasks[symbol] = task
        task.add_done_callback(lambda _t, s=symbol: self._snapshot_tasks.pop(s, None))

    async def _sync_symbol(self, symbol: str) -> None:
        rules, sync = self._rules[symbol], self._syncs[symbol]
        delay = 0.5
        while self._running and self._connected and sync.needs_snapshot:
            try:
                resp = await self._http.get("/spot/order_book", params={
                    "currency_pair": rules.venue_symbol, "limit": SNAPSHOT_LEVELS, "with_id": "true"})
                resp.raise_for_status()
                snap = resp.json()
                result = sync.load_snapshot({"lastUpdateId": snap["id"], "bids": snap["bids"], "asks": snap["asks"]})
                if result is DiffResult.APPLIED:
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
        # Gate accepts the custom "t-..." text in place of the order id.
        return await self._client.fetch_order(f"t-{cid}", symbol)
