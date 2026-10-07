"""Execution algorithms: one engine works every kind of parent order over time.

Every child is an ordinary order submitted through ``OrderEntry`` — the same
path as the order button — so it gets pre-trade risk, the funding gate, the
chosen routing strategy across venues and price-protected IOC children. The
engine never talks to an exchange itself.

    TWAP / VWAP / IS   a plan fixed at the start: slice i is due at start + (i-1)*D/N and
                       targets a cumulative quantity (even / the market's volume profile /
                       Almgren-Chriss). A slice that fills short rolls into the next one,
                       capped at twice its planned size (catching up never becomes a spike).
    POV                every check interval: market volume since the start (real candles)
                       -> target = p% of it -> order the difference (when it reaches a
                       tradable size). Ends when filled or at its deadline.
    ICEBERG            every refresh: if the best price is at or better than the limit,
                       take one piece of about the display size at the limit (IOC). Never
                       shows more than the display size; nothing rests on any book.

A slice below the smallest order any exchange accepts is not sent: it rolls
into the next one (the last one reports what couldn't be done).

Before the first order, the planned children are pre-flighted against the
position they will meet (the risk batch), so an algo that would cross a
position or exposure limit half way is refused up front.

Safety: an order that is already at the exchange is never interrupted —
pause and cancel take effect between orders, so no fill can be lost. The
kill switch pauses every running algo, a restart leaves them paused (never
auto-resumed), and three orders in a row rejected before reaching any venue
pause the algo with the reason.
"""

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.exchanges.precision import floor_to_step, is_close
from backend.services.risk_engine.checks import OrderSpec
from backend.services.routing_engine.strategies.best_price import fmt_price, fmt_qty
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import Order, OrderCreate, OrderSide, OrderType
from backend.shared.models.strategy import (
    ACTIVE_STATUSES,
    SCHEDULED_KINDS,
    URGENCY_KAPPA_T,
    AlgoCreate,
    AlgoKind,
    AlgoRecommendation,
    AlgoState,
    AlgoStatus,
    RecommendRequest,
    TwapSlice,
)
from backend.shared.utils.logger import get_logger

from .schedules import (
    AutoInputs,
    ac_cost_risk,
    almgren_chriss_holdings,
    choose_algo,
    cost_bps,
    enforce_min_slice,
    iceberg_piece,
    impact_coefficients,
    market_vwap,
    minute_sigma,
    plan_schedule,
    pov_quantity,
    recompute_metrics,
    slice_quantity,
    targets_from_holdings,
    volume_between,
    volume_profile,
    weighted_schedule,
)

logger = get_logger("algos")

MAX_CONSECUTIVE_REJECTIONS = 3
STOP_GRACE_S = 15.0
VOLUME_LOOKBACK_MIN = 60  # recent minutes behind "the market trades X per minute"
SIGMA_LOOKBACK_MIN = 120  # minutes of candles for volatility when the live tracker has too little


class AlgoNotFoundError(DomainError):
    def __init__(self, algo_id: str):
        super().__init__(f"Algo order {algo_id} not found", "ALGO_NOT_FOUND")


class AlgoRejectedError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "ALGO_REJECTED")


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


