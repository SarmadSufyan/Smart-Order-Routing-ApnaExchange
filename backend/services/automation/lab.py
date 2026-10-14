"""The practice lab: learning from our own trades, not from the internet.

Every bot order and every lot a bot opens is written down as a sample (in the audit
database, so testnet and real-money samples never mix):

    EXECUTION      one child order: the mid when we sent it, the price we got, the fee,
                   how much filled  ->  measured trading costs per exchange and coin
    CYCLE_LOT      one profit-cycle lot: market conditions at the buy (forecasts, RSI,
                   volatility, spread, order-book imbalance, time) -> how long it took to
                   sell and what it made after real-market fees
    ADVISOR_TRADE  one advisor trade: conditions + the forecast it acted on -> target,
                   stop or timeout, and the return after real-market fees

From them:
    * costs: the slippage we really pay, replacing the backtester's assumed 2 bps once
      there are enough of our own orders (``measured_slippage``);
    * a scoreboard per variant (each bot's label) with bootstrap confidence intervals, and
      a comparison of variants on the same coin by profit per day (a permutation test);
    * outcome models — "will this lot sell within 24 h?", "will this advisor trade hit its
      target?" — trained on the samples with the research module's walk-forward method and
      scored against the baseline; reported only once there are enough samples (200).

The statistics make no distribution assumptions (bootstrap / reshuffling with a fixed
seed), so they are easy to explain and the same on every machine. Honest limits: fills on
a test exchange are not fills on the real market (Bybit's demo tracks it closely, Binance's
spot testnet book is its own); every number says how many samples it rests on.
"""

import asyncio
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from backend.shared.models.automation import (
    BotState,
    LabPreset,
    LabReport,
    LabSample,
    LabSampleKind,
    Lot,
    LotStatus,
)
from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import Order, OrderSide
from backend.shared.utils.logger import get_logger

logger = get_logger("practice_lab")

MIN_COST_SAMPLES = 20  # filled orders before measured slippage replaces the assumption
MIN_MODEL_SAMPLES = 200  # labelled samples before an outcome model is trained
MIN_DAYS_TO_COMPARE = 3
CYCLE_HORIZON = timedelta(hours=24)
SAMPLES_KEPT = 50_000
RNG_SEED = 20261008
RESAMPLES = 2_000
FEATURE_TTL_S = 60.0

CYCLE_FEATURES = ["p_up_1h", "p_up_1d", "rsi_1h", "atr_pct_1h", "sigma_bps", "spread_bps", "imbalance_10bps",
                  "ret_1h", "ret_24h", "hour_sin", "hour_cos", "threshold_pct"]
ADVISOR_FEATURES = [f for f in CYCLE_FEATURES if f != "threshold_pct"]

PRESETS: dict[str, dict[str, Any]] = {
    "cycle_thresholds": {
        "title": "Profit-cycle threshold test",
        "description": ("Six profit-cycle bots side by side — BTC and ETH, each at 0.1%, 0.3% and 1% over fees — "
                        "$200 each, $20 per buy. Which threshold earns most per day in practice?"),
        "bots": [{"kind": "CYCLE", "symbol": s, "threshold_pct": t, "budget_usdt": 200.0, "buy_usdt": 20.0}
                 for s in ("BTC/USDT", "ETH/USDT") for t in (0.1, 0.3, 1.0)],
    },
    "advisor_explore": {
        "title": "Advisor: explore",
        "description": ("The advisor on auto-approve with a loose rule (P(up) ≥ 50%, any bracket history), all five "
                        "coins, $300, $20 per trade: collects outcomes to learn whether its forecasts help in practice."),
        "bots": [{"kind": "ADVISOR", "budget_usdt": 300.0, "trade_usdt": 20.0, "min_probability": 0.5,
                  "min_expectancy_r": -1.0, "max_open": 5, "max_trades_per_day": 24, "mode": "AUTO"}],
    },
    "advisor_strict": {
        "title": "Advisor: strict",
        "description": ("The advisor on auto-approve with its normal rule (P(up) ≥ 55% and a bracket that made money "
                        "on past candles after costs), $200, $20 per trade."),
        "bots": [{"kind": "ADVISOR", "budget_usdt": 200.0, "trade_usdt": 20.0, "mode": "AUTO"}],
    },
}


