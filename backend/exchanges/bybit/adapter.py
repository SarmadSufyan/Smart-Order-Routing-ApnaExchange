"""Bybit Spot adapter (execution on Bybit DEMO TRADING).

Market data: ``orderbook.50.<SYM>`` + ``tickers.<SYM>`` on the public spot
WebSocket. The first orderbook message is a full snapshot; each delta's
update id ``u`` must be exactly the previous ``u`` + 1 (verified live), so a
break means a lost message -> reconnect for a fresh snapshot.

Why Demo Trading and not Testnet (measured 2026-09-28): Bybit runs two
non-real-money environments. Its TESTNET (api-testnet.bybit.com) is a
separate market — BTC ~+170 bps, XRP ~+410 bps off the real market, BNB with
no asks. Its DEMO TRADING (api-demo.bybit.com) matches the real market to
within 1 bps on all five pairs. Demo keys are created from a normal Bybit
account switched into Demo Trading mode.
"""

import json

from websockets.asyncio.client import ClientConnection

from backend.exchanges.base import TickerStats
from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.stream_sync import ChainResult, InStreamSnapshotSync
from backend.shared.utils.logger import get_logger

logger = get_logger("bybit_adapter")

WS_URLS = {
    "mainnet": "wss://stream.bybit.com/v5/public/spot",
    "testnet": "wss://stream-testnet.bybit.com/v5/public/spot",
}
MAX_ARGS_PER_SUBSCRIBE = 10  # Bybit spot limit


def _levels(raw: list[list[str]]) -> list[tuple[float, float]]:
    return [(float(p), float(q)) for p, q in raw]


class BybitAdapter(CcxtVenueAdapter):
    exchange_id = "bybit"
    ccxt_id = "bybit"
    ccxt_options = {"defaultType": "spot"}
    test_environment = "demo"
    test_environment_name = "demo"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._syncs: dict[str, InStreamSnapshotSync] = {}

    def _on_rules_built(self) -> None:
        self._syncs = {s: InStreamSnapshotSync(self._books[s]) for s in self._rules}

    def _gap_count(self) -> int:
        return sum(s.gaps for s in self._syncs.values())

    async def _run_feed(self) -> None:
        await self._ws_loop(WS_URLS[self.data_mode], self._on_open, self._on_message,
                            app_ping='{"op":"ping"}', ping_every_s=20)

    async def _on_open(self, ws: ClientConnection) -> None:
        args = []
        for rules in self._rules.values():
            args += [f"orderbook.50.{rules.venue_symbol}", f"tickers.{rules.venue_symbol}"]
        for i in range(0, len(args), MAX_ARGS_PER_SUBSCRIBE):
            await ws.send(json.dumps({"op": "subscribe", "args": args[i:i + MAX_ARGS_PER_SUBSCRIBE]}))

    def _on_message(self, raw: str | bytes) -> bool:
        try:
            msg = json.loads(raw)
        except ValueError:
            return True
        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or not isinstance(data, dict):
            if msg.get("success") is False:
                logger.warning("ws_request_failed", venue_id=self.venue_id, message=str(msg.get("ret_msg"))[:200])
            return True

        if topic.startswith("orderbook."):
            symbol = self._by_venue_symbol.get(data.get("s", ""))
            sync = self._syncs.get(symbol) if symbol else None
            if sync is None:
                return True
            bids, asks, u = _levels(data.get("b", [])), _levels(data.get("a", [])), int(data["u"])
            if msg.get("type") == "snapshot":
                sync.on_snapshot(bids, asks, u)
                return True
            if sync.on_delta(bids, asks, u, chained=u == sync.last_id + 1) is ChainResult.GAP:
                logger.warning("book_gap_detected", venue_id=self.venue_id, symbol=symbol, u=u)
                return False
        elif topic.startswith("tickers."):
            symbol = self._by_venue_symbol.get(data.get("symbol", ""))
            if symbol and data.get("lastPrice"):
                self._tickers[symbol] = TickerStats(
                    last_price=float(data["lastPrice"]),
                    volume_base=float(data.get("volume24h") or 0.0),
                    volume_quote=float(data.get("turnover24h") or 0.0),
                )
        return True

    def _max_limit_deviation_bps(self, market: dict) -> float | None:
        # Bybit rejects limit prices beyond ±priceLimitRatio of its reference
        # price ("Buy order price cannot be higher than ..."), found by a live
        # test. Use the tighter of X/Y with a 20% safety margin.
        risk = (market.get("info") or {}).get("riskParameters") or {}
        ratios = [float(r) for r in (risk.get("priceLimitRatioX"), risk.get("priceLimitRatioY")) if r]
        return min(ratios) * 10_000 * 0.8 if ratios else None

    async def _fetch_order(self, order_id: str, symbol: str) -> dict:
        # ccxt's Bybit fetchOrder needs this flag for unified accounts.
        return await self._client.fetch_order(order_id, symbol, {"acknowledged": True})

    async def _fetch_by_client_id(self, symbol: str, cid: str) -> dict:
        # ccxt's Bybit fetchOrder needs the exchange id, but Bybit's API can
        # look up by our orderLinkId directly — any status, not just the last
        # N orders. Active orders live in /realtime, finished ones in /history
        # (which lags execution by a moment; _reconcile retries).
        market = self._client.market(symbol)
        for endpoint in (self._client.private_get_v5_order_realtime, self._client.private_get_v5_order_history):
            raw = await endpoint({"category": "spot", "symbol": market["id"], "orderLinkId": cid})
            found = ((raw or {}).get("result") or {}).get("list") or []
            if found:
                return self._client.parse_order(found[0], market)
        raise LookupError(f"order {cid} not found on {self.name}")