class AlgoEngine:
    def __init__(self, entry, order_manager, aggregator, collector, routing_engine, registry,
                 risk_engine, wallet, store=None, history=None, volatility=None) -> None:
        self._entry = entry
        self._orders = order_manager
        self._aggregator = aggregator
        self._collector = collector
        self._router = routing_engine
        self._registry = registry
        self._risk = risk_engine
        self._wallet = wallet
        self._store = store
        self._history = history  # MarketHistory: real-market candles (VWAP, POV, market VWAP benchmark)
        self._volatility = volatility  # VolatilityTracker: live sigma for implementation shortfall
        self._algos: dict[str, AlgoState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._busy: set[str] = set()  # an order is at the exchange right now
        self._rejections: dict[str, int] = {}
        self._rng = random.Random()

    # ── lifecycle ───────────────────────────────────────────────────
    async def load(self) -> None:
        """Restore algo orders. Anything that was running comes back PAUSED."""
        if self._store is None:
            return
        for algo in await self._store.load_algos():
            self._algos[algo.id] = algo
            if algo.status == AlgoStatus.RUNNING:
                await self._set_status(
                    algo, AlgoStatus.PAUSED, "system",
                    f"Gateway restarted while this {algo.kind} was running — review and resume it manually",
                )

    def start(self) -> None:
        event_bus.subscribe("kill_switch", self._on_kill_switch)

    async def stop(self) -> None:
        event_bus.unsubscribe("kill_switch", self._on_kill_switch)
        for algo in list(self._algos.values()):
            if algo.status == AlgoStatus.RUNNING:
                await self._set_status(algo, AlgoStatus.PAUSED, "system", "Gateway stopped — resume manually")
        # Let an order that is at the exchange finish, so its fill is recorded.
        deadline = asyncio.get_running_loop().time() + STOP_GRACE_S
        while self._busy and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.2)
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ── queries ─────────────────────────────────────────────────────
    def list(self) -> list[AlgoState]:
        return sorted(self._algos.values(),
                      key=lambda a: (a.status not in ACTIVE_STATUSES, -a.created_at.timestamp()))

    def get(self, algo_id: str) -> AlgoState:
        algo = self._algos.get(algo_id)
        if algo is None:
            raise AlgoNotFoundError(algo_id)
        return algo

    # ── market helpers ──────────────────────────────────────────────
    async def _market(self, symbol: str):
        """(nbbo, mid, executable venues' rules) — or AlgoRejectedError with the reason."""
        if symbol not in self._collector.symbols:
            raise AlgoRejectedError(f"Unsupported symbol {symbol}. Tradable: {', '.join(self._collector.symbols)}")
        nbbo = await self._aggregator.compute_nbbo(symbol)
        if not nbbo or not nbbo.best_bid or not nbbo.best_ask:
            raise AlgoRejectedError(f"No live market data for {symbol}")
        mid = (nbbo.best_bid + nbbo.best_ask) / 2
        venues = self._router.executable_venues(symbol)
        rules = {v: r for v in venues if (a := self._registry.get(v)) and (r := a.rules(symbol))}
        if not rules:
            excluded = self._router.excluded_venues(symbol)
            why = "; ".join(f"{v}: {r}" for v, r in excluded.items()) or "no venue connected"
            raise AlgoRejectedError(f"No venue can execute {symbol} right now ({why})")
        return nbbo, mid, rules

    @staticmethod
    def _venue_min(rule, mid: float) -> float:
        return max(rule.min_amount, rule.min_notional / mid if mid else 0.0)

    def _min_tradable(self, rules: dict, mid: float, capacity: dict[str, float] | None = None) -> float:
        """The smallest order a venue that can FUND it accepts at this price.

        A venue without the balance doesn't count (found live: Gate's ~$3 minimum
        was used while Gate had no USDT, so slices only Binance/Bybit could take
        - $5 minimum - were planned too small).
        """
        mins = [self._venue_min(r, mid) for v, r in rules.items()
                if capacity is None or v not in capacity or capacity[v] >= self._venue_min(r, mid)]
        return min(mins) if mins else min(self._venue_min(r, mid) for r in rules.values())

    async def _capacity(self, symbol: str, side: OrderSide, quantity: float, rules: dict, nbbo) -> dict[str, float]:
        probe = Order(symbol=symbol, side=side, quantity=quantity, order_type=OrderType.MARKET)
        return await self._router.venue_capacity(probe, list(rules), nbbo)

    def _book_side(self, symbol: str, side: OrderSide, venues: list[str]) -> list[tuple[float, float]]:
        """The consolidated side we would take (asks to buy, bids to sell), best first."""
        levels = []
        for vid in venues:
            depth = self._collector.get_depth(vid, symbol, 50) if hasattr(self._collector, "get_depth") else None
            if depth:
                levels.extend(depth["asks"] if side == OrderSide.BUY else depth["bids"])
        return sorted(levels, key=lambda lv: lv[0], reverse=side == OrderSide.SELL)

    async def _sigma_per_min(self, symbol: str) -> tuple[float | None, str]:
        """Per-minute volatility (relative) and where it came from."""
        if self._volatility is not None and (s := self._volatility.sigma(symbol)) is not None:
            return s, "live mid prices"
        if self._history is not None:
            try:
                bars, venues = await self._history.recent_bars(symbol, SIGMA_LOOKBACK_MIN)
                if (s := minute_sigma(bars)) is not None:
                    return s, f"last {SIGMA_LOOKBACK_MIN} min of candles ({', '.join(venues)})"
            except Exception as exc:  # noqa: BLE001 — volatility is optional for the plan
                logger.info("sigma_from_candles_failed", symbol=symbol, error=str(exc)[:160])
        return None, "not available"

    # ── create ──────────────────────────────────────────────────────
    async def create(self, params: AlgoCreate, user: str) -> AlgoState:
        params = params.model_copy(update={"symbol": params.symbol.upper()})
        symbol, side, kind = params.symbol, params.side, params.kind
        base, quote = split_symbol(symbol)
        if self._risk.kill_switch.is_active:
            raise AlgoRejectedError("Kill switch is active — no new algo orders")
        nbbo, mid, rules = await self._market(symbol)
        step = max(r.amount_step for r in rules.values())
        capacity = await self._capacity(symbol, side, params.quantity, rules, nbbo)
        min_tradable = self._min_tradable(rules, mid, capacity)

        notes: list[str] = []
        quantity = floor_to_step(params.quantity, step)
        if quantity <= 0:
            raise AlgoRejectedError(f"Quantity {params.quantity:g} is below the lot size ({step:g} {base})")
        if not is_close(quantity, params.quantity):
            notes.append(f"Quantity rounded down to the lot size: {fmt_qty(params.quantity)} -> {fmt_qty(quantity)}")
        if quantity < min_tradable:
            raise AlgoRejectedError(
                f"{fmt_qty(quantity)} {base} (~${quantity * mid:,.2f}) is smaller than any venue accepts "
                f"(smallest minimum ~{fmt_qty(min_tradable)} {base})")

        now = datetime.now(UTC)
        plan: dict = {"min_tradable": min_tradable}
        schedule: list[TwapSlice] = []
        interval = 0.0
        if kind in SCHEDULED_KINDS:
            schedule, plan_notes = await self._plan(params, quantity, now, step, mid, nbbo, list(rules), plan)
            notes.extend(plan_notes)
            # Every planned slice must be an order some funded venue accepts.
            planned = [s.target_cumulative for s in schedule]
            fixed = enforce_min_slice(planned, quantity, min_tradable, step)
            merged = sum(1 for a, b in zip(planned, fixed, strict=True) if not is_close(a, b))
            for s, target in zip(schedule, fixed, strict=True):
                s.target_cumulative = target
            interval = params.duration_s / params.slices
            sizes = [t - prev for prev, t in zip([0.0, *[s.target_cumulative for s in schedule[:-1]]],
                                                 [s.target_cumulative for s in schedule], strict=True)]
            nominal = floor_to_step(quantity / params.slices, step)
            if nominal <= 0:
                raise AlgoRejectedError(
                    f"{params.slices} slices of {fmt_qty(quantity)} {base} are below the lot size "
                    f"({step:g}); use at most {int(quantity / step)} slices")
            # Some funded venue must accept an average slice, or the algo can't work.
            unfunded = [v for v, r in rules.items() if v in capacity and capacity[v] < self._venue_min(r, mid)]
            fits = [v for v, r in rules.items() if nominal >= r.min_amount and nominal * mid >= r.min_notional
                    and v not in unfunded]
            if not fits:
                max_slices = int(quantity / min_tradable)
                raise AlgoRejectedError(
                    f"Each slice would be {fmt_qty(nominal)} {base} (~${nominal * mid:,.2f}), smaller than any "
                    f"venue accepts (smallest minimum ~{fmt_qty(min_tradable)} {base}). "
                    + (f"Use at most {max_slices} slices or a larger quantity."
                       if max_slices >= 2 else "Use a larger quantity."))
            if unfunded:
                funding = quote if side == OrderSide.BUY else base
                notes.append(f"{', '.join(unfunded)}: no {funding} balance for this order — slices route elsewhere")
            if too_small := [v for v in rules if v not in fits and v not in unfunded]:
                notes.append(f"Slices are below the minimum order on {', '.join(too_small)}; they route elsewhere")
            if merged:
                notes.append(f"Slices smaller than the smallest order a funded exchange accepts "
                             f"(~{fmt_qty(min_tradable)} {base}) were merged into their neighbours "
                             f"({sum(1 for q in sizes if q <= 0)} of {len(sizes)} slices now send nothing)")
            largest = max(sizes)
        elif kind == AlgoKind.POV:
            nominal = max(floor_to_step(min_tradable, step), step)
            largest = None  # sized by live volume; each order is capped by the per-order limit at send time
            plan["participation_pct"] = params.participation_pct
            notes.append(f"Trades {params.participation_pct:g} % of the real market's volume (all exchanges we "
                         f"watch), checked every {params.check_interval_s:g} s, for at most "
                         f"{params.duration_s / 60:g} min")
        else:  # ICEBERG
            display = floor_to_step(params.display_qty, step)
            if display < min_tradable:
                raise AlgoRejectedError(
                    f"Display size {fmt_qty(params.display_qty)} {base} is below the smallest order an exchange "
                    f"accepts (~{fmt_qty(min_tradable)} {base})")
            nominal = display
            largest = min(quantity, display * (1 + params.size_variance_pct / 100))
            away = cost_bps(side, params.limit_price, mid)
            touch = nbbo.best_ask if side == OrderSide.BUY else nbbo.best_bid
            if (side == OrderSide.BUY and touch > params.limit_price) or (side == OrderSide.SELL and touch < params.limit_price):
                notes.append(f"The best {'ask' if side == OrderSide.BUY else 'bid'} ({fmt_price(touch)}) is beyond "
                             f"the limit {fmt_price(params.limit_price)} ({abs(away):.0f} bps from the mid): "
                             "it waits until the price comes to it")
            notes.append(f"Shows at most ~{fmt_qty(display)} {base} (±{params.size_variance_pct:g} %) at a time, "
                         f"only at {fmt_price(params.limit_price)} or better, looking every {params.refresh_s:g} s")

        limits = self._risk.limits
        parent_value = quantity * mid
        if parent_value > limits.max_algo_notional_usdt:
            raise AlgoRejectedError(
                f"Algo order value ${parent_value:,.2f} exceeds the algo limit ${limits.max_algo_notional_usdt:,.2f}")
        if largest is not None and largest * mid > limits.max_order_notional:
            raise AlgoRejectedError(
                f"The largest child (~${largest * mid:,.2f}) exceeds the per-order limit "
                f"${limits.max_order_notional:,.2f}; use more slices or a smaller display size")

        # Pre-flight the planned children against the position they will meet (the risk
        # batch): an algo that would cross a limit half way is refused now, not half done.
        if kind in SCHEDULED_KINDS:
            pieces = [q for q in sizes if q > 0]
        else:
            piece = nominal if kind == AlgoKind.ICEBERG else max(nominal, quantity / 10)
            count = max(1, math.ceil(quantity / piece))
            pieces = [quantity / count] * count
        report = await self._risk.preflight([OrderSpec(symbol, side.value, q) for q in pieces], {symbol: mid})
        if (bad := report.first_rejection) is not None:
            raise AlgoRejectedError(
                f"Pre-flight: slice {bad.index + 1} of {len(pieces)} would be rejected by pre-trade risk "
                f"({', '.join(bad.outcome.failed)}): {bad.outcome.message}")
        notes.append(f"Pre-flight: all {len(pieces)} slices pass pre-trade risk at today's limits")

        # Can the venues fund the whole parent right now? (Each child is checked again.)
        if len(capacity) == len(rules):
            total = sum(capacity.values())
            if total < quantity and not is_close(total, quantity):
                funding = quote if side == OrderSide.BUY else base
                breakdown = ", ".join(f"{v} {fmt_qty(q)}" for v, q in capacity.items())
                raise AlgoRejectedError(
                    f"Insufficient balance: the venues can fund {fmt_qty(total)} of {fmt_qty(quantity)} "
                    f"{base} from their {funding} ({breakdown})")

        if params.limit_price and kind != AlgoKind.ICEBERG:
            away = cost_bps(side, params.limit_price, mid)
            if away < -100:
                notes.append(f"Limit {fmt_price(params.limit_price)} is {-away:.0f} bps through the market "
                             "on the passive side — orders fill only if the price comes to it")
        if params.routing.value != "best_price":
            notes.append(f"Each child is routed with {params.routing.value.replace('_', ' ')}")

        state = AlgoState(
            id=f"{kind.value}-{uuid4().hex[:6].upper()}",
            created_by=user,
            params=params.model_copy(update={"quantity": quantity}),
            interval_s=interval or (params.check_interval_s if kind == AlgoKind.POV else params.refresh_s),
            nominal_slice=nominal,
            lot_step=step,
            started_at=now,
            deadline_at=None if kind in SCHEDULED_KINDS else now + timedelta(seconds=params.duration_s),
            arrival_mid=mid,
            notes=notes,
            plan=plan,
            slices=schedule,
        )
        self._algos[state.id] = state
        self._persist(state)
        await audit("algo_started", f"{state.id} started by {user}: {self._describe(state)} · arrival mid "
                    f"{fmt_price(mid)}", actor=user, order_id=state.id, symbol=symbol,
                    payload=params.model_dump(mode="json"))
        self._launch(state)
        return state

    async def _plan(self, params: AlgoCreate, quantity: float, now: datetime, step: float, mid: float, nbbo,
                    venues: list[str], plan: dict) -> tuple[list[TwapSlice], list[str]]:
        """The slice schedule of a TWAP / VWAP / IS, and what it is based on."""
        notes: list[str] = []
        n, duration = params.slices, params.duration_s
        if params.kind == AlgoKind.TWAP:
            return plan_schedule(params, quantity, now, step, self._rng), notes

        if params.kind == AlgoKind.VWAP:
            weights = None
            if self._history is not None:
                try:
                    days, starts, used = await self._history.past_days(params.symbol, _ms(now), duration,
                                                                       params.volume_days)
                    weights = volume_profile(days, starts, duration, n)
                    plan.update(profile_days=sum(1 for d in days if d), profile_venues=used)
                except Exception as exc:  # noqa: BLE001 — fall back to an even schedule, and say so
                    logger.warning("vwap_profile_failed", symbol=params.symbol, error=str(exc)[:160])
            if weights is None:
                notes.append("No volume history could be fetched: VWAP falls back to even slices (TWAP)")
                plan["profile_fallback"] = True
                weights = [1.0] * n
            else:
                peak = max(range(n), key=lambda j: weights[j])
                source = (f"Volume profile from the same {duration / 60:g} min on the last {plan['profile_days']} "
                          f"day(s) ({', '.join(plan['profile_venues'])})")
                if duration / n < 60:
                    notes.append(f"{source}: slices shorter than a minute can't be told apart by 1-minute candles, "
                                 "so slices within the same minute share its volume evenly")
                if weights[peak] - 1 / n > 0.01:
                    notes.append(f"{source}: slice {peak + 1} is the busiest ({weights[peak] * 100:.1f} % of the "
                                 f"volume vs {100 / n:.1f} % for an even split)")
                elif duration / n >= 60:
                    notes.append(f"{source}: the volume was spread evenly over this window — close to a TWAP")
            plan["profile"] = [round(w, 5) for w in weights]
            return weighted_schedule(weights, quantity, now, duration, step, params.jitter_pct, self._rng), notes

        # IS — Almgren-Chriss: front-loaded by urgency, cost and risk measured on live data
        kappa_t = URGENCY_KAPPA_T[params.urgency]
        holdings = almgren_chriss_holdings(quantity, n, kappa_t)
        targets = targets_from_holdings(holdings)
        tau = duration / n
        levels = self._book_side(params.symbol, params.side, venues)
        eps, eta = impact_coefficients(levels, quantity / n, mid, tau)
        sigma_min, sigma_src = await self._sigma_per_min(params.symbol)
        sigma_abs = sigma_min * mid / math.sqrt(60) if sigma_min else 0.0
        cost, risk = ac_cost_risk(holdings, eps, eta, sigma_abs, tau)
        twap_cost, twap_risk = ac_cost_risk(almgren_chriss_holdings(quantity, n, 0.0), eps, eta, sigma_abs, tau)
        first_fifth = targets[max(0, math.ceil(n / 5) - 1)] / quantity
        plan.update(kappa_t=kappa_t, urgency=params.urgency.value, eps=eps, eta=eta,
                    sigma_per_min_bps=round(sigma_min * 1e4, 3) if sigma_min else None, sigma_source=sigma_src,
                    expected_cost_usdt=round(cost, 6), risk_usdt=round(risk, 6) if sigma_min else None,
                    twap_cost_usdt=round(twap_cost, 6), twap_risk_usdt=round(twap_risk, 6) if sigma_min else None,
                    front_load_pct=round(first_fifth * 100, 1))
        notes.append(f"Almgren–Chriss schedule, {params.urgency.value.lower()} urgency (κT = {kappa_t:g}): "
                     f"{first_fifth * 100:.0f} % of the order in the first fifth of the time")
        if sigma_min:
            notes.append(f"Expected impact cost ${cost:,.4f} with price risk ±${risk:,.4f} (1 σ, volatility "
                         f"{sigma_min * 1e4:.1f} bps/min from {sigma_src}); an even TWAP: ${twap_cost:,.4f} "
                         f"±${twap_risk:,.4f}")
        else:
            notes.append(f"Expected impact cost ${cost:,.4f} (an even TWAP: ${twap_cost:,.4f}); price risk not "
                         "shown — no volatility measurement yet")
        slices = [TwapSlice(index=i, scheduled_at=now + timedelta(seconds=(i - 1) * tau),
                            target_cumulative=quantity if i == n else floor_to_step(t, step))
                  for i, t in enumerate(targets, start=1)]
        return slices, notes

    def _describe(self, algo: AlgoState) -> str:
        p, base = algo.params, split_symbol(algo.params.symbol)[0]
        head = f"{p.kind.value} {p.side.value} {fmt_qty(p.quantity)} {p.symbol}"
        if p.kind == AlgoKind.POV:
            body = f" at {p.participation_pct:g} % of market volume for up to {p.duration_s / 60:g} min"
        elif p.kind == AlgoKind.ICEBERG:
            body = f" showing ~{fmt_qty(algo.nominal_slice)} {base} at a time, limit {fmt_price(p.limit_price)}"
        else:
            body = f" in {p.slices} slices over {p.duration_s:g} s"
            if p.kind == AlgoKind.IS:
                body += f" ({p.urgency.value.lower()} urgency)"
        if p.limit_price and p.kind != AlgoKind.ICEBERG:
            body += f", limit {fmt_price(p.limit_price)}"
        return head + body

    # ── auto ────────────────────────────────────────────────────────
    async def recommend(self, req: RecommendRequest) -> AlgoRecommendation:
        """Auto: one order now, or which algorithm over how long — with the numbers behind it."""
        symbol = req.symbol.upper()
        nbbo, mid, rules = await self._market(symbol)
        step = max(r.amount_step for r in rules.values())
        quantity = floor_to_step(req.quantity, step)
        min_tradable = self._min_tradable(rules, mid, await self._capacity(symbol, req.side, quantity, rules, nbbo))
        levels = self._book_side(symbol, req.side, list(rules))
        impact = None
        remaining, cost = quantity, 0.0
        for price, size in levels:
            take = min(remaining, size)
            cost += take * price
            remaining -= take
            if remaining <= 1e-12:
                impact = cost_bps(req.side, cost / quantity, mid)
                break
        volume_per_min, has_history, venues = None, False, []
        if self._history is not None:
            try:
                bars, venues = await self._history.recent_bars(symbol, VOLUME_LOOKBACK_MIN)
                if bars:
                    volume_per_min = sum(b.volume for b in bars) / VOLUME_LOOKBACK_MIN
                    has_history = True
            except Exception as exc:  # noqa: BLE001 — recommend without volume, and say so
                logger.info("recommend_volume_failed", symbol=symbol, error=str(exc)[:160])
        limits = self._risk.limits
        spread_bps = (nbbo.best_ask - nbbo.best_bid) / mid * 1e4
        choice = choose_algo(AutoInputs(
            quantity=quantity, mid=mid, spread_bps=spread_bps, impact_now_bps=impact,
            volume_per_min=volume_per_min, urgency=req.urgency, max_duration_s=req.max_duration_s,
            min_tradable=min_tradable, max_order_qty=limits.max_order_notional / mid, has_volume_history=has_history,
        ))
        warnings = list(choice.warnings)
        if quantity * mid > limits.max_algo_notional_usdt and choice.kind not in ("SINGLE", "NONE"):
            warnings.append(f"Its value ${quantity * mid:,.2f} is above the algo limit "
                            f"${limits.max_algo_notional_usdt:,.2f}: it would be refused at start")
        params = None
        if choice.kind not in ("SINGLE", "NONE"):
            params = AlgoCreate(
                kind=AlgoKind(choice.kind), symbol=symbol, side=req.side, quantity=quantity,
                duration_s=choice.duration_s, slices=choice.slices, participation_pct=choice.participation_pct,
                urgency=req.urgency, routing=req.routing,
            )
        return AlgoRecommendation(
            choice=choice.kind, params=params, reasons=choice.reasons, warnings=warnings,
            alternatives=choice.alternatives,
            metrics={"mid": mid, "spread_bps": round(spread_bps, 3),
                     "impact_now_bps": round(impact, 3) if impact is not None else None,
                     "volume_per_min": volume_per_min, "volume_venues": venues, "min_tradable": min_tradable,
                     "quantity": quantity, "notional_usdt": round(quantity * mid, 2)},
        )

    # ── controls ────────────────────────────────────────────────────
    async def pause(self, algo_id: str, actor: str, reason: str | None = None) -> AlgoState:
        algo = self.get(algo_id)
        if algo.status != AlgoStatus.RUNNING:
            raise AlgoRejectedError(f"{algo_id} is {algo.status.value}, not running")
        await self._set_status(algo, AlgoStatus.PAUSED, actor, reason or f"Paused by {actor}")
        return algo

    async def resume(self, algo_id: str, actor: str) -> AlgoState:
        algo = self.get(algo_id)
        if algo.status != AlgoStatus.PAUSED:
            raise AlgoRejectedError(f"{algo_id} is {algo.status.value}, not paused")
        if self._risk.kill_switch.is_active:
            raise AlgoRejectedError("Kill switch is active — release it before resuming")
        now = datetime.now(UTC)
        if algo.params.kind in SCHEDULED_KINDS:
            # The remaining slices continue from now at the same spacing.
            pending = [s for s in algo.slices if s.status == "PENDING"]
            for k, s in enumerate(pending):
                s.scheduled_at = now + timedelta(seconds=k * algo.interval_s)
        else:
            # The time spent paused doesn't count against the deadline, and (POV) neither
            # does the market volume that traded meanwhile.
            if algo.paused_at and algo.deadline_at:
                algo.deadline_at += now - algo.paused_at
            if algo.params.kind == AlgoKind.POV:
                algo.plan["resume_pending"] = True
        algo.paused_at = None
        self._rejections[algo_id] = 0
        await self._set_status(algo, AlgoStatus.RUNNING, actor, f"Resumed by {actor}")
        task = self._tasks.get(algo_id)
        if task is None or task.done():
            self._launch(algo)
        return algo

    async def cancel(self, algo_id: str, actor: str) -> AlgoState:
        algo = self.get(algo_id)
        if algo.status not in ACTIVE_STATUSES:
            raise AlgoRejectedError(f"{algo_id} is already {algo.status.value}")
        for s in algo.slices:
            if s.status == "PENDING":
                s.status, s.note = "SKIPPED", "cancelled"
        recompute_metrics(algo)
        await self._set_status(
            algo, AlgoStatus.CANCELLED, actor,
            f"Cancelled by {actor} with {fmt_qty(algo.filled_quantity)} of {fmt_qty(algo.params.quantity)} filled",
        )
        return algo

    async def _on_kill_switch(self, data: dict) -> None:
        if not data.get("active") or "venue_orders_swept" in data:
            return
        for algo in list(self._algos.values()):
            if algo.status == AlgoStatus.RUNNING:
                await self._set_status(algo, AlgoStatus.PAUSED, "kill-switch",
                                       f"Kill switch engaged: {data.get('reason')}")

    # ── the loops ───────────────────────────────────────────────────
    def _launch(self, algo: AlgoState) -> None:
        self._wake[algo.id] = asyncio.Event()
        runner = {AlgoKind.POV: self._run_pov, AlgoKind.ICEBERG: self._run_iceberg}.get(
            algo.params.kind, self._run_scheduled)
        self._tasks[algo.id] = asyncio.create_task(self._guard(algo.id, runner))

    async def _guard(self, algo_id: str, runner) -> None:
        algo = self._algos[algo_id]
        try:
            await runner(algo)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — record it, never leave the algo silently stuck
            logger.error("algo_failed", algo_id=algo_id, error=str(exc)[:200])
            await self._set_status(algo, AlgoStatus.FAILED, "system", f"Internal error: {str(exc)[:200]}")

    async def _sleep(self, algo: AlgoState, seconds: float) -> None:
        """Wait, but wake early on pause / cancel."""
        if seconds <= 0:
            return
        wake = self._wake[algo.id]
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _run_scheduled(self, algo: AlgoState) -> None:
        while algo.status == AlgoStatus.RUNNING:
            nxt = next((s for s in algo.slices if s.status == "PENDING"), None)
            if nxt is None:
                await self._finish(algo)
                return
            delay = (nxt.scheduled_at - datetime.now(UTC)).total_seconds()
            if delay > 0:
                await self._sleep(algo, delay)
                continue  # re-check the status: pause/cancel wake us early
            await self._scheduled_slice(algo, nxt)

    async def _scheduled_slice(self, algo: AlgoState, sl: TwapSlice) -> None:
        if self._risk.kill_switch.is_active:
            await self._set_status(algo, AlgoStatus.PAUSED, "kill-switch", "Kill switch is active")
            return
        params = algo.params
        mid = await self._mid(params.symbol)
        max_child = floor_to_step(self._risk.limits.max_order_notional / mid, algo.lot_step) if mid else None
        prev = next((s.target_cumulative for s in reversed(algo.slices) if s.index < sl.index), 0.0)
        planned = max(sl.target_cumulative - prev, algo.nominal_slice)
        qty = slice_quantity(sl.target_cumulative, algo.filled_quantity, planned, algo.lot_step, max_child)
        sl.sent_at, sl.mid_at_send = datetime.now(UTC), mid
        if qty <= 0:
            sl.status, sl.note = "SKIPPED", "schedule already met"
            self._touch(algo)
            return
        min_tradable = algo.plan.get("min_tradable", 0.0)
        is_last = sl.index == max(s.index for s in algo.slices)
        if qty < min_tradable - 1e-12:
            # Only a catch-up remainder can be this small (the plan never is): roll it on, or
            # say plainly at the end that no exchange would take it.
            sl.quantity = qty
            sl.status = "UNFILLED" if is_last else "SKIPPED"
            sl.note = (f"{fmt_qty(qty)} is below the smallest order an exchange accepts (~{fmt_qty(min_tradable)})"
                       + (": nothing more can be sent" if is_last else ": rolled into the next slice"))
            self._touch(algo)
            return
        await self._send(algo, sl, qty, params.limit_price)

    async def _run_pov(self, algo: AlgoState) -> None:
        params = algo.params
        start_ms = _ms(algo.started_at)
        offset = algo.plan.get("volume_offset", 0.0)  # market volume that traded while paused
        last_volume = algo.market_volume or 0.0
        while algo.status == AlgoStatus.RUNNING:
            if algo.remaining <= 0 or is_close(algo.filled_quantity, params.quantity):
                await self._finish(algo)
                return
            if datetime.now(UTC) >= algo.deadline_at:
                await self._finish(algo)
                return
            if self._risk.kill_switch.is_active:
                await self._set_status(algo, AlgoStatus.PAUSED, "kill-switch", "Kill switch is active")
                return
            if self._history is None:
                algo.waiting_reason = "no market-volume source configured"
                await self._set_status(algo, AlgoStatus.PAUSED, "pov", "POV needs real-market volume data")
                return
            now_ms = _ms(datetime.now(UTC))
            try:
                bars, venues = await self._history.minute_bars(params.symbol, start_ms, now_ms)
            except Exception as exc:  # noqa: BLE001 — wait and try again
                bars, venues = [], []
                algo.waiting_reason = f"volume data unavailable ({str(exc)[:80]}) — retrying"
            gross = volume_between(bars, start_ms, now_ms, now_ms=now_ms)
            if algo.plan.pop("resume_pending", False):
                offset += max(gross - (algo.plan.get("volume_at_pause") or gross), 0.0)
                algo.plan["volume_offset"] = offset
                last_volume = gross - offset
            volume = max(gross - offset, 0.0)
            algo.market_volume = round(volume, 10)
            if bars:
                algo.market_vwap = market_vwap(bars, start_ms, now_ms, now_ms=now_ms)
                algo.plan["volume_venues"] = venues
            if volume > 0:
                algo.participation_actual_pct = float(f"{algo.filled_quantity / volume * 100:.4g}")
            mid = await self._mid(params.symbol)
            max_child = floor_to_step(self._risk.limits.max_order_notional / mid, algo.lot_step) if mid else None
            qty = pov_quantity(params.participation_pct, volume, algo.filled_quantity, volume - last_volume,
                               algo.remaining, algo.lot_step, algo.plan.get("min_tradable", 0.0), max_child)
            last_volume = volume
            if qty > 0:
                algo.waiting_reason = None
                sl = TwapSlice(index=len(algo.slices) + 1, scheduled_at=datetime.now(UTC),
                               target_cumulative=min(params.quantity, params.participation_pct / 100 * volume))
                sl.sent_at, sl.mid_at_send = datetime.now(UTC), mid
                algo.slices.append(sl)
                await self._send(algo, sl, qty, params.limit_price)
            elif bars or algo.waiting_reason is None:
                target = params.participation_pct / 100 * volume
                algo.waiting_reason = (f"{params.participation_pct:g} % of {fmt_qty(volume)} traded = "
                                       f"{fmt_qty(target)}; filled {fmt_qty(algo.filled_quantity)} — next order once "
                                       f"the gap reaches a tradable size (~{fmt_qty(algo.plan.get('min_tradable', 0))})")
            self._touch(algo)
            await event_bus.publish("algo_update", {"id": algo.id, "status": algo.status.value})
            await self._sleep(algo, params.check_interval_s)

    async def _run_iceberg(self, algo: AlgoState) -> None:
        params = algo.params
        is_buy = params.side == OrderSide.BUY
        while algo.status == AlgoStatus.RUNNING:
            if algo.remaining <= 0 or is_close(algo.filled_quantity, params.quantity):
                await self._finish(algo)
                return
            if datetime.now(UTC) >= algo.deadline_at:
                await self._finish(algo)
                return
            if self._risk.kill_switch.is_active:
                await self._set_status(algo, AlgoStatus.PAUSED, "kill-switch", "Kill switch is active")
                return
            nbbo = await self._aggregator.compute_nbbo(params.symbol)
            # Only venues we can trade on count: a data-only venue at the limit is no fill.
            executable = set(self._router.executable_venues(params.symbol))
            quotes = [q for vid, q in (nbbo.venue_quotes.items() if nbbo else []) if vid in executable]
            prices = [q.ask_price if is_buy else q.bid_price for q in quotes]
            prices = [p for p in prices if p and p > 0]
            touch = (min(prices) if is_buy else max(prices)) if prices else None
            crossing = touch is not None and (touch <= params.limit_price if is_buy else touch >= params.limit_price)
            if crossing:
                piece = iceberg_piece(algo.nominal_slice, params.size_variance_pct, self._rng.uniform(-1, 1),
                                      algo.remaining, algo.lot_step, algo.plan.get("min_tradable", 0.0))
                if piece > 0:
                    algo.waiting_reason = None
                    sl = TwapSlice(index=len(algo.slices) + 1, scheduled_at=datetime.now(UTC),
                                   target_cumulative=min(params.quantity, algo.filled_quantity + piece))
                    sl.sent_at, sl.mid_at_send = datetime.now(UTC), (nbbo.best_bid + nbbo.best_ask) / 2
                    algo.slices.append(sl)
                    await self._send(algo, sl, piece, params.limit_price)
            else:
                side_word = "ask" if is_buy else "bid"
                algo.waiting_reason = (f"best {side_word} {fmt_price(touch) if touch else '—'} is beyond the limit "
                                       f"{fmt_price(params.limit_price)} — waiting")
                self._touch(algo)
            # A varied refresh, so the pieces don't tick like a clock.
            await self._sleep(algo, params.refresh_s * (1 + 0.2 * self._rng.uniform(-1, 1)))

    # ── one child order ─────────────────────────────────────────────
    async def _mid(self, symbol: str) -> float | None:
        nbbo = await self._aggregator.compute_nbbo(symbol)
        return (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo and nbbo.best_bid and nbbo.best_ask else None

    async def _send(self, algo: AlgoState, sl: TwapSlice, qty: float, limit_price: float | None) -> None:
        params = algo.params
        base, _ = split_symbol(params.symbol)
        if sl.sent_at is None:
            sl.sent_at = datetime.now(UTC)
        order_in = OrderCreate(
            symbol=params.symbol, side=params.side, quantity=qty,
            order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
            limit_price=limit_price, strategy=params.routing,
        )
        self._busy.add(algo.id)
        try:
            order, error = await self._entry.submit(order_in, submitted_by=algo.created_by, parent_id=algo.id)
        finally:
            self._busy.discard(algo.id)

        sl.quantity = qty
        sl.order_id = str(order.id)
        sl.filled = order.filled_quantity
        sl.avg_price = order.avg_fill_price
        sl.venues = [c.venue_id for c in order.child_orders if c.filled_quantity > 0]
        if sl.filled > 0 and (sl.filled >= qty or is_close(sl.filled, qty)):
            sl.status = "FILLED"
        elif sl.filled > 0:
            sl.status = "PARTIAL"
        else:
            sl.status = "UNFILLED"
        child_reasons = "; ".join(f"{c.venue_id}: {c.reject_reason}" for c in order.child_orders if c.reject_reason)
        if sl.status != "FILLED":
            sl.note = (error.message if error else None) or child_reasons or order.rejection_reason
        algo.fees_usdt += self._fees_usdt(order)
        recompute_metrics(algo)
        self._touch(algo)

        total = params.slices if params.kind in SCHEDULED_KINDS else "~"
        summary = (f"{algo.id} slice {sl.index}/{total}: "
                   + (f"{fmt_qty(sl.filled)} of {fmt_qty(qty)} {base} @ {fmt_price(sl.avg_price)} ({', '.join(sl.venues)})"
                      if sl.filled > 0 else f"0 of {fmt_qty(qty)} {base} filled — {sl.note}")
                   + f" · done {fmt_qty(algo.filled_quantity)}/{fmt_qty(params.quantity)}")
        await audit("algo_slice", summary, actor=params.kind.value.lower(), order_id=algo.id, symbol=params.symbol,
                    payload=sl.model_dump(mode="json"))
        await event_bus.publish("algo_update", {"id": algo.id, "status": algo.status.value, "slice": sl.index})

        # Rejected before reaching any venue (risk, funding, no liquidity) three
        # times running: something is wrong — stop and say why.
        if not order.child_orders:
            self._rejections[algo.id] = self._rejections.get(algo.id, 0) + 1
            if self._rejections[algo.id] >= MAX_CONSECUTIVE_REJECTIONS and algo.status == AlgoStatus.RUNNING:
                await self._set_status(
                    algo, AlgoStatus.PAUSED, params.kind.value.lower(),
                    f"{MAX_CONSECUTIVE_REJECTIONS} orders in a row were rejected before reaching any venue: {sl.note}",
                )
        else:
            self._rejections[algo.id] = 0

    async def _finish(self, algo: AlgoState) -> None:
        if self._history is not None and algo.started_at and algo.filled_quantity > 0:
            try:  # judge the run against the real market's VWAP over the same time
                end_ms = _ms(datetime.now(UTC))
                bars, _ = await self._history.minute_bars(algo.params.symbol, _ms(algo.started_at), end_ms)
                algo.market_vwap = market_vwap(bars, _ms(algo.started_at), end_ms, now_ms=end_ms) or algo.market_vwap
            except Exception as exc:  # noqa: BLE001 — the benchmark is a bonus, never a blocker
                logger.info("market_vwap_failed", algo_id=algo.id, error=str(exc)[:160])
        recompute_metrics(algo)
        base, _ = split_symbol(algo.params.symbol)
        done = algo.filled_quantity >= algo.params.quantity or is_close(algo.filled_quantity, algo.params.quantity)
        result = (f"{fmt_qty(algo.filled_quantity)} {base} @ avg {fmt_price(algo.avg_fill_price)}"
                  if algo.filled_quantity > 0 else "nothing filled")
        if algo.arrival_slippage_bps is not None:
            result += f" · {algo.arrival_slippage_bps:+.2f} bps vs arrival"
        if algo.vs_market_vwap_bps is not None:
            result += f" · {algo.vs_market_vwap_bps:+.2f} bps vs market VWAP"
        elif algo.vs_interval_twap_bps is not None:
            result += f" · {algo.vs_interval_twap_bps:+.2f} bps vs interval TWAP"
        if algo.participation_actual_pct is not None:
            result += f" · {algo.participation_actual_pct:.3g} % of market volume"
        if algo.fees_usdt:
            result += f" · fees ${algo.fees_usdt:,.4f}"
        if done:
            await self._set_status(algo, AlgoStatus.COMPLETED, algo.params.kind.value.lower(), f"Completed: {result}")
        else:
            why = "Schedule ended" if algo.params.kind in SCHEDULED_KINDS else "Deadline reached"
            await self._set_status(algo, AlgoStatus.EXPIRED, algo.params.kind.value.lower(),
                                   f"{why} with {fmt_qty(algo.remaining)} {base} unfilled: {result}")

    # ── helpers ─────────────────────────────────────────────────────
    def _fees_usdt(self, order) -> float:
        base, quote = split_symbol(order.symbol)
        total = 0.0
        for c in order.child_orders:
            if not c.fee or not c.fee_asset:
                continue
            if c.fee_asset == quote:
                total += c.fee
            elif c.fee_asset == base:
                total += c.fee * (c.fill_price or 0.0)
            elif (price := self._wallet.price_usdt(c.fee_asset, c.venue_id)) is not None:
                total += c.fee * price
        return total

    def _touch(self, algo: AlgoState) -> None:
        algo.updated_at = datetime.now(UTC)
        self._persist(algo)

    def _persist(self, algo: AlgoState) -> None:
        if self._store is not None:
            self._store.upsert_algo(algo)

    async def _set_status(self, algo: AlgoState, status: AlgoStatus, actor: str, reason: str) -> None:
        if status == AlgoStatus.PAUSED and algo.status == AlgoStatus.RUNNING:
            algo.paused_at = datetime.now(UTC)
            algo.plan["volume_at_pause"] = (algo.market_volume or 0.0) + algo.plan.get("volume_offset", 0.0)
        algo.status = status
        algo.status_reason = reason
        if status not in ACTIVE_STATUSES:
            algo.ended_at = datetime.now(UTC)
        self._touch(algo)
        if (wake := self._wake.get(algo.id)) is not None:
            wake.set()
        event = {AlgoStatus.RUNNING: "algo_resumed", AlgoStatus.PAUSED: "algo_paused"}.get(status, "algo_finished")
        await audit(event, f"{algo.id} {status.value}: {reason}", actor=actor, order_id=algo.id,
                    symbol=algo.params.symbol)
        await event_bus.publish("algo_update", {"id": algo.id, "status": status.value, "reason": reason})
        logger.info("algo_status", algo_id=algo.id, status=status.value, reason=reason)


TwapEngine = AlgoEngine  # the original name (TWAP was the first algo)
