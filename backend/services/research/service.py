"""Research service: forecasts (3 horizons x every coin), their track record, charts, backtests, trade plans.

Heavy numpy work runs in worker threads (``asyncio.to_thread``) so the event loop — order books,
orders — never waits for a model to train.

The live track record: each time a new candle closes, the service forecasts the NEXT one and
writes the forecast down (``CandleStore.log_forecast``); when that next candle closes, it scores
it. Nothing is re-scored or edited afterwards.
"""

import asyncio
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np

from backend.shared.utils.logger import get_logger

from . import backtest as bt
from . import techniques
from .candles import HISTORY_BARS, TIMEFRAME_MS, CandleStore, CandleSync
from .indicators import atr, bollinger, ewma_vol, log_returns, macd, rsi, sma
from .model import (
    WARMUP,
    LogisticModel,
    Scores,
    build_features,
    contributions,
    fit_logistic,
    next_up_labels,
    predict_proba,
    range_coverage,
    range_quantiles,
    score,
    select_l2,
    standardized_next_returns,
    usable_rows,
    walk_forward,
)
from .signals import book_imbalance, book_signal_text, depth_within, indicator_signals, microprice
from .sizing import bracket_stats, plan_trade

logger = get_logger("research")

HORIZON_LABEL = {"1m": "next minute", "1h": "next hour", "1d": "next day"}
BAR_WORD = {"1m": "minute", "1h": "hour", "1d": "day"}
MODEL_BARS = {"1m": 5_000, "1h": 5_000, "1d": 1_500}
RETRAIN_EVERY = {"1m": 60, "1h": 24, "1d": 7}  # new candles before the model is refitted
MIN_BARS = 300
FEATURE_TEXT = {
    "return_1": "last {bar}'s move", "return_3": "move over 3 {bar}s", "return_6": "move over 6 {bar}s",
    "return_12": "move over 12 {bar}s", "return_24": "move over 24 {bar}s", "rsi_14": "RSI",
    "macd_hist": "MACD momentum", "bollinger_b": "position in the Bollinger band",
    "dist_sma20": "distance from the 20-{bar} average", "dist_sma50": "distance from the 50-{bar} average",
    "vol_24": "recent volatility", "vol_ratio": "volatility vs normal", "volume_z": "volume vs normal",
    "volume_x_return": "volume behind the last move", "atr_pct": "typical range (ATR)",
    "clock_sin": "time of day/week", "clock_cos": "time of day/week",
}


class ResearchUnavailableError(Exception):
    """Not enough history yet (still downloading) — the message says what's missing."""


@dataclass
class ModelState:
    model: LogisticModel
    trained_last_ts: int
    trained_bars: int
    oos: Scores | None
    quantiles: dict[float, float]
    coverage80: float | None
    source: str | None


def _clean(x) -> list:
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 10) for v in x]