# ── statistics (pure) ───────────────────────────────────────────────
def bootstrap_mean_ci(values: list[float], level: float = 0.95) -> tuple[float, float, float] | None:
    """(mean, low, high) by resampling — None below 5 values."""
    if len(values) < 5:
        return None
    x = np.asarray(values, dtype=float)
    rng = np.random.default_rng(RNG_SEED)
    means = x[rng.integers(0, len(x), size=(RESAMPLES, len(x)))].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - level) / 2, 1 - (1 - level) / 2])
    return float(x.mean()), float(lo), float(hi)


def permutation_p_value(a: list[float], b: list[float]) -> float | None:
    """Two-sided: how often reshuffling the labels gives a difference in means at least this big."""
    if len(a) < 2 or len(b) < 2:
        return None
    x = np.asarray(a + b, dtype=float)
    observed = abs(np.mean(a) - np.mean(b))
    rng = np.random.default_rng(RNG_SEED)
    hits = 0
    for _ in range(RESAMPLES):
        rng.shuffle(x)
        if abs(x[:len(a)].mean() - x[len(a):].mean()) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (RESAMPLES + 1)


def daily_sums(points: list[tuple[datetime, float]], first_day: datetime, last_day: datetime) -> list[float]:
    """Sum per UTC day from ``first_day`` to ``last_day`` (days without a close count as 0)."""
    days = defaultdict(float)
    for ts, v in points:
        days[ts.date()] += v
    out, d = [], first_day.date()
    while d <= last_day.date():
        out.append(days.get(d, 0.0))
        d += timedelta(days=1)
    return out


def execution_sample(bot_id: str, variant: str, order: Order, mid_at_send: float | None, market_fee_bps: dict,
                     now: datetime) -> list[LabSample]:
    """One EXECUTION sample per child that reached an exchange."""
    base, quote = split_symbol(order.symbol)
    out = []
    sign = 1 if order.side == OrderSide.BUY else -1
    for c in order.child_orders:
        if c.sent_at is None:
            continue
        filled = c.filled_quantity
        outcome: dict[str, Any] = {"requested_qty": c.quantity, "filled_qty": filled,
                                   "fill_ratio": round(filled / c.quantity, 6) if c.quantity else None,
                                   "reject_reason": c.reject_reason}
        if filled > 0 and c.fill_price > 0:
            notional = filled * c.fill_price
            fee = c.fee or 0.0
            charged = fee if c.fee_asset == quote else fee * c.fill_price if c.fee_asset == base else None
            outcome.update({
                "fill_price": c.fill_price, "notional_usdt": round(notional, 8),
                "slippage_bps": round(sign * (c.fill_price / mid_at_send - 1) * 1e4, 4) if mid_at_send else None,
                "vs_plan_bps": round(sign * (c.fill_price / c.price - 1) * 1e4, 4) if c.price else None,
                "fee_bps": round(charged / notional * 1e4, 4) if charged is not None and notional else None,
                "market_fee_bps": market_fee_bps.get(c.venue_id),
            })
        out.append(LabSample(
            id=f"x-{c.id}", kind=LabSampleKind.EXECUTION, bot_id=bot_id, variant=variant, symbol=order.symbol,
            venue_id=c.venue_id, opened_at=c.sent_at or now, closed_at=c.filled_at or now,
            features={"side": order.side.value, "mid_at_send": mid_at_send, "order_type": order.order_type.value,
                      "limit_price": c.limit_price, "expected_price": c.price, "order_id": str(order.id)},
            outcome=outcome,
        ))
    return out


