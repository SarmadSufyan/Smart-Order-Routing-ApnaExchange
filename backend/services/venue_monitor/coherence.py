"""Execution coherence: can we trust a venue's execution book?

ApnaSOR decides on real-market (mainnet) order books but executes on
testnets/demo environments. That is only meaningful where the execution
book tracks the real one. Measured 2026-09-28:

    OKX demo ~0-1 bps   Bybit demo <1 bps   Gate testnet ~1-4 bps   Binance testnet ~3-5 bps
    (Bybit's separate TESTNET was BTC +170 bps, XRP +410 bps, BNB no asks —
    this monitor is what caught it, and why Bybit now uses Demo Trading.)

Every few seconds this monitor compares, per venue and symbol, the mid of
the EXECUTION venue's best bid/ask with the mid of the book we decide on.
Beyond ``threshold_bps`` the pair is excluded from routing in BOTH
directions: an execution book 165 bps above the real market would make a
BUY expire and a SELL fill at a price the real market never offered.

When data and execution use the same book (e.g. mainnet in Phase 8) there is
nothing to compare and the pair is always coherent.
"""

import asyncio
from datetime import UTC, datetime

from backend.exchanges.registry import ExchangeRegistry
from backend.shared.models.venue import CoherenceState, CoherenceStatus
from backend.shared.utils.logger import get_logger

logger = get_logger("coherence_monitor")


def evaluate(
    exec_bid: float | None, exec_ask: float | None, data_mid: float | None, threshold_bps: float
) -> CoherenceState:
    """Pure decision: how far is the execution book from the decision book?"""
    now = datetime.now(UTC)
    if not exec_bid or not exec_ask or exec_bid <= 0 or exec_ask <= 0:
        return CoherenceState(status=CoherenceStatus.EMPTY, exec_bid=exec_bid, exec_ask=exec_ask, checked_at=now)
    if not data_mid or data_mid <= 0:
        return CoherenceState(status=CoherenceStatus.UNKNOWN, exec_bid=exec_bid, exec_ask=exec_ask, checked_at=now)
    divergence = ((exec_bid + exec_ask) / 2 / data_mid - 1) * 10_000
    status = CoherenceStatus.OK if abs(divergence) <= threshold_bps else CoherenceStatus.DIVERGED
    return CoherenceState(
        status=status, divergence_bps=round(divergence, 2),
        exec_bid=exec_bid, exec_ask=exec_ask, checked_at=now,
    )


class CoherenceMonitor:
    def __init__(
        self,
        registry: ExchangeRegistry,
        market_data_collector,
        symbols: list[str],
        threshold_bps: float = 50.0,
        interval_s: float = 5.0,
    ) -> None:
        self._registry = registry
        self._collector = market_data_collector
        self._symbols = list(symbols)
        self._threshold = threshold_bps
        self._interval_s = interval_s
        self._states: dict[tuple[str, str], CoherenceState] = {}
        self._running = False

    @property
    def threshold_bps(self) -> float:
        return self._threshold

    def state(self, venue_id: str, symbol: str) -> CoherenceState:
        adapter = self._registry.get(venue_id)
        if adapter is not None and adapter.data_mode == adapter.mode:
            return CoherenceState(status=CoherenceStatus.SAME_BOOK)
        return self._states.get((venue_id, symbol), CoherenceState(status=CoherenceStatus.UNKNOWN))

    def venue_states(self, venue_id: str) -> dict[str, CoherenceState]:
        adapter = self._registry.get(venue_id)
        symbols = adapter.symbols if adapter else []
        return {s: self.state(venue_id, s) for s in symbols}

    def blocking_reason(self, venue_id: str, symbol: str) -> str | None:
        """Why this venue can't execute this symbol right now, or None."""
        st = self.state(venue_id, symbol)
        if st.status in (CoherenceStatus.OK, CoherenceStatus.SAME_BOOK):
            return None
        adapter = self._registry.get(venue_id)
        name = adapter.name if adapter else venue_id
        env = adapter.display_environment if adapter else "execution"
        if st.status is CoherenceStatus.DIVERGED:
            return (f"{name} {env} book is {st.divergence_bps:+.0f} bps from the real market "
                    f"(limit ±{self._threshold:g} bps) - fills would not be real-market prices")
        if st.status is CoherenceStatus.EMPTY:
            return f"{name} {env} book for {symbol} has no bid or no ask"
        return f"{name} {env} book for {symbol} not verified yet"

    async def check_once(self) -> None:
        for adapter in self._registry.all():
            # Execution-book prices are public, so measure even before API keys
            # exist: the team can see which symbols will work up front.
            if not adapter.execution_available or adapter.data_mode == adapter.mode:
                continue
            wanted = [s for s in self._symbols if s in adapter.symbols]
            try:
                tops = await adapter.execution_tops(wanted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("coherence_probe_failed", venue_id=adapter.venue_id, error=str(exc)[:160])
                continue
            for symbol in wanted:
                quote = self._collector.get_venue_quote(adapter.venue_id, symbol)
                data_mid = (quote.bid_price + quote.ask_price) / 2 if quote and not quote.is_stale else None
                bid, ask = tops.get(symbol, (None, None))
                new = evaluate(bid, ask, data_mid, self._threshold)
                old = self._states.get((adapter.venue_id, symbol))
                if old is None or old.status != new.status:
                    logger.info("coherence_changed", venue_id=adapter.venue_id, symbol=symbol,
                                status=new.status.value, divergence_bps=new.divergence_bps)
                self._states[(adapter.venue_id, symbol)] = new

    async def start(self) -> None:
        self._running = True
        logger.info("coherence_monitor_started", threshold_bps=self._threshold)
        while self._running:
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("coherence_check_failed", error=str(exc)[:200])
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        self._running = False
