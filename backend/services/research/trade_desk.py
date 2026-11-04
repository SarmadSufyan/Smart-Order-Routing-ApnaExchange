"""The trade desk (research R1): a scanner over every coin the exchanges share, and a time machine for a bracket.

What a day trader does before the session, done by the system every few minutes and shown with its evidence:

    1. the universe: every USDT spot coin that ALL the enabled exchanges' real markets list (read from their
       public market lists; 147 on 2026-10-25), the 20 traded coins first — or the trading universe only
    2. the candles: small histories per coin (daily, hourly, and 5-minute bars for the opening ranges) in the
       research store, kept current gently (one exchange at a time, a few requests a second)
    3. the setups (``scanner.py``): which coins show a pattern right now, with the numbers behind it
    4. the evidence per hit (``timemachine.py``): what happened every past time the same setup fired on the same
       coin with the same bracket, after costs — and, on request, a 10,000-replay time machine for the next bars
    5. the entry timer: waiting / triggered / expired against the live price
    6. the plan: stop, target, size (the research planner's rules), both kinds of odds, one verdict

Read-only: it holds no order entry and no adapter, and nothing here can place an order. A plan is a proposal
for a person (the copilot chains it in R3), with "no edge" said as plainly as "favourable".
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict

import numpy as np

from backend.shared.utils.logger import get_logger

from . import scanner as sc
from . import timemachine as tm
from .candles import CandleStore
from .indicators import atr
from .service import ResearchUnavailableError
from .sizing import BracketStats, plan_trade

logger = get_logger("trade_desk")

CANDLE_SOURCES = ("V1", "V4", "V5", "V7", "V6")  # a coin's candles come from the first exchange that lists it
HISTORY = {"1d": 400, "1h": 1_500, "5m": 288 * 2}  # bars kept per coin of the wider universe
MAIN_HISTORY_5M = 288 * 30  # the traded coins keep 30 days of 5-minute bars for the opening-range time machine
LOAD = {"1d": 3_000, "1h": 5_000, "5m": 288 * 30}  # how much of the store a scan reads per coin
TIME_LIMIT_BARS = {"1h": 24, "1d": 10, "5m": 48}  # the bracket's time limit by timeframe (a day, ten days, 4 hours)
EXPIRE_BARS = {"entry": {"1h": 2, "1d": 1, "5m": 3}, "watch": {"1h": 48, "1d": 10, "5m": 12}}
K_ATR = 1.0  # the scanner's bracket: stop 1 ATR below the entry, target 1.5 x that above it
RR = 1.5
MIN_FIRES = 20
UNIVERSE_REFRESH_S = 3_600.0
PAGE = 1_000


def parse_session_opens(text: str) -> tuple[int, ...]:
    """'00:00,08:00,13:30' -> minutes after midnight UTC."""
    opens = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        hh, mm = part.split(":")
        opens.append(int(hh) * 60 + int(mm))
    return tuple(sorted(set(opens))) or sc.DEFAULT_SESSION_OPENS_MIN


class TradeDeskService:
    def __init__(self, store: CandleStore, registry, aggregator, symbols: list[str], *, universe: str = "common",
                 max_coins: int = 200, interval_s: float = 300.0,
                 session_opens: tuple[int, ...] = sc.DEFAULT_SESSION_OPENS_MIN,
                 range_minutes: int = sc.DEFAULT_RANGE_MINUTES, window_minutes: int = sc.DEFAULT_WINDOW_MINUTES,
                 replays: int = tm.DEFAULT_REPLAYS, pool_days: int = 60, reference_fee_bps: float = 10.0,
                 slippage_bps: float = 2.0, risk_engine=None, enabled: bool = True, clock: Callable[[], float] = time.time,
                 pace_s: float = 0.05, concurrency: int = 3) -> None:
        self._store = store
        self._registry = registry
        self._aggregator = aggregator
        self._symbols = list(symbols)
        self._mode = universe
        self._max_coins = max_coins
        self._interval_s = interval_s
        self._session = {"opens_min": tuple(session_opens), "range_minutes": range_minutes,
                         "window_minutes": window_minutes}
        self._replays = replays
        self._pool_days = pool_days
        self._fee_bps = reference_fee_bps
        self._slippage_bps = slippage_bps
        self._risk = risk_engine
        self.enabled = enabled
        self._clock = clock
        self._pace_s = pace_s
        self._concurrency = concurrency
        self._measured: Callable[[str], tuple[float, int] | None] | None = None
        self._universe: list[str] = list(symbols)
        self._universe_source = "the trading universe"
        self._universe_at: float | None = None
        self._latest: dict | None = None
        self._history_cache: dict[tuple[str, str, str], tuple[int, tm.HistoryCheck]] = {}
        self._errors: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self.scanning = False
        self._scan_lock = asyncio.Lock()
        self._last_scan_s: float | None = None

    # ── costs ───────────────────────────────────────────────────────
    def set_cost_source(self, measured: Callable[[str], tuple[float, int] | None] | None) -> None:
        """Use the slippage measured on our own orders (the practice lab) instead of the assumption."""
        self._measured = measured

    def _cost_round_trip_bps(self, symbol: str) -> tuple[float, str]:
        m = self._measured(symbol) if self._measured is not None else None
        if m is not None:
            slip, source = max(m[0], 0.0), f"slippage measured on {m[1]} of our own orders"
        else:
            slip, source = self._slippage_bps, f"slippage assumed {self._slippage_bps:g} bps"
        return 2 * (self._fee_bps + slip), f"taker fee {self._fee_bps:g} bps each way, {source}"

    # ── lifecycle ───────────────────────────────────────────────────
    async def start(self) -> None:
        if not self.enabled:
            logger.info("trade_desk_disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="trade-desk")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        await asyncio.sleep(20)  # the exchanges' markets load first; the research sync has the main coins' bars
        while self._running:
            try:
                await self.scan_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed scan is retried at the next interval
                logger.error("trade_desk_scan_failed", error=str(exc)[:200])
            await asyncio.sleep(self._interval_s)

    # ── the universe ────────────────────────────────────────────────
    async def discover_universe(self) -> list[str]:
        """The USDT spot coins every real market lists, the traded coins first; or just the traded coins."""
        if self._mode != "common":
            self._universe, self._universe_source = list(self._symbols), "the trading universe"
            self._universe_at = self._clock()
            return self._universe
        lists: list[tuple[str, set[str]]] = []
        for adapter in self._registry.all():
            if getattr(adapter, "data_mode", None) != "mainnet":
                continue  # a sandbox's market list is not the market
            try:
                symbols = await asyncio.wait_for(adapter.data_markets(), timeout=45)
            except Exception as exc:  # noqa: BLE001 — one exchange's list missing: the others decide
                logger.warning("trade_desk_markets_failed", venue_id=adapter.venue_id, error=str(exc)[:160])
                continue
            if symbols:
                lists.append((adapter.name, set(symbols)))
        if len(lists) < 2:
            if self._universe_at is None:
                self._universe, self._universe_source = list(self._symbols), (
                    "the trading universe (fewer than two exchanges' market lists could be read)")
            self._universe_at = self._clock()
            return self._universe
        common = set.intersection(*(s for _, s in lists))
        others = sorted(common - set(self._symbols))
        self._universe = (list(self._symbols) + others)[: self._max_coins]
        names = ", ".join(n for n, _ in lists)
        self._universe_source = f"every USDT spot coin listed by {names} ({len(common)} coins)"
        self._universe_at = self._clock()
        logger.info("trade_desk_universe", coins=len(self._universe), common=len(common), exchanges=names)
        return self._universe

    @property
    def universe(self) -> list[str]:
        return list(self._universe)

    # ── candles ─────────────────────────────────────────────────────
    def _wanted(self, symbol: str) -> dict[str, int]:
        """Which (timeframe -> bars) this coin needs refreshed here; the research sync owns the main coins' 1h/1d."""
        if symbol in self._symbols:
            return {"5m": MAIN_HISTORY_5M}
        return dict(HISTORY)

    async def _refresh(self, symbol: str, tf: str, bars: int) -> bool:
        """Bring one (coin, timeframe) up to the last closed candle; returns whether candles are there."""
        tf_ms = sc.TF_MS[tf]
        now_ms = int(self._clock() * 1000)
        closed_before = now_ms - now_ms % tf_ms
        last = await asyncio.to_thread(self._store.last_ts, symbol, tf)
        since = last + tf_ms if last else closed_before - bars * tf_ms
        if since >= closed_before:
            return True
        errors = []
        for vid in CANDLE_SOURCES:
            adapter = self._registry.get(vid)
            if adapter is None:
                continue
            try:
                cursor, added = since, 0
                while cursor < closed_before:
                    candles = await asyncio.wait_for(
                        adapter.fetch_candles(symbol, tf, since_ms=cursor, limit=PAGE), timeout=30)
                    rows = [(c.ts_ms, c.open, c.high, c.low, c.close, c.volume) for c in candles
                            if cursor <= c.ts_ms < closed_before]
                    if not rows:
                        break
                    await asyncio.to_thread(self._store.upsert, symbol, tf, rows, adapter.venue_id)
                    added += len(rows)
                    cursor = rows[-1][0] + tf_ms
                    await asyncio.sleep(self._pace_s)
                if added:
                    if symbol not in self._symbols or tf == "5m":  # never trim the research's years of 1h/1d
                        await asyncio.to_thread(self._store.trim, symbol, tf, int(bars * 1.2))
                    self._errors.pop(f"{symbol} {tf}", None)
                    return True
            except Exception as exc:  # noqa: BLE001 — try the next exchange
                errors.append(f"{vid}: {(str(exc) or type(exc).__name__)[:100]}")
        if errors:
            self._errors[f"{symbol} {tf}"] = "; ".join(errors)
        return last is not None

    async def _prepare(self, symbol: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            for tf, bars in self._wanted(symbol).items():
                await self._refresh(symbol, tf, bars)
                await asyncio.sleep(self._pace_s)

    async def _bars(self, symbol: str) -> dict[str, dict]:
        out = {}
        for tf in ("1d", "1h", "5m"):
            data = await asyncio.to_thread(self._store.load, symbol, tf, LOAD[tf])
            if len(data["close"]) >= sc.MIN_BARS[tf]:
                out[tf] = data
        return out

    # ── the scan ────────────────────────────────────────────────────
    async def scan_now(self) -> dict:
        """Refresh the candles, run every setup on every coin, attach each hit's history; keep the result."""
        async with self._scan_lock:
            self.scanning = True
            started = time.monotonic()
            try:
                if self._universe_at is None or self._clock() - self._universe_at > UNIVERSE_REFRESH_S:
                    await self.discover_universe()
                sem = asyncio.Semaphore(self._concurrency)
                await asyncio.gather(*(self._prepare(s, sem) for s in self._universe), return_exceptions=True)
                hits: list[dict] = []
                coins_with_data = 0
                for symbol in self._universe:
                    bars_by_tf = await self._bars(symbol)
                    if not bars_by_tf:
                        continue
                    coins_with_data += 1
                    found = await asyncio.to_thread(sc.scan_symbol, symbol, bars_by_tf, None, 1, **self._session)
                    for hit in found:
                        hits.append(await self._enrich(hit, bars_by_tf[hit.timeframe]))
                hits.sort(key=lambda h: (-h["rank"], -h["dollar_volume"], h["symbol"]))
                now_ms = int(self._clock() * 1000)
                self._latest = {
                    "as_of_ts": now_ms, "took_s": round(time.monotonic() - started, 1),
                    "universe": {"coins": len(self._universe), "with_data": coins_with_data,
                                 "source": self._universe_source, "traded": list(self._symbols)},
                    "setups": [asdict(s) for s in sc.SETUPS.values()],
                    "bracket": {"k_atr": K_ATR, "rr": RR, "time_limit_bars": TIME_LIMIT_BARS},
                    "hits": hits, "errors": dict(list(self._errors.items())[:20]),
                    "note": ("A setup is a pattern, not a forecast. Each hit shows what happened every past time the "
                             "same setup fired on the same coin with this bracket, after fees. Nothing is sent."),
                }
                self._last_scan_s = self._clock()
                logger.info("trade_desk_scan", coins=len(self._universe), with_data=coins_with_data, hits=len(hits),
                            took_s=self._latest["took_s"])
                return self._latest
            finally:
                self.scanning = False

    async def _live_price(self, symbol: str) -> float | None:
        if symbol not in self._symbols:
            return None
        try:
            nbbo = await self._aggregator.compute_nbbo(symbol)
        except Exception:  # noqa: BLE001 — a missing live price just means "use the last close"
            return None
        if nbbo and nbbo.best_bid and nbbo.best_ask:
            return (nbbo.best_bid + nbbo.best_ask) / 2
        return None

    def _history_for(self, symbol: str, setup: str, tf: str, bars: dict) -> tm.HistoryCheck:
        """Every past fire of this setup on this coin, the scanner's bracket placed at each; cached per last bar."""
        key = (symbol, setup, tf)
        last_ts = int(bars["ts"][-1])
        hit = self._history_cache.get(key)
        if hit and hit[0] == last_ts:
            return hit[1]
        entries = np.where(sc.fires(setup, bars, tf, **self._session))[0]
        check = self._replay(symbol, setup, tf, bars, entries, K_ATR, RR)
        self._history_cache[key] = (last_ts, check)
        return check

    def _replay(self, symbol: str, setup: str, tf: str, bars: dict, entries, k_atr: float, rr: float,
                stop_rule: str = "atr") -> tm.HistoryCheck:
        h, lo, c = bars["high"], bars["low"], bars["close"]
        a = atr(h, lo, c)
        stop_dist = k_atr * a
        if stop_rule == "range_mid" and setup == "range_breakout":
            _, ranges = sc.opening_ranges(bars["ts"], h, lo, c, tf, **self._session)
            stop_dist = np.full(len(c), np.nan)
            for r in ranges:
                if r.fired_at is not None and r.high is not None:
                    stop_dist[r.fired_at] = c[r.fired_at] - (r.high + r.low) / 2
        cost_bps, _ = self._cost_round_trip_bps(symbol)
        with np.errstate(invalid="ignore", divide="ignore"):
            cost_r = np.where(stop_dist > 0, c * cost_bps / 1e4 / stop_dist, np.nan)
        return tm.replay_history(bars["ts"], h, lo, c, entries, stop_dist, rr * stop_dist,
                                 TIME_LIMIT_BARS[tf], cost_r)

    async def _enrich(self, hit: sc.Hit, bars: dict) -> dict:
        history = await asyncio.to_thread(self._history_for, hit.symbol, hit.setup, hit.timeframe, bars)
        h_verdict, h_text = tm.history_verdict(history, MIN_FIRES)
        live = await self._live_price(hit.symbol)
        now_ms = int(self._clock() * 1000)
        state, state_text = sc.entry_state(hit.kind, hit.trigger, live if live is not None else hit.price, hit.age_bars,
                                           EXPIRE_BARS[hit.kind][hit.timeframe],
                                           hit.details.get("window_end_ts"), now_ms)
        # Rank: proven history first (by expectancy), then unproven, then the ones history argues against.
        rank = {"positive": 2.0 + (history.expectancy_r or 0.0), "too_few": 1.0, "negative": 0.0}[h_verdict]
        return {
            **asdict(hit), "live_price": live, "state": state, "state_text": state_text,
            "history": asdict(history), "history_verdict": h_verdict, "history_text": h_text,
            "traded_here": hit.symbol in self._symbols, "rank": rank,
        }

    # ── the time machine and the plan ───────────────────────────────
    def _check_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol not in self._universe:
            raise ResearchUnavailableError(f"{symbol} is not in the trade desk's universe ({len(self._universe)} coins)")
        return symbol

    async def _load(self, symbol: str, tf: str) -> dict:
        if tf not in sc.TF_MS:
            raise ResearchUnavailableError(f"Unknown timeframe {tf}; one of {', '.join(sc.TF_MS)}")
        data = await asyncio.to_thread(self._store.load, symbol, tf, LOAD[tf])
        if len(data["close"]) < max(sc.MIN_BARS[tf], tm.MIN_POOL_BARS):
            raise ResearchUnavailableError(
                f"Only {len(data['close'])} {tf} candles of {symbol} so far — the scanner is still collecting them")
        return data

    async def time_machine(self, symbol: str, timeframe: str, entry: float, stop: float, target: float,
                           max_bars: int | None = None, replays: int | None = None) -> dict:
        """The odds of a long bracket over the next bars, replayed from the coin's own candles."""
        symbol = self._check_symbol(symbol)
        data = await self._load(symbol, timeframe)
        bars = max_bars or TIME_LIMIT_BARS[timeframe]
        cost_bps, cost_text = self._cost_round_trip_bps(symbol)
        cost_r = entry * cost_bps / 1e4 / (entry - stop) if entry > stop else 0.0
        # The pool: ``pool_days`` days of bars; daily bars get at least a year, since 60 of them is too thin a sample.
        pool_bars = self._pool_days * sc.BARS_PER_DAY[timeframe]
        if timeframe == "1d":
            pool_bars = max(pool_bars, 365)
        pool = await asyncio.to_thread(tm.build_pool, data["ts"], data["high"], data["low"], data["close"], timeframe,
                                       pool_bars)
        start_slot = tm.slot_of(int(data["ts"][-1]) + sc.TF_MS[timeframe], timeframe)
        odds = await asyncio.to_thread(tm.simulate, pool, start_slot, bars, entry, stop, target,
                                       replays or self._replays, tm.DEFAULT_SEED, tm.DEFAULT_JITTER, cost_r)
        return {"symbol": symbol, "timeframe": timeframe, "cost_bps_round_trip": cost_bps, "cost_text": cost_text,
                "pool": {"bars": pool.bars, "sessions": pool.sessions, "first_ts": pool.first_ts, "last_ts": pool.last_ts,
                         "days": self._pool_days},
                "odds": asdict(odds),
                "note": ("Each replay draws past bars of the same time of day, scaled onto the path so far; the stop is "
                         "checked before the target. Odds from this coin's own history, never a promise.")}

    async def plan(self, symbol: str, timeframe: str = "1h", setup: str | None = None, equity: float = 1_000.0,
                   risk_pct: float = 1.0, k_atr: float = K_ATR, rr: float = RR, max_bars: int | None = None,
                   stop_rule: str = "atr", replays: int | None = None) -> dict:
        """Stop, target, size and both kinds of odds for a long trade on a coin, optionally for a setup."""
        symbol = self._check_symbol(symbol)
        if setup is not None and setup not in sc.SETUPS:
            raise ResearchUnavailableError(f"Unknown setup {setup}; one of {', '.join(sc.SETUPS)}")
        if stop_rule not in ("atr", "range_mid"):
            raise ResearchUnavailableError("stop_rule is 'atr' or 'range_mid'")
        data = await self._load(symbol, timeframe)
        h, lo, c = data["high"], data["low"], data["close"]
        a = await asyncio.to_thread(atr, h, lo, c)
        atr_now = float(a[-1])
        if not atr_now > 0:
            raise ResearchUnavailableError("No volatility measurement yet for this coin")
        live = await self._live_price(symbol)
        entry = live if live is not None else float(c[-1])
        entry_source = "the live mid price" if live is not None else "the last closed candle"
        stop = entry - k_atr * atr_now
        stop_text = f"{k_atr:g} × ATR ({atr_now:,.6g}) below the entry"
        if stop_rule == "range_mid":
            if timeframe != "5m" or setup != "range_breakout":
                raise ResearchUnavailableError("The range-mid stop belongs to the opening-range breakout on 5m bars")
            _, ranges = sc.opening_ranges(data["ts"], h, lo, c, timeframe, **self._session)
            current = [r for r in ranges if r.high is not None][-1:] if ranges else []
            if not current:
                raise ResearchUnavailableError("No complete opening range in this session yet")
            mid = (current[0].high + current[0].low) / 2
            if mid >= entry:
                raise ResearchUnavailableError("The price is below the opening range's midpoint: no long bracket")
            stop, stop_text = mid, f"the opening range's midpoint ({mid:,.6g})"
        risk = entry - stop
        target = entry + rr * risk
        bars = max_bars or TIME_LIMIT_BARS[timeframe]
        cost_bps, cost_text = self._cost_round_trip_bps(symbol)
        cost_r = entry * cost_bps / 1e4 / risk
        # 1. what really happened every past time (the setup fired / at every bar)
        if setup:
            entries = np.where(sc.fires(setup, data, timeframe, **self._session))[0]
            history_label = f"every past time '{sc.SETUPS[setup].label}' fired on {symbol}"
        else:
            entries = np.arange(50, len(c) - 1)
            history_label = f"every past {timeframe} candle of {symbol}"
        history = await asyncio.to_thread(self._replay, symbol, setup or "", timeframe, data, entries, k_atr, rr,
                                          stop_rule)
        h_verdict, h_text = tm.history_verdict(history, MIN_FIRES)
        # 2. the time machine for the next bars
        tm_out = await self.time_machine(symbol, timeframe, entry, stop, target, bars, replays)
        odds = tm_out["odds"]
        # 3. size, with the research planner's rules (fixed-fractional risk, half-Kelly cap, the per-order limit)
        decided = history.wins + history.losses
        p = history.wins / decided if decided else None
        b = (rr - cost_r) / (1 + cost_r)
        kelly = (p - (1 - p) / b) if p is not None and b > 0 else None
        stats = BracketStats(history.samples, history.wins, history.losses, history.timeouts, history.win_rate,
                             history.expectancy_r, history.break_even_win_rate,
                             round(kelly, 4) if kelly is not None else None)
        max_notional = self._risk.limits.max_order_notional if self._risk is not None else None
        sized = plan_trade(entry, atr_now, equity, risk_pct, k_atr, rr, cost_bps, stats, max_notional, stop=stop)
        # 4. one verdict
        if h_verdict == "negative" or odds["verdict"] == "unfavourable":
            verdict, verdict_text = "skip", "History or the time machine argues against this bracket: skip, or change it"
        elif h_verdict == "positive" and odds["verdict"] == "favourable":
            verdict, verdict_text = "favourable", "Both the real history and the replays favour this bracket (past results, not a promise)"
        elif h_verdict == "positive":
            verdict, verdict_text = "mixed", "The real history favours it; the replays call it a coin flip"
        elif odds["verdict"] == "favourable":
            verdict, verdict_text = "mixed", "The replays favour it, but there is not enough real history to confirm"
        else:
            verdict, verdict_text = "coin_flip", "Nothing here says this bracket pays: a coin flip at best"
        capped = next((r.removeprefix("Size capped by ") for r in sized.reasons if r.startswith("Size capped by ")), None)
        reasons = [
            f"Entry {entry:,.6g} from {entry_source}; stop {stop_text}; target {rr:g} × the risk above the entry",
            f"Risking {risk_pct:g}% of {equity:,.2f} = {equity * risk_pct / 100:,.2f} if the stop is hit"
            + (f"; size capped by {capped}" if capped else ""),
            f"History, {history_label}: {h_text}",
            f"Time machine, {odds['replays']:,} replays of the next {bars} bars: {odds['verdict_text']}",
            f"Costs: {cost_text} ({cost_bps:g} bps round trip, {cost_r:.3f} R)",
        ]
        return {
            "symbol": symbol, "timeframe": timeframe, "setup": setup, "side": "BUY", "equity": equity,
            "risk_pct": risk_pct, "k_atr": k_atr, "rr": rr, "max_bars": bars, "stop_rule": stop_rule, "atr": atr_now,
            "entry_source": entry_source, "plan": asdict(sized), "history": asdict(history),
            "history_verdict": h_verdict, "history_text": h_text, "time_machine": tm_out,
            "verdict": verdict, "verdict_text": verdict_text, "reasons": reasons,
            "cost_bps_round_trip": cost_bps,
            "disclaimer": "A plan measured on past candles and replays of them, not financial advice. Nothing is sent.",
        }

    # ── the entry timer ─────────────────────────────────────────────
    async def entry(self, symbol: str, setup: str, timeframe: str) -> dict:
        """The live state of one hit from the latest scan: waiting / triggered / expired."""
        symbol = symbol.upper()
        if not self._latest:
            raise ResearchUnavailableError("No scan yet")
        for hit in self._latest["hits"]:
            if hit["symbol"] == symbol and hit["setup"] == setup and hit["timeframe"] == timeframe:
                live = await self._live_price(symbol)
                price = live if live is not None else hit["price"]
                now_ms = int(self._clock() * 1000)
                bars_since = (now_ms - hit["bar_ts"]) // sc.TF_MS[timeframe] - 1
                state, text = sc.entry_state(hit["kind"], hit["trigger"], price, max(hit["age_bars"], bars_since),
                                             EXPIRE_BARS[hit["kind"]][timeframe], hit["details"].get("window_end_ts"),
                                             now_ms)
                return {**hit, "live_price": live, "state": state, "state_text": text, "checked_ts": now_ms}
        raise ResearchUnavailableError(f"No live hit for {symbol} {setup} on {timeframe} in the latest scan")

    # ── status ──────────────────────────────────────────────────────
    def latest(self) -> dict | None:
        return self._latest

    def status(self) -> dict:
        now = self._clock()
        return {
            "enabled": self.enabled, "scanning": self.scanning,
            "universe": {"coins": len(self._universe), "source": self._universe_source,
                         "discovered_ts": int(self._universe_at * 1000) if self._universe_at else None},
            "last_scan_ts": int(self._last_scan_s * 1000) if self._last_scan_s else None,
            "next_scan_in_s": (max(0.0, round(self._last_scan_s + self._interval_s - now, 1))
                               if self._last_scan_s else None),
            "interval_s": self._interval_s, "hits": len(self._latest["hits"]) if self._latest else 0,
            "errors": dict(list(self._errors.items())[:20]), "setups": [asdict(s) for s in sc.SETUPS.values()],
            "session": {"opens_utc": [f"{m // 60:02d}:{m % 60:02d}" for m in self._session["opens_min"]],
                        "range_minutes": self._session["range_minutes"],
                        "entry_window_minutes": self._session["window_minutes"]},
            "time_machine": {"replays": self._replays, "days": self._pool_days},
        }