def lot_sample(bot: BotState, lot: Lot, now: datetime) -> LabSample:
    kind = LabSampleKind.CYCLE_LOT if bot.cycle is not None else LabSampleKind.ADVISOR_TRADE
    features = dict(lot.features)
    if bot.cycle is not None:
        features.update(threshold_pct=bot.cycle.threshold_pct, buy_usdt=bot.cycle.buy_usdt,
                        budget_usdt=bot.cycle.budget_usdt, spacing_pct=bot.cycle.spacing_pct)
    else:
        features.update(budget_usdt=bot.advisor.budget_usdt, stop=lot.stop_price, target=lot.target_price)
    outcome: dict[str, Any] = {"status": lot.status.value, "buy_price": lot.buy_price, "cost_usdt": lot.cost_usdt,
                               "market_cost_usdt": lot.market_cost_usdt, "target_price": lot.target_price}
    if lot.status != LotStatus.OPEN and lot.closed_at is not None:
        hold = (lot.closed_at - lot.opened_at).total_seconds()
        ret = (lot.profit_market_usdt or 0.0) / lot.sold_market_cost_usdt if lot.sold_market_cost_usdt else None
        outcome.update({"exit_reason": lot.exit_reason, "hold_s": round(hold, 1),
                        "profit_usdt": lot.profit_usdt, "profit_market_usdt": lot.profit_market_usdt,
                        "return_market_pct": round(ret * 100, 5) if ret is not None else None,
                        "win": lot.exit_reason == "target"})
    return LabSample(id=f"lot-{bot.id}-{lot.id}", kind=kind, bot_id=bot.id, variant=bot.label, symbol=lot.symbol,
                     venue_id=",".join(lot.venues) or None, opened_at=lot.opened_at, closed_at=lot.closed_at,
                     features=features, outcome=outcome)


def cost_table(samples: list[LabSample]) -> list[dict]:
    """Per exchange and coin: how our orders really filled."""
    groups: dict[tuple[str, str], list[LabSample]] = defaultdict(list)
    for s in samples:
        if s.kind == LabSampleKind.EXECUTION:
            groups[(s.venue_id or "?", s.symbol)].append(s)
    rows = []
    for (venue, symbol), group in sorted(groups.items()):
        requested = sum(s.outcome.get("requested_qty") or 0 for s in group)
        filled = [s for s in group if (s.outcome.get("filled_qty") or 0) > 0]
        slip = [s.outcome["slippage_bps"] for s in filled if s.outcome.get("slippage_bps") is not None]
        fees = [s.outcome["fee_bps"] for s in filled if s.outcome.get("fee_bps") is not None]
        rows.append({
            "venue_id": venue, "symbol": symbol, "orders": len(group), "filled": len(filled),
            "fill_ratio": round(sum(s.outcome.get("filled_qty") or 0 for s in group) / requested, 4) if requested else None,
            "slippage_bps_median": round(float(np.median(slip)), 3) if slip else None,
            "slippage_bps_mean": round(float(np.mean(slip)), 3) if slip else None,
            "slippage_bps_p90": round(float(np.quantile(slip, 0.9)), 3) if len(slip) >= 10 else None,
            "fee_bps_charged": round(float(np.median(fees)), 3) if fees else None,
            "market_fee_bps": next((s.outcome.get("market_fee_bps") for s in filled
                                    if s.outcome.get("market_fee_bps") is not None), None),
        })
    return rows


def _feature_row(features: dict, names: list[str]) -> list[float]:
    return [float(features[n]) if features.get(n) is not None else math.nan for n in names]


