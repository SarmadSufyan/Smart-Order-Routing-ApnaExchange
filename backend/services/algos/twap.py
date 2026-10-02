"""TWAP execution bot: slice a parent order evenly over a time window.

Each slice is an ordinary order submitted through ``OrderEntry`` — the same
path as the order button — so it gets pre-trade risk, the funding gate,
best-price routing across venues and price-protected IOC children. The bot
never talks to an exchange itself.

Schedule (catch-up). For N slices over duration D, slice i (1..N) is due at
start + (i-1)·D/N (± optional jitter) and targets a cumulative fill of Q·i/N:

    for i in 1..N:                                   # O(N) slices
        wait until slice i is due (pause/cancel wake the loop early)
        if kill switch active: pause; stop
        qty = floor_to_lot(min(target_i - filled, 2 * Q/N, per-order cap))
        order = OrderEntry.submit(qty)               # routed: O(L log L) each
        filled += order.filled
    status = COMPLETED if filled == Q else EXPIRED (the shortfall is reported)

A slice that fills short rolls its remainder into the next one, capped at
twice the normal size so catching up never becomes a market-impact spike.

Before the first slice, every planned slice is pre-flighted against the
position it will meet (the risk batch), so a TWAP that would cross a
position or exposure limit half way is refused up front.

Safety: a slice that is already at the exchange is never interrupted —
pause and cancel take effect between slices, so no fill can be lost. The
kill switch pauses every running TWAP, a restart leaves them paused (never
auto-resumed), and three slices in a row rejected before reaching any venue
pause the TWAP with the reason.
"""

import asyncio
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
    AlgoStatus,
    TwapCreate,
    TwapSlice,
    TwapState,
)
from backend.shared.utils.logger import get_logger

logger = get_logger("twap")

MAX_CATCHUP = 2.0  # a slice may be at most this many normal slices
MAX_CONSECUTIVE_REJECTIONS = 3
STOP_GRACE_S = 15.0


class AlgoNotFoundError(DomainError):
    def __init__(self, algo_id: str):
        super().__init__(f"Algo order {algo_id} not found", "ALGO_NOT_FOUND")


class AlgoRejectedError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "ALGO_REJECTED")


# ── pure schedule math (unit-tested) ────────────────────────────────
def plan_schedule(
    params: TwapCreate, quantity: float, start: datetime, lot_step: float, rng: random.Random | None = None,
) -> list[TwapSlice]:
    """Due times and cumulative targets for every slice."""
    interval = params.duration_s / params.slices
    slices = []
    for i in range(1, params.slices + 1):
        offset = (i - 1) * interval
        if params.jitter_pct and i > 1 and rng is not None:
            # ± jitter% of HALF an interval keeps slices in order.
            offset += rng.uniform(-1, 1) * params.jitter_pct / 100 * interval / 2
        target = quantity if i == params.slices else floor_to_step(quantity * i / params.slices, lot_step)
        slices.append(TwapSlice(index=i, scheduled_at=start + timedelta(seconds=offset), target_cumulative=target))
    return slices


def slice_quantity(target_cumulative: float, filled: float, nominal: float, lot_step: float,
                   max_child: float | None = None) -> float:
    """What the next slice should ask for: catch up to the schedule, within caps."""
    want = min(target_cumulative - filled, MAX_CATCHUP * nominal)
    if max_child is not None:
        want = min(want, max_child)
    return max(0.0, floor_to_step(want, lot_step))


def cost_bps(side: OrderSide, price: float, benchmark: float) -> float:
    """Execution cost vs a benchmark, in bps. Positive = worse than the benchmark."""
    sign = 1 if side == OrderSide.BUY else -1
    return (price - benchmark) / benchmark * 10_000 * sign


def recompute_metrics(state: TwapState) -> None:
    filled = sum(s.filled for s in state.slices)
    notional = sum(s.filled * s.avg_price for s in state.slices)
    state.filled_quantity = round(filled, 12)
    state.avg_fill_price = notional / filled if filled > 0 else 0.0
    side = state.params.side
    if filled > 0 and state.arrival_mid:
        state.arrival_slippage_bps = round(cost_bps(side, state.avg_fill_price, state.arrival_mid), 3)
    mids = [s.mid_at_send for s in state.slices if s.sent_at and s.mid_at_send]
    if mids:
        state.interval_twap = sum(mids) / len(mids)
        if filled > 0:
            state.vs_interval_twap_bps = round(cost_bps(side, state.avg_fill_price, state.interval_twap), 3)


