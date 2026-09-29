"""OKX Spot adapter (execution on OKX Demo Trading).

Market data: ``books`` (400-level, snapshot + incremental) and ``tickers``
on the public WebSocket. Each incremental message's ``prevSeqId`` must equal
the previous message's ``seqId`` (verified live); a break -> reconnect.

OKX documents a CRC32 checksum for this channel, but the public ``books``
channel currently sends ``checksum: 0`` (observed 2026-09-28), so the
sequence chain is the only integrity check that can actually be performed.

Demo trading: ccxt's sandbox mode adds the ``x-simulated-trading: 1`` header;
demo API keys are separate from live keys and need a passphrase.
Measured 2026-09-28: the demo book tracks the real market within ~3 bps.
"""

import json

from websockets.asyncio.client import ClientConnection

from backend.exchanges.base import TickerStats
from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.stream_sync import ChainResult, InStreamSnapshotSync
from backend.shared.utils.logger import get_logger

logger = get_logger("okx_adapter")

WS_URLS = {
    "mainnet": "wss://ws.okx.com:8443/ws/v5/public",
    "testnet": "wss://wspap.okx.com:8443/ws/v5/public",
}


def _levels(raw: list[list[str]]) -> list[tuple[float, float]]:
    # OKX levels are [price, size, deprecated, order_count]
    return [(float(level[0]), float(level[1])) for level in raw]


class OkxAdapter(CcxtVenueAdapter):
    exchange_id = "okx"
    ccxt_id = "okx"
    ccxt_options = {"defaultType": "spot"}
    test_environment_name = "demo"  # reached via ccxt sandbox mode (x-simulated-trading header)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._syncs: dict[str, InStreamSnapshotSync] = {}

    def _on_rules_built(self) -> None:
        self._syncs = {s: InStreamSnapshotSync(self._books[s]) for s in self._rules}

    def _gap_count(self) -> int:
        return sum(s.gaps for s in self._syncs.values())

    async def _run_feed(self) -> None:
        # OKX closes connections idle for 30 s; the text "ping" keeps it open.
        await self._ws_loop(WS_URLS[self.data_mode], self._on_open, self._on_message,
                            app_ping="ping", ping_every_s=20)

    async def _on_open(self, ws: ClientConnection) -> None:
        args = []
        for rules in self._rules.values():
            args += [{"channel": "books", "instId": rules.venue_symbol},
                     {"channel": "tickers", "instId": rules.venue_symbol}]
        await ws.send(json.dumps({"op": "subscribe", "args": args}))

    def _on_message(self, raw: str | bytes) -> bool:
        if raw == "pong":
            return True
        try:
            msg = json.loads(raw)
        except ValueError:
            return True
        if msg.get("event") == "error":
            logger.warning("ws_request_failed", venue_id=self.venue_id, message=str(msg.get("msg"))[:200])
            return True
        channel = (msg.get("arg") or {}).get("channel")
        data = msg.get("data")
        if not channel or not data:
            return True
        symbol = self._by_venue_symbol.get(msg["arg"].get("instId", ""))
        if symbol is None:
            return True

        if channel == "books":
            sync = self._syncs.get(symbol)
            if sync is None:
                return True
            d = data[0]
            bids, asks, seq = _levels(d.get("bids", [])), _levels(d.get("asks", [])), int(d["seqId"])
            if msg.get("action") == "snapshot":
                sync.on_snapshot(bids, asks, seq)
                return True
            chained = int(d.get("prevSeqId", -2)) == sync.last_id
            if sync.on_delta(bids, asks, seq, chained=chained) is ChainResult.GAP:
                logger.warning("book_gap_detected", venue_id=self.venue_id, symbol=symbol, seq=seq)
                return False
        elif channel == "tickers":
            t = data[0]
            if t.get("last"):
                self._tickers[symbol] = TickerStats(
                    last_price=float(t["last"]),
                    volume_base=float(t.get("vol24h") or 0.0),
                    volume_quote=float(t.get("volCcy24h") or 0.0),
                )
        return True

    async def _fetch_by_client_id(self, symbol: str, cid: str) -> dict:
        return await self._client.fetch_order(None, symbol, {"clOrdId": cid})