def outcome_model(samples: list[LabSample], kind: LabSampleKind, now: datetime) -> dict:
    """Train + walk-forward-score a model of this kind's outcome, once there are enough samples."""
    from backend.services.research.model import score, usable_rows, walk_forward

    if kind == LabSampleKind.CYCLE_LOT:
        names, question = CYCLE_FEATURES, "Will a profit-cycle lot sell within 24 hours of its buy?"
        rows = []
        for s in samples:
            if s.kind != kind:
                continue
            if s.closed_at is not None and s.outcome.get("exit_reason") == "target":
                rows.append((s.opened_at, s.features, float(s.closed_at - s.opened_at <= CYCLE_HORIZON)))
            elif now - s.opened_at >= CYCLE_HORIZON:
                rows.append((s.opened_at, s.features, 0.0))
    else:
        names, question = ADVISOR_FEATURES, "Will an advisor trade reach its target (before its stop or time limit)?"
        rows = [(s.opened_at, s.features, float(bool(s.outcome.get("win")))) for s in samples
                if s.kind == kind and s.closed_at is not None and s.outcome.get("exit_reason") in ("target", "stop", "max hold")]
    rows.sort(key=lambda r: r[0])
    out: dict[str, Any] = {"kind": kind.value, "question": question, "labelled": len(rows), "needed": MIN_MODEL_SAMPLES,
                           "features": names, "scores": None, "edge": False}
    if rows:
        out["base_rate"] = round(sum(r[2] for r in rows) / len(rows), 4)
    if len(rows) < MIN_MODEL_SAMPLES:
        out["status"] = f"collecting: {len(rows)} of {MIN_MODEL_SAMPLES} labelled samples"
        return out
    X = np.array([_feature_row(r[1], names) for r in rows])
    y = np.array([r[2] for r in rows])
    usable = len(usable_rows(X, y))
    if usable < MIN_MODEL_SAMPLES:
        out["status"] = f"collecting: {usable} complete samples of {MIN_MODEL_SAMPLES} (some lack features)"
        return out
    sc = score(walk_forward(X, y), y)
    if sc is None:
        out["status"] = "not enough out-of-sample predictions yet"
        return out
    out["scores"] = {k: getattr(sc, k) for k in ("n", "accuracy", "baseline_accuracy", "brier_skill", "p_value")}
    out["edge"] = sc.edge
    out["status"] = (f"beats the baseline out of sample (p = {sc.p_value:.3f})" if sc.edge else
                     f"no proven edge yet: {sc.accuracy * 100:.1f}% vs {sc.baseline_accuracy * 100:.1f}% for the "
                     f"baseline (p = {sc.p_value:.2f})")
    return out


