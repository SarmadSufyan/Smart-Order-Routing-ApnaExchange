"""Candle history for research: a local SQLite store, kept current from the exchanges' public data.

One REFERENCE market per coin — Binance's real (mainnet) market, the deepest; Bybit, then Gate,
if Binance can't be reached — so a chart, a backtest and a forecast all read the same series.
(The execution algorithms use a different view: all exchanges' 1-minute volume summed.)

    timeframe   kept           why
    1m          5,000 candles  ~3.5 days: the next-minute forecast and intraday backtests
    1h          5,000 candles  ~7 months: the next-hour forecast
    1d          1,500 candles  ~4 years: the next-day forecast

Only CLOSED candles are stored (the one still forming would change). The store also keeps the
live forecast log — every forecast the service issued and, once its candle closed, what happened —
which is the honest track record.

Its own file (``data/market-history.db``): real-market data is the same whichever environment
orders go to, and it never mixes with the audit log.
"""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from backend.shared.utils.logger import get_logger

logger = get_logger("research_candles")

TIMEFRAME_MS = {"1m": 60_000, "1h": 3_600_000, "1d": 86_400_000}
HISTORY_BARS = {"1m": 5_000, "1h": 5_000, "1d": 1_500}
SYNC_EVERY_S = {"1m": 20.0, "1h": 60.0, "1d": 600.0}
PREFERRED_SOURCES = ("V1", "V4", "V6", "V5")
PAGE = 1_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts_ms INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts_ms)
);
CREATE TABLE IF NOT EXISTS forecasts (
    symbol TEXT NOT NULL, horizon TEXT NOT NULL, issued_ts INTEGER NOT NULL,  -- close time of the candle it was made on
    price REAL NOT NULL, p_up REAL NOT NULL, low80 REAL, high80 REAL, edge INTEGER NOT NULL,
    actual_price REAL, up_actual INTEGER, in_range80 INTEGER,
    PRIMARY KEY (symbol, horizon, issued_ts)
);
"""


class CandleStore:
    """Thread-safe SQLite store; call the methods through ``asyncio.to_thread`` from async code."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def open(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def upsert(self, symbol: str, timeframe: str, rows: list[tuple], source: str) -> int:
        """rows = [(ts_ms, open, high, low, close, volume)]."""
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(symbol, timeframe, *r, source) for r in rows])
            self._db.commit()
        return len(rows)

    def last_ts(self, symbol: str, timeframe: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT MAX(ts_ms) FROM candles WHERE symbol=? AND timeframe=?",
                                   (symbol, timeframe)).fetchone()
        return row[0]

    def load(self, symbol: str, timeframe: str, limit: int) -> dict[str, np.ndarray]:
        """The latest ``limit`` candles, oldest first, as arrays: ts, open, high, low, close, volume."""
        with self._lock:
            rows = self._db.execute(
                "SELECT ts_ms, open, high, low, close, volume FROM candles WHERE symbol=? AND timeframe=? "
                "ORDER BY ts_ms DESC LIMIT ?", (symbol, timeframe, limit)).fetchall()
        rows.reverse()
        arr = np.asarray(rows, dtype=float) if rows else np.zeros((0, 6))
        return {"ts": arr[:, 0].astype(np.int64), "open": arr[:, 1], "high": arr[:, 2], "low": arr[:, 3],
                "close": arr[:, 4], "volume": arr[:, 5]}

    def info(self, symbol: str, timeframe: str) -> tuple[int, int | None, int | None, str | None]:
        """(count, first ts, last ts, source of the last candle)."""
        with self._lock:
            n, first, last = self._db.execute(
                "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM candles WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)).fetchone()
            src = self._db.execute(
                "SELECT source FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts_ms DESC LIMIT 1",
                (symbol, timeframe)).fetchone()
        return n, first, last, src[0] if src else None

    def trim(self, symbol: str, timeframe: str, keep: int) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM candles WHERE symbol=? AND timeframe=? AND ts_ms < (SELECT MIN(ts_ms) FROM "
                "(SELECT ts_ms FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts_ms DESC LIMIT ?))",
                (symbol, timeframe, symbol, timeframe, keep))
            self._db.commit()

    # ── the live forecast log ──
    def log_forecast(self, symbol: str, horizon: str, issued_ts: int, price: float, p_up: float,
                     low80: float | None, high80: float | None, edge: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO forecasts (symbol, horizon, issued_ts, price, p_up, low80, high80, edge) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (symbol, horizon, issued_ts, price, p_up, low80, high80, int(edge)))
            self._db.commit()

    def unscored(self, symbol: str, horizon: str) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT issued_ts, price, low80, high80 FROM forecasts WHERE symbol=? AND horizon=? "
                "AND actual_price IS NULL ORDER BY issued_ts", (symbol, horizon)).fetchall()

    def score_forecast(self, symbol: str, horizon: str, issued_ts: int, actual: float, up: bool,
                       in_range: bool | None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE forecasts SET actual_price=?, up_actual=?, in_range80=? WHERE symbol=? AND horizon=? "
                "AND issued_ts=?", (actual, int(up), None if in_range is None else int(in_range), symbol, horizon,
                                    issued_ts))
            self._db.commit()

    def scored(self, symbol: str, horizon: str, limit: int = 5_000) -> list[tuple]:
        """[(issued_ts, p_up, up_actual, in_range80, edge)] newest first."""
        with self._lock:
            return self._db.execute(
                "SELECT issued_ts, p_up, up_actual, in_range80, edge FROM forecasts WHERE symbol=? AND horizon=? "
                "AND actual_price IS NOT NULL ORDER BY issued_ts DESC LIMIT ?", (symbol, horizon, limit)).fetchall()