class TwapEngine:
    def __init__(self, entry, order_manager, aggregator, collector, routing_engine, registry,
                 risk_engine, wallet, store=None) -> None:
        self._entry = entry
        self._orders = order_manager
        self._aggregator = aggregator
        self._collector = collector
        self._router = routing_engine
        self._registry = registry
        self._risk = risk_engine
        self._wallet = wallet
        self._store = store
        self._algos: dict[str, TwapState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._busy: set[str] = set()  # a slice is at the exchange right now
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
                    "Gateway restarted while this TWAP was running — review and resume it manually",
                )

    def start(self) -> None:
        event_bus.subscribe("kill_switch", self._on_kill_switch)

    async def stop(self) -> None:
        event_bus.unsubscribe("kill_switch", self._on_kill_switch)
        for algo in list(self._algos.values()):
            if algo.status == AlgoStatus.RUNNING:
                await self._set_status(algo, AlgoStatus.PAUSED, "system", "Gateway stopped — resume manually")
        # Let a slice that is at the exchange finish, so its fill is recorded.
        deadline = asyncio.get_running_loop().time() + STOP_GRACE_S
        while self._busy and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.2)
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ── queries ─────────────────────────────────────────────────────
    def list(self) -> list[TwapState]:
        return sorted(self._algos.values(),
                      key=lambda a: (a.status not in ACTIVE_STATUSES, -a.created_at.timestamp()))

    def get(self, algo_id: str) -> TwapState:
        algo = self._algos.get(algo_id)
        if algo is None:
            raise AlgoNotFoundError(algo_id)
        return algo

    # ── create ──────────────────────────────────────────────────────
    async def create(self, params: TwapCreate, user: str) -> TwapState:
        params = params.model_copy(update={"symbol": params.symbol.upper()})
        symbol, side = params.symbol, params.side
        base, quote = split_symbol(symbol)
        if self._risk.kill_switch.is_active:
            raise AlgoRejectedError("Kill switch is active — no new algo orders")
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
        step = max(r.amount_step for r in rules.values())

        notes: list[str] = []
        quantity = floor_to_step(params.quantity, step)
        if quantity <= 0:
            raise AlgoRejectedError(f"Quantity {params.quantity:g} is below the lot size ({step:g} {base})")
        if not is_close(quantity, params.quantity):
            notes.append(f"Quantity rounded down to the lot size: {fmt_qty(params.quantity)} -> {fmt_qty(quantity)}")
        nominal = floor_to_step(quantity / params.slices, step)
        if nominal <= 0:
            raise AlgoRejectedError(
                f"{params.slices} slices of {fmt_qty(quantity)} {base} are below the lot size "
                f"({step:g}); use at most {int(quantity / step)} slices"
            )

        # Some venue must accept a slice this small, or every slice fails.
        def fits(r) -> bool:
            return nominal >= r.min_amount and nominal * mid >= r.min_notional

        if not any(fits(r) for r in rules.values()):
            smallest = min(max(r.min_amount, r.min_notional / mid) for r in rules.values())
            max_slices = int(quantity / smallest)
            raise AlgoRejectedError(
                f"Each slice would be {fmt_qty(nominal)} {base} (~${nominal * mid:,.2f}), smaller than any "
                f"venue accepts (smallest minimum ~{fmt_qty(smallest)} {base}). "
                + (f"Use at most {max_slices} slices or a larger quantity."
                   if max_slices >= 2 else "Use a larger quantity.")
            )
        too_small = [v for v, r in rules.items() if not fits(r)]
        if too_small:
            notes.append(f"Slices are below the minimum order on {', '.join(too_small)}; they route elsewhere")

        limits = self._risk.limits
        parent_value = quantity * mid
        if parent_value > limits.max_algo_notional_usdt:
            raise AlgoRejectedError(
                f"Algo order value ${parent_value:,.2f} exceeds the algo limit ${limits.max_algo_notional_usdt:,.2f}"
            )
        if nominal * mid > limits.max_order_notional:
            raise AlgoRejectedError(
                f"Each slice (~${nominal * mid:,.2f}) exceeds the per-order limit "
                f"${limits.max_order_notional:,.2f}; use more slices"
            )

        # Pre-flight every slice against the position it will meet (the risk
        # batch): a TWAP that would cross the position or exposure limit half
        # way is refused now, not after it has already traded half of it.
        now = datetime.now(UTC)
        schedule = plan_schedule(params, quantity, now, step, self._rng)
        targets = [s.target_cumulative for s in schedule]
        sizes = [t - prev for prev, t in zip([0.0, *targets[:-1]], targets, strict=True)]
        report = await self._risk.preflight([OrderSpec(symbol, side.value, q) for q in sizes], {symbol: mid})
        if (bad := report.first_rejection) is not None:
            raise AlgoRejectedError(
                f"Pre-flight: slice {bad.index + 1} of {len(sizes)} would be rejected by pre-trade risk "
                f"({', '.join(bad.outcome.failed)}): {bad.outcome.message}"
            )
        notes.append(f"Pre-flight: all {len(sizes)} slices pass pre-trade risk at today's limits")

        # Can the venues fund the whole parent right now? (Each slice is checked again.)
        probe = Order(symbol=symbol, side=side, quantity=quantity, order_type=OrderType.MARKET)
        capacity = await self._router.venue_capacity(probe, list(rules), nbbo)
        if len(capacity) == len(rules):
            total = sum(capacity.values())
            if total < quantity and not is_close(total, quantity):
                funding = quote if side == OrderSide.BUY else base
                breakdown = ", ".join(f"{v} {fmt_qty(q)}" for v, q in capacity.items())
                raise AlgoRejectedError(
                    f"Insufficient balance: the venues can fund {fmt_qty(total)} of {fmt_qty(quantity)} "
                    f"{base} from their {funding} ({breakdown})"
                )

        if params.limit_price:
            away = cost_bps(side, params.limit_price, mid)
            if away < -100:
                notes.append(f"Limit {fmt_price(params.limit_price)} is {-away:.0f} bps through the market "
                             "on the passive side — slices fill only if the price comes to it")

        state = TwapState(
            id=f"TWAP-{uuid4().hex[:6].upper()}",
            created_by=user,
            params=params.model_copy(update={"quantity": quantity}),
            interval_s=params.duration_s / params.slices,
            nominal_slice=nominal,
            lot_step=step,
            started_at=now,
            arrival_mid=mid,
            notes=notes,
            slices=schedule,
        )
        self._algos[state.id] = state
        self._persist(state)
        await audit(
            "algo_started",
            f"{state.id} started by {user}: {side.value} {fmt_qty(quantity)} {symbol} in {params.slices} slices "
            f"of ~{fmt_qty(nominal)} every {state.interval_s:g} s"
            + (f", limit {fmt_price(params.limit_price)}" if params.limit_price else "")
            + f" · arrival mid {fmt_price(mid)}",
            actor=user, order_id=state.id, symbol=symbol, payload=params.model_dump(mode="json"),
        )
        self._launch(state)
        return state

    # ── controls ────────────────────────────────────────────────────
    async def pause(self, algo_id: str, actor: str, reason: str | None = None) -> TwapState:
        algo = self.get(algo_id)
        if algo.status != AlgoStatus.RUNNING:
            raise AlgoRejectedError(f"{algo_id} is {algo.status.value}, not running")
        await self._set_status(algo, AlgoStatus.PAUSED, actor, reason or f"Paused by {actor}")
        return algo

    async def resume(self, algo_id: str, actor: str) -> TwapState:
        algo = self.get(algo_id)
        if algo.status != AlgoStatus.PAUSED:
            raise AlgoRejectedError(f"{algo_id} is {algo.status.value}, not paused")
        if self._risk.kill_switch.is_active:
            raise AlgoRejectedError("Kill switch is active — release it before resuming")
        # The remaining slices continue from now at the same spacing.
        pending = [s for s in algo.slices if s.status == "PENDING"]
        now = datetime.now(UTC)
        for k, s in enumerate(pending):
            s.scheduled_at = now + timedelta(seconds=k * algo.interval_s)
        self._rejections[algo_id] = 0
        await self._set_status(algo, AlgoStatus.RUNNING, actor, f"Resumed by {actor}")
        task = self._tasks.get(algo_id)
        if task is None or task.done():
            self._launch(algo)
        return algo

    async def cancel(self, algo_id: str, actor: str) -> TwapState:
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

    # ── the loop ────────────────────────────────────────────────────
    def _launch(self, algo: TwapState) -> None:
        self._wake[algo.id] = asyncio.Event()
        self._tasks[algo.id] = asyncio.create_task(self._run(algo.id))

    async def _run(self, algo_id: str) -> None:
        algo = self._algos[algo_id]
        wake = self._wake[algo_id]
        try:
            while algo.status == AlgoStatus.RUNNING:
                nxt = next((s for s in algo.slices if s.status == "PENDING"), None)
                if nxt is None:
                    await self._finish(algo)
                    return
                delay = (nxt.scheduled_at - datetime.now(UTC)).total_seconds()
                if delay > 0:
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    continue  # re-check the status: pause/cancel wake us early
                await self._execute_slice(algo, nxt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — record it, never leave the algo silently stuck
            logger.error("twap_failed", algo_id=algo_id, error=str(exc)[:200])
            await self._set_status(algo, AlgoStatus.FAILED, "system", f"Internal error: {str(exc)[:200]}")

    async def _execute_slice(self, algo: TwapState, sl: TwapSlice) -> None:
        if self._risk.kill_switch.is_active:
            await self._set_status(algo, AlgoStatus.PAUSED, "kill-switch", "Kill switch is active")
            return
        params = algo.params
        base, _ = split_symbol(params.symbol)
        nbbo = await self._aggregator.compute_nbbo(params.symbol)
        mid = (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo and nbbo.best_bid and nbbo.best_ask else None
        max_child = (floor_to_step(self._risk.limits.max_order_notional / mid, algo.lot_step)
                     if mid else None)
        qty = slice_quantity(sl.target_cumulative, algo.filled_quantity, algo.nominal_slice, algo.lot_step, max_child)
        sl.sent_at, sl.mid_at_send = datetime.now(UTC), mid
        if qty <= 0:
            sl.status, sl.note = "SKIPPED", "schedule already met"
            self._touch(algo)
            return

        order_in = OrderCreate(
            symbol=params.symbol, side=params.side, quantity=qty,
            order_type=OrderType.LIMIT if params.limit_price else OrderType.MARKET,
            limit_price=params.limit_price,
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

        summary = (f"{algo.id} slice {sl.index}/{params.slices}: "
                   + (f"{fmt_qty(sl.filled)} of {fmt_qty(qty)} {base} @ {fmt_price(sl.avg_price)} ({', '.join(sl.venues)})"
                      if sl.filled > 0 else f"0 of {fmt_qty(qty)} {base} filled — {sl.note}")
                   + f" · done {fmt_qty(algo.filled_quantity)}/{fmt_qty(params.quantity)}")
        await audit("algo_slice", summary, actor="twap", order_id=algo.id, symbol=params.symbol,
                    payload=sl.model_dump(mode="json"))
        await event_bus.publish("algo_update", {"id": algo.id, "status": algo.status.value, "slice": sl.index})

        # Rejected before reaching any venue (risk, funding, no liquidity) three
        # times running: something is wrong — stop and say why.
        if not order.child_orders:
            self._rejections[algo.id] = self._rejections.get(algo.id, 0) + 1
            if self._rejections[algo.id] >= MAX_CONSECUTIVE_REJECTIONS and algo.status == AlgoStatus.RUNNING:
                await self._set_status(
                    algo, AlgoStatus.PAUSED, "twap",
                    f"{MAX_CONSECUTIVE_REJECTIONS} slices in a row were rejected before reaching any venue: {sl.note}",
                )
        else:
            self._rejections[algo.id] = 0

    async def _finish(self, algo: TwapState) -> None:
        recompute_metrics(algo)
        base, _ = split_symbol(algo.params.symbol)
        done = algo.filled_quantity >= algo.params.quantity or is_close(algo.filled_quantity, algo.params.quantity)
        result = (f"{fmt_qty(algo.filled_quantity)} {base} @ avg {fmt_price(algo.avg_fill_price)}"
                  if algo.filled_quantity > 0 else "nothing filled")
        if algo.arrival_slippage_bps is not None:
            result += f" · {algo.arrival_slippage_bps:+.2f} bps vs arrival"
        if algo.vs_interval_twap_bps is not None:
            result += f" · {algo.vs_interval_twap_bps:+.2f} bps vs interval TWAP"
        if algo.fees_usdt:
            result += f" · fees ${algo.fees_usdt:,.4f}"
        if done:
            await self._set_status(algo, AlgoStatus.COMPLETED, "twap", f"Completed: {result}")
        else:
            await self._set_status(
                algo, AlgoStatus.EXPIRED, "twap",
                f"Schedule ended with {fmt_qty(algo.remaining)} {base} unfilled: {result}",
            )

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

    def _touch(self, algo: TwapState) -> None:
        algo.updated_at = datetime.now(UTC)
        self._persist(algo)

    def _persist(self, algo: TwapState) -> None:
        if self._store is not None:
            self._store.upsert_algo(algo)

    async def _set_status(self, algo: TwapState, status: AlgoStatus, actor: str, reason: str) -> None:
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