# ── the service ─────────────────────────────────────────────────────
class PracticeLab:
    def __init__(self, store, research, volatility, aggregator, mode: str, market_fee_bps: dict[str, float]) -> None:
        self._store = store
        self._research = research
        self._volatility = volatility
        self._aggregator = aggregator
        self._mode = mode
        self._fees = dict(market_fee_bps)
        self._samples: dict[str, LabSample] = {}
        self._feature_cache: dict[str, tuple[float, dict]] = {}
        self._models: tuple[float, list[dict]] | None = None

    @property
    def enabled(self) -> bool:
        """Lab experiments (presets, lab bots) run on testnet only; samples are kept in every mode."""
        return self._mode == "testnet"

    async def load(self) -> None:
        if self._store is None:
            return
        for s in await self._store.load_lab_samples(SAMPLES_KEPT):
            self._samples[s.id] = s
        logger.info("lab_samples_restored", samples=len(self._samples))

    def samples(self, kind: LabSampleKind | None = None) -> list[LabSample]:
        return [s for s in self._samples.values() if kind is None or s.kind == kind]

    def _save(self, sample: LabSample) -> None:
        self._samples[sample.id] = sample
        if len(self._samples) > SAMPLES_KEPT:
            for key in sorted(self._samples, key=lambda k: self._samples[k].opened_at)[:len(self._samples) - SAMPLES_KEPT]:
                del self._samples[key]
        if self._store is not None:
            self._store.upsert_lab_sample(sample)

    # ── recording ───────────────────────────────────────────────────
    def record_execution(self, bot: BotState, order: Order, mid_at_send: float | None) -> None:
        for sample in execution_sample(bot.id, bot.label, order, mid_at_send, self._fees, datetime.now(UTC)):
            self._save(sample)

    def record_lot(self, bot: BotState, lot: Lot) -> None:
        self._save(lot_sample(bot, lot, datetime.now(UTC)))

    async def features(self, symbol: str) -> dict:
        """Market conditions right now, for a lot's sample (cached for a minute per coin)."""
        loop = asyncio.get_running_loop()
        cached = self._feature_cache.get(symbol)
        if cached and loop.time() - cached[0] < FEATURE_TTL_S:
            return dict(cached[1])
        now = datetime.now(UTC)
        hour = now.hour + now.minute / 60
        f: dict[str, Any] = {"hour_utc": now.hour, "dow": now.weekday(),
                             "hour_sin": round(math.sin(2 * math.pi * hour / 24), 6),
                             "hour_cos": round(math.cos(2 * math.pi * hour / 24), 6)}
        if self._volatility is not None and (s := self._volatility.sigma(symbol)) is not None:
            f["sigma_bps"] = round(s * 1e4, 4)
        if self._research is not None:
            for tf in ("1h", "1d"):
                try:
                    fc = await self._research.forecast_horizon(symbol, tf)
                    f[f"p_up_{tf}"] = fc.get("p_up")
                    f[f"edge_{tf}"] = fc.get("edge")
                    if tf == "1h":
                        f["atr_pct_1h"] = fc.get("typical_move_pct")
                except Exception as exc:  # noqa: BLE001 — a sample without a forecast is still a sample
                    logger.info("lab_forecast_unavailable", symbol=symbol, timeframe=tf, error=str(exc)[:120])
            try:
                chart = await self._research.chart(symbol, "1h", 60)
                closes = [c for c in chart["close"] if c is not None]
                rsi = [r for r in chart["rsi"] if r is not None]
                if rsi:
                    f["rsi_1h"] = rsi[-1]
                if len(closes) >= 25:
                    f["ret_1h"] = round((closes[-1] / closes[-2] - 1) * 100, 5)
                    f["ret_24h"] = round((closes[-1] / closes[-25] - 1) * 100, 5)
            except Exception as exc:  # noqa: BLE001
                logger.info("lab_chart_unavailable", symbol=symbol, error=str(exc)[:120])
            try:
                book = await self._research.orderbook(symbol)
                if book.get("available"):
                    f["spread_bps"] = book.get("spread_bps")
                    f["imbalance_10bps"] = book.get("imbalance_10bps")
                    f["microprice_vs_mid_bps"] = book.get("microprice_vs_mid_bps")
            except Exception as exc:  # noqa: BLE001
                logger.info("lab_book_unavailable", symbol=symbol, error=str(exc)[:120])
        self._feature_cache[symbol] = (loop.time(), f)
        return dict(f)

    # ── what the lab tells the rest of ApnaSOR ──────────────────────
    def measured_slippage(self, symbol: str) -> tuple[float, int] | None:
        """Median slippage (bps vs the mid, + = cost) of our filled orders on this coin, once there are enough."""
        slip = [s.outcome["slippage_bps"] for s in self.samples(LabSampleKind.EXECUTION)
                if s.symbol == symbol and s.outcome.get("slippage_bps") is not None]
        if len(slip) < MIN_COST_SAMPLES:
            return None
        return float(np.median(slip)), len(slip)

    def measured_costs(self, symbol: str) -> dict:
        m = self.measured_slippage(symbol)
        return {"n": m[1], "slippage_bps": round(m[0], 3)} if m else {"n": 0}

    def similar_advisor(self, p_up: float) -> dict:
        """How earlier advisor trades with a P(up) in the same 5-point bucket ended."""
        lo = math.floor(p_up * 20) / 20
        done = [s for s in self.samples(LabSampleKind.ADVISOR_TRADE)
                if s.closed_at is not None and s.features.get("p_up_1h") is not None
                and lo <= s.features["p_up_1h"] < lo + 0.05 and s.outcome.get("return_market_pct") is not None]
        if not done:
            return {"n": 0, "bucket": f"{lo * 100:.0f}–{(lo + 0.05) * 100:.0f}%"}
        return {"n": len(done), "bucket": f"{lo * 100:.0f}–{(lo + 0.05) * 100:.0f}%",
                "win_rate": sum(1 for s in done if s.outcome.get("win")) / len(done),
                "avg_return_pct": float(np.mean([s.outcome["return_market_pct"] for s in done]))}

    # ── the report ──────────────────────────────────────────────────
    def presets(self) -> list[LabPreset]:
        return [LabPreset(name=name, title=p["title"], description=p["description"],
                          budget_usdt=sum(b["budget_usdt"] for b in p["bots"])) for name, p in PRESETS.items()]

    async def report(self, bots: dict[str, BotState]) -> LabReport:
        now = datetime.now(UTC)
        loop = asyncio.get_running_loop()
        samples = list(self._samples.values())  # a snapshot: bots keep adding samples meanwhile
        running = {b.id: b.status.value == "RUNNING" for b in bots.values()}
        if self._models is None or loop.time() - self._models[0] > 300:
            models = await asyncio.to_thread(lambda: [outcome_model(samples, k, now) for k in
                                                      (LabSampleKind.CYCLE_LOT, LabSampleKind.ADVISOR_TRADE)])
            self._models = (loop.time(), models)
        scoreboard, comparisons = await asyncio.to_thread(cycle_scoreboard, samples, running, now)
        advisor = await asyncio.to_thread(advisor_stats, samples)
        counts: dict[str, int] = defaultdict(int)
        for s in samples:
            counts[s.kind.value] += 1
        return LabReport(
            mode=self._mode, enabled=self.enabled,
            note=("Everything here comes from our own bots' orders in the exchanges' sandbox environments — sandbox "
                  "balances, the exchanges' own order handling. Profits are shown at real-market fees. Sandbox fills are "
                  "not real-market fills (Bybit's sandbox tracks the market closely; Binance's has its own book), so "
                  "treat costs as indicative. Each number says how many samples it rests on."
                  if self._mode == "testnet" else
                  "Real-money samples. Lab experiments (presets) run in the sandbox only."),
            samples=dict(counts), costs=cost_table(samples), scoreboard=scoreboard, advisor=advisor,
            comparisons=comparisons, models=self._models[1], presets=self.presets(),
        )