class CandleSync:
    """Downloads the history once, then keeps every (coin, timeframe) current; tells listeners about new candles."""

    def __init__(self, registry, store: CandleStore, symbols: list[str], clock=time.time,
                 history_bars: dict[str, int] | None = None, sources: tuple[str, ...] = PREFERRED_SOURCES) -> None:
        self._registry = registry
        self._store = store
        self._symbols = list(symbols)
        self._clock = clock
        self._history = dict(history_bars or HISTORY_BARS)
        self._sources = sources
        self._status: dict[tuple[str, str], dict] = {}
        self._listeners = []
        self._task: asyncio.Task | None = None
        self._running = False
        self.ready = asyncio.Event()

    def on_new_candles(self, callback) -> None:
        """``await callback(symbol, timeframe)`` after new closed candles were stored."""
        self._listeners.append(callback)

    def status(self) -> list[dict]:
        out = []
        for symbol in self._symbols:
            for tf in TIMEFRAME_MS:
                s = self._status.get((symbol, tf), {})
                out.append({"symbol": symbol, "timeframe": tf, **s})
        return out

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="candle-sync")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        # First fill: the long timeframes first (few requests), so every page has something soon.
        for tf in ("1d", "1h", "1m"):
            await asyncio.gather(*(self.sync(s, tf) for s in self._symbols), return_exceptions=True)
        self.ready.set()
        last_run = {tf: self._clock() for tf in TIMEFRAME_MS}
        while self._running:
            await asyncio.sleep(5)
            now = self._clock()
            for tf, every in SYNC_EVERY_S.items():
                # A series still empty or failing is retried every 20 s, not on its normal schedule.
                behind = any(not self._status.get((s, tf), {}).get("bars") or self._status.get((s, tf), {}).get("error")
                             for s in self._symbols)
                if now - last_run[tf] >= (min(every, 20.0) if behind else every):
                    last_run[tf] = now
                    await asyncio.gather(*(self.sync(s, tf) for s in self._symbols), return_exceptions=True)

    def _adapters(self, symbol: str):
        # Not filtered by ``adapter.symbols``: that list fills only once the exchange's markets have
        # loaded, which can be after the first sync (found live). An exchange that doesn't list the
        # coin simply returns no candles, and the next one is tried.
        for vid in self._sources:
            adapter = self._registry.get(vid)
            if adapter is not None:
                yield adapter

    async def sync(self, symbol: str, timeframe: str) -> int:
        """Fetch every closed candle since the last stored one; returns how many were added."""
        tf_ms = TIMEFRAME_MS[timeframe]
        now_ms = int(self._clock() * 1000)
        last = await asyncio.to_thread(self._store.last_ts, symbol, timeframe)
        since = last + tf_ms if last else now_ms - now_ms % tf_ms - self._history[timeframe] * tf_ms
        closed_before = now_ms - now_ms % tf_ms  # the candle starting here is still forming
        status = self._status.setdefault((symbol, timeframe), {})
        if since >= closed_before:  # already up to date
            if "bars" not in status:
                n, first, last_ts, source = await asyncio.to_thread(self._store.info, symbol, timeframe)
                status.update(bars=n, first_ts=first, last_ts=last_ts, source=source, synced_at=now_ms, error=None)
            return 0
        added, errors = 0, []
        for adapter in self._adapters(symbol):
            try:
                cursor = since
                while cursor < closed_before:
                    candles = await asyncio.wait_for(
                        adapter.fetch_candles(symbol, timeframe, since_ms=cursor, limit=PAGE), timeout=30)
                    rows = [(c.ts_ms, c.open, c.high, c.low, c.close, c.volume) for c in candles
                            if cursor <= c.ts_ms < closed_before]
                    if not rows:
                        break
                    await asyncio.to_thread(self._store.upsert, symbol, timeframe, rows, adapter.venue_id)
                    added += len(rows)
                    cursor = rows[-1][0] + tf_ms
                if added:
                    status.update(source=adapter.venue_id, error=None)
                    break
                errors.append(f"{adapter.venue_id}: no candles")
            except Exception as exc:  # noqa: BLE001 — try the next exchange
                errors.append(f"{adapter.venue_id}: {(str(exc) or type(exc).__name__)[:120]}")
        if errors and not added:
            status["error"] = "; ".join(errors)
            logger.warning("candle_sync_failed", symbol=symbol, timeframe=timeframe, errors=status["error"])
        if added:
            await asyncio.to_thread(self._store.trim, symbol, timeframe, int(self._history[timeframe] * 1.2))
        n, first, last_ts, source = await asyncio.to_thread(self._store.info, symbol, timeframe)
        status.update(bars=n, first_ts=first, last_ts=last_ts, synced_at=now_ms)
        if source:
            status["source"] = source
        if added:
            for cb in self._listeners:
                try:
                    await cb(symbol, timeframe)
                except Exception as exc:  # noqa: BLE001 — a listener's failure never stops the sync
                    logger.error("candle_listener_failed", symbol=symbol, timeframe=timeframe, error=str(exc)[:160])
        return added
