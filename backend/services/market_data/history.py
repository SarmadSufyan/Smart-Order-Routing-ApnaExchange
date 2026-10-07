"""Real-market history: 1-minute candles from every exchange we watch, merged per minute.

The execution algorithms need the market's real trading volume — VWAP for
the shape of a normal day, POV for the volume happening right now, and every
algo for the market VWAP it is judged against. That comes from the
exchanges' public candles (the real market when MARKET_DATA_MODE=mainnet,
whatever the execution mode), fetched concurrently and summed per minute.

An exchange that is slow or unreachable is skipped for that request (each
has its own time limit) and the answer lists which exchanges it is based on;
it never blocks an algo. Past windows never change, so they are cached.
"""

import asyncio
import time
from collections import OrderedDict

from backend.exchanges.base import Candle
from backend.shared.models.market_data import MINUTE_MS, MinuteBar
from backend.shared.utils.logger import get_logger

logger = get_logger("market_history")

DAY_MS = 86_400_000
PAGE = 1000  # candles per request (Binance and Bybit allow 1000)
MAX_PAGES = 3  # 3,000 minutes = more than the longest window (24 h)


def merge_candles(per_venue: list[list[Candle]], start_ms: int, end_ms: int) -> list[MinuteBar]:
    """Sum every venue's 1-minute candles per minute: volume, and typical price x volume."""
    merged: dict[int, list[float]] = {}
    for candles in per_venue:
        for c in candles:
            if c.ts_ms + MINUTE_MS <= start_ms or c.ts_ms >= end_ms or c.volume <= 0:
                continue
            typical = (c.high + c.low + c.close) / 3
            slot = merged.setdefault(c.ts_ms, [0.0, 0.0])
            slot[0] += c.volume
            slot[1] += typical * c.volume
    return [MinuteBar(ts_ms=ts, volume=v, pv=pv) for ts, (v, pv) in sorted(merged.items())]


class MarketHistory:
    def __init__(self, registry, call_timeout_s: float = 12.0, cache_size: int = 256,
                 clock=time.time) -> None:
        self._registry = registry
        self._timeout = call_timeout_s
        self._cache: OrderedDict[tuple, list[Candle]] = OrderedDict()
        self._cache_size = cache_size
        self._clock = clock

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def minute_bars(self, symbol: str, start_ms: int, end_ms: int) -> tuple[list[MinuteBar], list[str]]:
        """Merged minute bars covering [start, end), and the venues the answer is based on."""
        adapters = [a for a in self._registry.all() if symbol in a.symbols]
        results = await asyncio.gather(
            *(self._venue_candles(a, symbol, start_ms, end_ms) for a in adapters), return_exceptions=True
        )
        answered, per_venue = [], []
        for adapter, res in zip(adapters, results, strict=True):
            if isinstance(res, BaseException):
                logger.info("history_venue_skipped", venue_id=adapter.venue_id, symbol=symbol,
                            error=(str(res) or type(res).__name__)[:160])
                continue
            if res:
                answered.append(adapter.venue_id)
                per_venue.append(res)
        return merge_candles(per_venue, start_ms, end_ms), answered

    async def recent_bars(self, symbol: str, minutes: int) -> tuple[list[MinuteBar], list[str]]:
        now = self._now_ms()
        return await self.minute_bars(symbol, now - minutes * MINUTE_MS, now)

    async def past_days(self, symbol: str, start_ms: int, duration_s: float,
                        days: int) -> tuple[list[list[MinuteBar]], list[int], list[str]]:
        """The same time window on each of the last ``days`` days (for a VWAP volume profile)."""
        starts = [start_ms - d * DAY_MS for d in range(1, days + 1)]
        windows = await asyncio.gather(
            *(self.minute_bars(symbol, s, s + int(duration_s * 1000)) for s in starts)
        )
        venues = sorted({v for _, answered in windows for v in answered})
        return [bars for bars, _ in windows], starts, venues

    async def _venue_candles(self, adapter, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        key = (adapter.venue_id, symbol, start_ms, end_ms)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        candles = await asyncio.wait_for(self._fetch(adapter, symbol, start_ms, end_ms), timeout=self._timeout)
        if end_ms < self._now_ms() - 2 * MINUTE_MS and candles:  # a finished window never changes
            self._cache[key] = candles
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return candles

    @staticmethod
    async def _fetch(adapter, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        since = start_ms - start_ms % MINUTE_MS
        out: list[Candle] = []
        for _ in range(MAX_PAGES):
            rows = await adapter.fetch_candles(symbol, "1m", since_ms=since, limit=PAGE)
            rows = [c for c in rows if since <= c.ts_ms < end_ms]
            if not rows:
                break
            out.extend(rows)
            since = rows[-1].ts_ms + MINUTE_MS
            if since >= end_ms:  # some exchanges return fewer than PAGE per request: keep paging until the end
                break
        return out