def cycle_scoreboard(samples: list[LabSample], running: dict[str, bool],
                     now: datetime) -> tuple[list[dict], list[dict]]:
    """Profit-cycle variants, and comparisons of variants on the same coin."""
    groups: dict[str, list[LabSample]] = defaultdict(list)
    for s in samples:
        if s.kind == LabSampleKind.CYCLE_LOT:
            groups[s.variant].append(s)
    rows, daily = [], {}
    for variant, group in sorted(groups.items()):
        closed = [s for s in group if s.closed_at is not None and s.outcome.get("profit_market_usdt") is not None]
        first = min(s.opened_at for s in group)
        days = max((now - first).total_seconds() / 86_400, 1 / 24)
        profit = sum(s.outcome["profit_market_usdt"] for s in closed)
        budget = max((s.features.get("budget_usdt") or 0) for s in group) or None
        holds = [s.outcome["hold_s"] / 3600 for s in closed if s.outcome.get("hold_s") is not None]
        old = [s for s in group if now - s.opened_at >= CYCLE_HORIZON]
        fast = [s for s in old if s.closed_at is not None and s.closed_at - s.opened_at <= CYCLE_HORIZON]
        ci = bootstrap_mean_ci([s.outcome["return_market_pct"] for s in closed
                                if s.outcome.get("return_market_pct") is not None])
        rows.append({
            "variant": variant, "symbol": group[0].symbol, "bots": sorted({s.bot_id for s in group}),
            "running": any(running.get(bid, False) for bid in {s.bot_id for s in group}),
            "threshold_pct": group[0].features.get("threshold_pct"), "lots": len(group), "closed": len(closed),
            "open": len(group) - len(closed), "days": round(days, 3),
            "profit_market_usdt": round(profit, 6), "profit_per_day_usdt": round(profit / days, 6),
            "return_per_day_pct": round(profit / days / budget * 100, 5) if budget else None,
            "return_per_lot_pct": {"mean": round(ci[0], 5), "low": round(ci[1], 5), "high": round(ci[2], 5)} if ci else None,
            "median_hold_h": round(float(np.median(holds)), 3) if holds else None,
            "sold_within_24h": round(len(fast) / len(old), 4) if old else None, "lots_older_than_24h": len(old),
        })
        daily[variant] = (group[0].symbol, daily_sums(
            [(s.closed_at, s.outcome["profit_market_usdt"]) for s in closed], first, now), first)
    comparisons = []
    by_symbol: dict[str, list[str]] = defaultdict(list)
    for variant, (symbol, _, _) in daily.items():
        by_symbol[symbol].append(variant)
    for symbol, variants in sorted(by_symbol.items()):
        for i, a in enumerate(variants):
            for b in variants[i + 1:]:
                da, db = daily[a][1], daily[b][1]
                row = {"symbol": symbol, "a": a, "b": b, "days_a": len(da), "days_b": len(db)}
                if min(len(da), len(db)) < MIN_DAYS_TO_COMPARE:
                    row["verdict"] = f"need at least {MIN_DAYS_TO_COMPARE} days of each to compare profit per day"
                else:
                    p = permutation_p_value(da, db)
                    better = a if np.mean(da) > np.mean(db) else b
                    row.update(mean_a=round(float(np.mean(da)), 6), mean_b=round(float(np.mean(db)), 6), p_value=p,
                               verdict=(f"{better} earns more per day (p = {p:.3f}, unlikely to be luck)" if p is not None
                                        and p < 0.05 else f"no proven difference yet (p = {p:.2f})"))
                comparisons.append(row)
    return rows, comparisons


