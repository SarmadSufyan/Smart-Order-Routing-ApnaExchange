"""Realized volatility from our own NBBO mid prices (real market data).

The stress test needs to know how much a coin's price typically moves per
minute. Instead of an assumed number, we sample the live NBBO mid every few
seconds and measure it: the standard deviation of the log returns between
samples, scaled to one minute (variance grows linearly with time, so
sigma_1min = sigma_sample * sqrt(60 / interval)).

Honest limits: right after start-up there is no history (the stress test
says so instead of guessing), and a quiet night market really does measure
low volatility.
"""

import asyncio
import math
from collections import deque

from backend.shared.utils.logger import get_logger

logger = get_logger("volatility")

MIN_RETURNS = 12  # one minute at the default 5 s sampling


def sigma_per_minute(mids: list[float], interval_s: float) -> float | None:
    """Per-minute volatility of a mid-price series sampled every ``interval_s``."""
    returns = [math.log(b / a) for a, b in zip(mids, mids[1:], strict=False) if a > 0 and b > 0]
    if len(returns) < MIN_RETURNS:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var * 60.0 / interval_s)


class VolatilityTracker:
    def __init__(self, aggregator, symbols: list[str], interval_s: float = 5.0, window: int = 720) -> None:
        self._aggregator = aggregator
        self._symbols = symbols
        self.interval_s = interval_s
        self._mids: dict[str, deque[float]] = {s: deque(maxlen=window) for s in symbols}
        self._running = False

    def sample(self, symbol: str, mid: float) -> None:
        if symbol in self._mids and mid > 0:
            self._mids[symbol].append(mid)

    def samples(self, symbol: str) -> int:
        return len(self._mids.get(symbol, ()))

    def sigma(self, symbol: str) -> float | None:
        mids = self._mids.get(symbol)
        return sigma_per_minute(list(mids), self.interval_s) if mids else None

    async def start(self) -> None:
        self._running = True
        logger.info("volatility_tracker_started", interval_s=self.interval_s)
        while self._running:
            for symbol in self._symbols:
                try:
                    nbbo = await self._aggregator.compute_nbbo(symbol)
                    if nbbo and nbbo.best_bid and nbbo.best_ask:
                        self.sample(symbol, (nbbo.best_bid + nbbo.best_ask) / 2)
                except Exception as exc:  # noqa: BLE001 — keep sampling the others
                    logger.warning("volatility_sample_failed", symbol=symbol, error=str(exc)[:160])
            await asyncio.sleep(self.interval_s)

    async def stop(self) -> None:
        self._running = False