class ResearchService:
    def __init__(self, store: CandleStore, sync: CandleSync, collector, aggregator, symbols: list[str],
                 reference_fee_bps: float = 10.0, risk_engine=None, slippage_bps: float = 2.0) -> None:
        self._store = store
        self._sync = sync
        self._collector = collector
        self._aggregator = aggregator
        self._symbols = list(symbols)
        self._fee_bps = reference_fee_bps
        self._slippage_bps = slippage_bps
        self._risk = risk_engine
        # The practice lab's measured slippage (bps, orders) per coin, once it has enough of our own orders.
        self._measured: Callable[[str], tuple[float, int] | None] | None = None
        self._models: dict[tuple[str, str], ModelState] = {}
        self._studies: dict[tuple[str, str], tuple[tuple, dict]] = {}  # technique studies by their candles
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def set_cost_source(self, measured: Callable[[str], tuple[float, int] | None] | None) -> None:
        """Use the slippage measured on our own orders (the practice lab) instead of the assumption."""
        self._measured = measured

    def _slippage(self, symbol: str) -> tuple[float, str]:
        """(slippage bps per trade, where it comes from)."""
        m = self._measured(symbol) if self._measured is not None else None
        if m is not None:
            # A negative median (fills better than the mid) is real but not something to plan on.
            return max(m[0], 0.0), f"measured on {m[1]} of our own orders (median {m[0]:+.2f} bps vs the mid)"
        return self._slippage_bps, f"assumed {self._slippage_bps:g} bps (not enough of our own orders yet)"

    async def open_store(self) -> None:
        await asyncio.to_thread(self._store.open)

    def close_store(self) -> None:
        self._store.close()

    async def start(self) -> None:
        self._sync.on_new_candles(self._on_new_candles)
        await self._sync.start()
        self._warmup = asyncio.create_task(self._train_all(), name="research-warmup")

    async def stop(self) -> None:
        if getattr(self, "_warmup", None):
            self._warmup.cancel()
            await asyncio.gather(self._warmup, return_exceptions=True)
        await self._sync.stop()

    async def _train_all(self) -> None:
        """Once the history is in, train every (coin, horizon) so the scoreboard is complete from the start."""
        await self._sync.ready.wait()
        for symbol in self._symbols:
            for tf in TIMEFRAME_MS:
                try:
                    await self.model_for(symbol, tf)
                except ResearchUnavailableError:
                    continue
                except Exception as exc:  # noqa: BLE001 — one failed model never stops the others
                    logger.error("research_warmup_failed", symbol=symbol, timeframe=tf, error=str(exc)[:160])

    def _check_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol not in self._symbols:
            raise ResearchUnavailableError(f"Unknown coin {symbol}. Research covers: {', '.join(self._symbols)}")
        return symbol

    async def _load(self, symbol: str, tf: str, bars: int, minimum: int = MIN_BARS) -> dict[str, np.ndarray]:
        data = await asyncio.to_thread(self._store.load, symbol, tf, bars)
        if len(data["close"]) < minimum:
            raise ResearchUnavailableError(
                f"Only {len(data['close'])} {tf} candles of {symbol} so far (need {minimum}) — the history is "
                "still downloading; try again in a minute")
        return data

    # ── models ──────────────────────────────────────────────────────
    async def model_for(self, symbol: str, tf: str, data: dict | None = None) -> ModelState:
        """The trained model for (coin, horizon), refitted after RETRAIN_EVERY new candles."""
        key = (symbol, tf)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            data = data or await self._load(symbol, tf, MODEL_BARS[tf])
            last = int(data["ts"][-1])
            state = self._models.get(key)
            if state is None or (last - state.trained_last_ts) // TIMEFRAME_MS[tf] >= RETRAIN_EVERY[tf]:
                state = await asyncio.to_thread(self._train, data, tf)
                state.source = (await asyncio.to_thread(self._store.info, symbol, tf))[3]
                self._models[key] = state
                logger.info("research_model_trained", symbol=symbol, timeframe=tf, bars=state.trained_bars,
                            oos_accuracy=state.oos.accuracy if state.oos else None,
                            edge=state.oos.edge if state.oos else None)
            return state

    @staticmethod
    def _train(data: dict, tf: str) -> ModelState:
        X = build_features(data["open"], data["high"], data["low"], data["close"], data["volume"], data["ts"], tf)
        y = next_up_labels(data["close"])
        oos = score(walk_forward(X, y), y)
        rows = usable_rows(X, y)
        model = fit_logistic(X[rows], y[rows], l2=select_l2(X[rows], y[rows]))
        r = log_returns(data["close"])
        z = standardized_next_returns(r, ewma_vol(r))
        return ModelState(model=model, trained_last_ts=int(data["ts"][-1]), trained_bars=len(rows), oos=oos,
                          quantiles=range_quantiles(z), coverage80=range_coverage(z), source=None)

    @staticmethod
    def _predict(state: ModelState, data: dict, tf: str) -> dict:
        X = build_features(data["open"], data["high"], data["low"], data["close"], data["volume"], data["ts"], tf)
        x = X[-1]
        price = float(data["close"][-1])
        p = float(predict_proba(state.model, x)[0])
        r = log_returns(data["close"])
        sigma = float(ewma_vol(r)[-1])
        q = state.quantiles

        def band(lo: float, hi: float) -> list[float] | None:
            return [price * math.exp(q[lo] * sigma), price * math.exp(q[hi] * sigma)] if q else None

        # The clock is two features (sin, cos): show it as one driver.
        pushes: dict[str, float] = {}
        for name, push in contributions(state.model, x):
            key = "clock_sin" if name in ("clock_sin", "clock_cos") else name
            pushes[key] = pushes.get(key, 0.0) + push
        ranked = sorted(pushes.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        drivers = [{"feature": name, "text": FEATURE_TEXT[name].format(bar=BAR_WORD[tf]), "push": round(push, 4)}
                   for name, push in ranked]
        return {"price": price, "p_up": round(p, 4), "sigma": sigma, "range80": band(0.10, 0.90),
                "range50": band(0.25, 0.75), "drivers": drivers}

    # ── forecasts ───────────────────────────────────────────────────
    async def forecast(self, symbol: str) -> dict:
        """The forecast for the next minute, hour and day of one coin."""
        symbol = self._check_symbol(symbol)
        out = {"symbol": symbol, "horizons": [], "disclaimer": (
            "A probability, not a promise: no one can reliably predict crypto prices. The track record below "
            "shows how often these forecasts were right. Not financial advice.")}
        for tf in TIMEFRAME_MS:
            try:
                out["horizons"].append(await self._forecast_one(symbol, tf))
            except ResearchUnavailableError as exc:
                out["horizons"].append({"horizon": tf, "label": HORIZON_LABEL[tf], "unavailable": str(exc)})
        return out

    async def forecast_horizon(self, symbol: str, tf: str) -> dict:
        """The forecast for one horizon of one coin (bots and the practice lab use this)."""
        if tf not in TIMEFRAME_MS:
            raise ResearchUnavailableError(f"Unknown timeframe {tf}; one of {', '.join(TIMEFRAME_MS)}")
        return await self._forecast_one(self._check_symbol(symbol), tf)

    async def _forecast_one(self, symbol: str, tf: str) -> dict:
        data = await self._load(symbol, tf, MODEL_BARS[tf])
        state = await self.model_for(symbol, tf, data)
        pred = await asyncio.to_thread(self._predict, state, data, tf)
        tail = {k: v[-300:] for k, v in data.items()}
        signals = await asyncio.to_thread(indicator_signals, tail["high"], tail["low"], tail["close"],
                                          tail["volume"], BAR_WORD[tf])
        edge = bool(state.oos and state.oos.edge)
        p = pred["p_up"]
        lean = "up" if p > 0.5 else "down"
        oos = state.oos
        if oos is None:
            call = "Not enough history yet to test the model — treat this as about 50/50"
        elif not edge:
            side = "up" if oos.up_rate >= 0.5 else "down"
            acc, base = f"{oos.accuracy * 100:.1f}%", f"{oos.baseline_accuracy * 100:.1f}%"
            if oos.accuracy <= oos.baseline_accuracy:
                why = f"out of sample it was right no more often than always guessing '{side}' ({acc} vs {base})"
            elif oos.log_loss >= oos.log_loss_baseline:
                why = (f"right a little more often than always guessing '{side}' ({acc} vs {base}), but its "
                       "probabilities scored worse than simply quoting how often the price rises")
            else:
                why = f"it beat the baseline ({acc} vs {base}) but that could easily be luck (p = {oos.p_value:.2f})"
            call = f"No proven edge: {why} — treat this as about 50/50"
        else:
            call = (f"Leans {lean}: {max(p, 1 - p) * 100:.1f}% — out of sample it was right "
                    f"{oos.accuracy * 100:.1f}% of the time vs {oos.baseline_accuracy * 100:.1f}% for the baseline "
                    f"(unlikely to be luck: p = {oos.p_value:.3f}); a small edge, not a sure thing")
        live = await asyncio.to_thread(self._live_scores, symbol, tf)
        return {
            "horizon": tf, "label": HORIZON_LABEL[tf], "as_of_ts": int(data["ts"][-1]) + TIMEFRAME_MS[tf],
            "price": pred["price"], "p_up": p, "lean": lean, "edge": edge, "call": call,
            "range80": pred["range80"], "range50": pred["range50"],
            "typical_move_pct": round(pred["sigma"] * 100, 4), "drivers": pred["drivers"], "signals": signals,
            "model": {"out_of_sample": asdict(state.oos) if state.oos else None, "range_coverage80": state.coverage80,
                      "trained_bars": state.trained_bars, "trained_last_ts": state.trained_last_ts,
                      "source": state.source},
            "live": live,
        }

    # ── live log ────────────────────────────────────────────────────
    async def _on_new_candles(self, symbol: str, tf: str) -> None:
        """A candle closed: score forecasts it settles, then forecast the next one and write it down."""
        await asyncio.to_thread(self._score_due, symbol, tf)
        try:
            data = await self._load(symbol, tf, MODEL_BARS[tf])
        except ResearchUnavailableError:
            return
        state = await self.model_for(symbol, tf, data)
        pred = await asyncio.to_thread(self._predict, state, data, tf)
        r80 = pred["range80"] or [None, None]
        await asyncio.to_thread(self._store.log_forecast, symbol, tf, int(data["ts"][-1]), pred["price"],
                                pred["p_up"], r80[0], r80[1], bool(state.oos and state.oos.edge))

    def _score_due(self, symbol: str, tf: str) -> None:
        due = self._store.unscored(symbol, tf)
        if not due:
            return
        data = self._store.load(symbol, tf, MODEL_BARS[tf])
        index = {int(t): i for i, t in enumerate(data["ts"])}
        for issued_ts, price, low80, high80 in due:
            nxt = index.get(int(issued_ts) + TIMEFRAME_MS[tf])
            if nxt is None:
                continue
            actual = float(data["close"][nxt])
            in_range = None if low80 is None else bool(low80 <= actual <= high80)
            self._store.score_forecast(symbol, tf, issued_ts, actual, actual > price, in_range)

    def _live_scores(self, symbol: str, tf: str) -> dict:
        rows = self._store.scored(symbol, tf)
        if not rows:
            return {"n": 0}
        p = np.array([r[1] for r in rows])
        up = np.array([r[2] for r in rows], dtype=float)
        ranged = [r[3] for r in rows if r[3] is not None]
        edge_rows = [r for r in rows if r[4]]
        return {
            "n": len(rows), "hit_rate": round(float(np.mean((p > 0.5) == (up == 1))), 4),
            "up_rate": round(float(up.mean()), 4), "brier": round(float(np.mean((p - up) ** 2)), 5),
            "range_coverage80": round(sum(ranged) / len(ranged), 4) if ranged else None,
            "n_with_edge": len(edge_rows),
            "hit_rate_with_edge": (round(float(np.mean([(r[1] > 0.5) == (r[2] == 1) for r in edge_rows])), 4)
                                   if edge_rows else None),
            "since_ts": int(rows[-1][0]),
        }

    async def scoreboard(self) -> list[dict]:
        out = []
        for symbol in self._symbols:
            for tf in TIMEFRAME_MS:
                state = self._models.get((symbol, tf))
                out.append({
                    "symbol": symbol, "horizon": tf, "label": HORIZON_LABEL[tf],
                    "out_of_sample": asdict(state.oos) if state and state.oos else None,
                    "range_coverage80": state.coverage80 if state else None,
                    "live": await asyncio.to_thread(self._live_scores, symbol, tf),
                })
        return out

    def status(self) -> dict:
        return {"candles": self._sync.status(), "ready": self._sync.ready.is_set(),
                "models": [{"symbol": s, "horizon": tf, "trained_bars": m.trained_bars,
                            "trained_last_ts": m.trained_last_ts} for (s, tf), m in self._models.items()]}

    # ── charts ──────────────────────────────────────────────────────
    async def chart(self, symbol: str, tf: str, bars: int) -> dict:
        symbol = self._check_symbol(symbol)
        if tf not in TIMEFRAME_MS:
            raise ResearchUnavailableError(f"Unknown timeframe {tf}; one of {', '.join(TIMEFRAME_MS)}")
        bars = max(50, min(bars, 1_000))
        data = await self._load(symbol, tf, bars + 100, minimum=60)  # extra for the indicators' warm-up
        bars = min(bars, len(data["close"]))

        def build() -> dict:
            c = data["close"]
            mid, upper, lower, _ = bollinger(c)
            line, sig, hist = macd(c)
            cut = slice(-bars, None)
            return {
                "symbol": symbol, "timeframe": tf,
                "ts": [int(t) for t in data["ts"][cut]], "open": _clean(data["open"][cut]),
                "high": _clean(data["high"][cut]), "low": _clean(data["low"][cut]), "close": _clean(c[cut]),
                "volume": _clean(data["volume"][cut]),
                "sma20": _clean(sma(c, 20)[cut]), "sma50": _clean(sma(c, 50)[cut]),
                "bb_upper": _clean(upper[cut]), "bb_mid": _clean(mid[cut]), "bb_lower": _clean(lower[cut]),
                "rsi": _clean(rsi(c)[cut]), "macd": _clean(line[cut]), "macd_signal": _clean(sig[cut]),
                "macd_hist": _clean(hist[cut]), "atr": _clean(atr(data["high"], data["low"], c)[cut]),
            }

        return await asyncio.to_thread(build)

    # ── live order book ─────────────────────────────────────────────
    async def orderbook(self, symbol: str) -> dict:
        symbol = self._check_symbol(symbol)
        nbbo = await self._aggregator.compute_nbbo(symbol)
        if not nbbo or not nbbo.best_bid or not nbbo.best_ask:
            return {"symbol": symbol, "available": False, "text": "No live order book for this coin right now"}
        mid = (nbbo.best_bid + nbbo.best_ask) / 2
        bids, asks = [], []
        for vid in nbbo.venue_quotes:
            depth = self._collector.get_depth(vid, symbol, 50)
            if depth:
                bids.extend(depth["bids"])
                asks.extend(depth["asks"])
        imb10, imb25 = book_imbalance(bids, asks, mid, 10), book_imbalance(bids, asks, mid, 25)
        micro = microprice(nbbo.best_bid, nbbo.best_bid_size, nbbo.best_ask, nbbo.best_ask_size)
        micro_bps = (micro / mid - 1) * 1e4 if micro else None
        direction, text = book_signal_text(imb10, micro_bps)
        return {
            "symbol": symbol, "available": True, "mid": mid, "spread_bps": round((nbbo.best_ask - nbbo.best_bid) / mid * 1e4, 3),
            "imbalance_10bps": round(imb10, 4) if imb10 is not None else None,
            "imbalance_25bps": round(imb25, 4) if imb25 is not None else None,
            "microprice": micro, "microprice_vs_mid_bps": round(micro_bps, 3) if micro_bps is not None else None,
            "bid_depth_10bps_usdt": round(depth_within(bids, mid, 10), 2),
            "ask_depth_10bps_usdt": round(depth_within(asks, mid, 10), 2),
            "venues": sorted(nbbo.venue_quotes), "direction": direction, "text": text,
            "note": "Live only: no order-book history is stored, so the forecast model doesn't use it",
        }

    # ── backtests and trade plans ───────────────────────────────────
    async def backtest(self, symbol: str, tf: str, strategy: str, params: dict, bars: int, capital: float,
                       fee_bps: float | None, slippage_bps: float | None) -> dict:
        symbol = self._check_symbol(symbol)
        if strategy not in bt.STRATEGIES:
            raise ResearchUnavailableError(f"Unknown rule {strategy}; one of {', '.join(bt.STRATEGIES)}")
        data = await self._load(symbol, tf, max(MIN_BARS, min(bars, HISTORY_BARS[tf])))
        fee = self._fee_bps if fee_bps is None else fee_bps
        slip, slip_source = self._slippage(symbol) if slippage_bps is None else (slippage_bps, "set by you")

        def run() -> dict:
            probs, start = None, 0
            if strategy == "model":
                X = build_features(data["open"], data["high"], data["low"], data["close"], data["volume"],
                                   data["ts"], tf)
                probs = walk_forward(X, next_up_labels(data["close"]))
                valid = np.where(~np.isnan(probs))[0]
                if len(valid) == 0:
                    raise ResearchUnavailableError("Not enough history for an out-of-sample model backtest")
                start = int(valid[0]) + 1  # trade only where every forecast was made without seeing the outcome
            desired = bt.positions(strategy, params, data["close"], probs)
            res = bt.run_backtest(data["ts"], data["open"], data["close"], desired, fee, slip, capital, tf, start)
            return {"symbol": symbol, "timeframe": tf, "strategy": strategy, "params": params, "fee_bps": fee,
                    "slippage_bps": slip, "slippage_source": slip_source, "capital": capital, **asdict(res),
                    "note": ("Long only (spot). Decisions at each close, trades at the next open, fees and slippage "
                             "on every trade. Past results don't predict future ones.")}

        return await asyncio.to_thread(run)

    async def techniques(self, symbol: str, tf: str) -> dict:
        """Every classic technique on this coin's whole stored history: what worked, what each says now."""
        symbol = self._check_symbol(symbol)
        if tf not in TIMEFRAME_MS:
            raise ResearchUnavailableError(f"Unknown timeframe {tf}; one of {', '.join(TIMEFRAME_MS)}")
        data = await self._load(symbol, tf, HISTORY_BARS[tf], minimum=techniques.WARMUP + 100)
        key = (symbol, tf, int(data["ts"][-1]), len(data["ts"]))
        if (hit := self._studies.get((symbol, tf))) and hit[0] == key:
            return hit[1]  # same candles as last time: the study can't have changed
        slip, slip_source = self._slippage(symbol)
        out = await asyncio.to_thread(techniques.study, data["ts"], data["open"], data["close"], tf,
                                      self._fee_bps, slip)
        out = {"symbol": symbol, "timeframe": tf, "fee_bps": self._fee_bps, "slippage_bps": slip,
               "slippage_source": slip_source, **out}
        self._studies[(symbol, tf)] = (key, out)
        return out

    async def trade_plan(self, symbol: str, tf: str, equity: float, risk_pct: float, k_atr: float, rr: float,
                         max_bars: int) -> dict:
        symbol = self._check_symbol(symbol)
        data = await self._load(symbol, tf, MODEL_BARS[tf])
        nbbo = await self._aggregator.compute_nbbo(symbol)
        entry = nbbo.best_ask if nbbo and nbbo.best_ask else float(data["close"][-1])  # a buy pays the ask
        slip, slip_source = self._slippage(symbol)
        cost = 2 * (self._fee_bps + slip)
        max_notional = self._risk.limits.max_order_notional if self._risk is not None else None

        def run() -> dict:
            a = atr(data["high"], data["low"], data["close"])
            stats = bracket_stats(data["high"], data["low"], data["close"], a, k_atr, rr, max_bars, cost,
                                  start=WARMUP)
            plan = plan_trade(entry, float(a[-1]), equity, risk_pct, k_atr, rr, cost, stats, max_notional)
            return {"symbol": symbol, "timeframe": tf, "side": "BUY", "equity": equity, "risk_pct": risk_pct,
                    "k_atr": k_atr, "rr": rr, "max_bars": max_bars, "atr": float(a[-1]),
                    "plan": asdict(plan), "history": asdict(stats), "cost_bps_round_trip": cost,
                    "slippage_source": slip_source,
                    "disclaimer": "A plan measured on past candles, not financial advice. Nothing is sent."}

        out = await asyncio.to_thread(run)
        try:
            fc = await self._forecast_one(symbol, tf)
            out["forecast"] = {"p_up": fc["p_up"], "edge": fc["edge"], "call": fc["call"]}
        except ResearchUnavailableError:
            out["forecast"] = None
        return out