def advisor_stats(samples: list[LabSample]) -> list[dict]:
    """Advisor variants: wins, stops, timeouts and the return after real-market fees, by forecast bucket."""
    groups: dict[str, list[LabSample]] = defaultdict(list)
    for s in samples:
        if s.kind == LabSampleKind.ADVISOR_TRADE:
            groups[s.variant].append(s)
    rows = []
    for variant, group in sorted(groups.items()):
        done = [s for s in group if s.closed_at is not None and s.outcome.get("return_market_pct") is not None]
        wins = sum(1 for s in done if s.outcome.get("exit_reason") == "target")
        stops = sum(1 for s in done if s.outcome.get("exit_reason") == "stop")
        ci = bootstrap_mean_ci([s.outcome["return_market_pct"] for s in done])
        buckets = []
        for lo in (0.45, 0.5, 0.55, 0.6, 0.65):
            sel = [s for s in done if lo <= (s.features.get("p_up_1h") or -1) < lo + 0.05]
            if sel:
                buckets.append({"bucket": f"{lo * 100:.0f}–{(lo + 0.05) * 100:.0f}%", "n": len(sel),
                                "win_rate": round(sum(1 for s in sel if s.outcome.get("win")) / len(sel), 4),
                                "avg_return_pct": round(float(np.mean([s.outcome["return_market_pct"] for s in sel])), 5)})
        rows.append({"variant": variant, "trades": len(group), "closed": len(done), "open": len(group) - len(done),
                     "wins": wins, "stops": stops, "timeouts": len(done) - wins - stops,
                     "win_rate": round(wins / len(done), 4) if done else None,
                     "return_pct": {"mean": round(ci[0], 5), "low": round(ci[1], 5), "high": round(ci[2], 5)} if ci else None,
                     "total_profit_market_usdt": round(sum(s.outcome.get("profit_market_usdt") or 0 for s in done), 6),
                     "by_probability": buckets})
    return rows
