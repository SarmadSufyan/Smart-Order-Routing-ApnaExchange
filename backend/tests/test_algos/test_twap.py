"""TWAP bot: schedule math, catch-up, kill switch, rejections, restart, validation."""

import asyncio
import random
import unittest
from datetime import UTC, datetime
from unittest import mock

from pydantic import ValidationError

import backend.shared.models.strategy as strategy_models
from backend.exchanges.base import SymbolRules
from backend.services.algos.twap import (
    AlgoRejectedError,
    TwapEngine,
    cost_bps,
    plan_schedule,
    slice_quantity,
)
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.shared.database.store import AuditStore
from backend.shared.exceptions import RiskCheckFailedError
from backend.shared.models.market_data import NBBO
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderStatus
from backend.shared.models.risk import RiskLimits
from backend.shared.models.strategy import AlgoStatus, TwapCreate, TwapState

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def params(qty: float = 0.004, slices: int = 4, duration: float = 0.2, **kw) -> TwapCreate:
    return TwapCreate(symbol="BTC/USDT", side=kw.pop("side", OrderSide.BUY), quantity=qty,
                      duration_s=duration, slices=slices, limit_price=kw.pop("limit", None),
                      jitter_pct=kw.pop("jitter", 0.0))


class ScheduleMathTest(unittest.TestCase):
    def test_even_schedule_with_cumulative_targets(self) -> None:
        p = TwapCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0, duration_s=40, slices=4)
        s = plan_schedule(p, 1.0, T0, 0.001)
        self.assertEqual([x.target_cumulative for x in s], [0.25, 0.5, 0.75, 1.0])
        self.assertEqual([(x.scheduled_at - T0).total_seconds() for x in s], [0, 10, 20, 30])

    def test_jitter_keeps_slices_in_order_and_first_on_time(self) -> None:
        p = TwapCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0, duration_s=100, slices=10, jitter_pct=50)
        s = plan_schedule(p, 1.0, T0, 0.001, random.Random(7))
        times = [x.scheduled_at for x in s]
        self.assertEqual(times[0], T0)
        self.assertEqual(times, sorted(times))

    def test_catch_up_is_capped_at_twice_a_slice(self) -> None:
        self.assertAlmostEqual(slice_quantity(0.5, 0.1, 0.25, 0.001), 0.4)  # behind: catch up
        self.assertAlmostEqual(slice_quantity(1.0, 0.0, 0.25, 0.001), 0.5)  # far behind: 2x cap
        self.assertAlmostEqual(slice_quantity(1.0, 0.0, 0.25, 0.001, max_child=0.3), 0.3)  # per-order cap
        self.assertEqual(slice_quantity(0.5, 0.5, 0.25, 0.001), 0.0)  # ahead: nothing

    def test_cost_is_positive_when_worse_for_either_side(self) -> None:
        self.assertAlmostEqual(cost_bps(OrderSide.BUY, 101.0, 100.0), 100.0)
        self.assertAlmostEqual(cost_bps(OrderSide.SELL, 99.0, 100.0), 100.0)
        self.assertAlmostEqual(cost_bps(OrderSide.SELL, 101.0, 100.0), -100.0)

    def test_too_fast_a_schedule_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            TwapCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0, duration_s=10, slices=10)


# ── engine with stubbed market and order entry ─────────────────────
class StubEntry:
    """Scripted outcomes per slice: a fill fraction, or None = rejected before any venue."""

    def __init__(self, outcomes: list[float | None] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.requested: list[float] = []

    async def submit(self, order_in, submitted_by, parent_id=None):
        self.requested.append(order_in.quantity)
        frac = self.outcomes.pop(0) if self.outcomes else 1.0
        order = Order(symbol=order_in.symbol, side=order_in.side, quantity=order_in.quantity,
                      order_type=order_in.order_type, submitted_by=submitted_by, parent_id=parent_id)
        if frac is None:
            order.status, order.rejection_reason = OrderStatus.REJECTED, "Risk check failed: position limit"
            return order, RiskCheckFailedError("position limit")
        child = ChildOrder(parent_order_id=order.id, venue_id="V1", quantity=order_in.quantity, price=100.0)
        child.filled_quantity = round(order_in.quantity * frac, 12)
        child.fill_price = 100.5 if frac else 0.0
        child.reject_reason = None if frac else "price protection: no liquidity within 50 bps"
        order.child_orders = [child]
        order.filled_quantity, order.avg_fill_price = child.filled_quantity, child.fill_price
        return order, None


class StubAdapter:
    rules_min_notional = 0.05  # $ minimum order value

    def rules(self, symbol: str) -> SymbolRules:
        return SymbolRules(symbol=symbol, venue_symbol="BTCUSDT", base="BTC", quote="USDT", amount_step=0.001,
                           min_amount=0.001, price_tick=0.01, min_notional=StubAdapter.rules_min_notional)


class Stubs:
    symbols = ["BTC/USDT"]

    async def compute_nbbo(self, symbol: str) -> NBBO:
        return NBBO(symbol=symbol, best_bid=99.9, best_ask=100.1, best_bid_venue="V1", best_ask_venue="V1",
                    best_bid_size=10, best_ask_size=10, spread=0.2)

    def executable_venues(self, symbol=None):
        return ["V1"]

    def excluded_venues(self, symbol=None):
        return {}

    async def venue_capacity(self, order, venues, nbbo):
        return {}

    def get(self, venue_id: str):
        return StubAdapter()

    def price_usdt(self, asset, venue_id):
        return None


async def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


class TwapEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Real slices must be >= 3 s apart; tests run the same code 100x faster.
        patcher = mock.patch.object(strategy_models, "MIN_SLICE_INTERVAL_S", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, entry: StubEntry, store=None, limits: RiskLimits | None = None) -> TwapEngine:
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), limits or RiskLimits())
        s = Stubs()
        engine = TwapEngine(entry, None, s, s, s, s, self.risk, s, store=store)
        engine.start()
        self.addAsyncCleanup(engine.stop)
        return engine

    async def test_runs_every_slice_and_measures_the_execution(self) -> None:
        entry = StubEntry()
        engine = self.make(entry)
        algo = await engine.create(params(), "sarmad")
        await wait_for(lambda: algo.status == AlgoStatus.COMPLETED)
        self.assertEqual(entry.requested, [0.001] * 4)
        self.assertAlmostEqual(algo.filled_quantity, 0.004)
        self.assertAlmostEqual(algo.avg_fill_price, 100.5)
        self.assertAlmostEqual(algo.arrival_slippage_bps, 50.0)  # paid 100.5 vs arrival mid 100.0
        self.assertAlmostEqual(algo.vs_interval_twap_bps, 50.0)

    async def test_unfilled_slice_rolls_into_the_next(self) -> None:
        entry = StubEntry([0.0, 1.0, 1.0, 1.0])  # first slice finds no liquidity in its band
        engine = self.make(entry)
        algo = await engine.create(params(), "sarmad")
        await wait_for(lambda: algo.status not in (AlgoStatus.RUNNING,))
        self.assertEqual(entry.requested, [0.001, 0.002, 0.001, 0.001])
        self.assertEqual(algo.status, AlgoStatus.COMPLETED)

    async def test_shortfall_at_the_end_is_reported_not_hidden(self) -> None:
        entry = StubEntry([1.0, 1.0, 1.0, 0.0])
        engine = self.make(entry)
        algo = await engine.create(params(), "sarmad")
        await wait_for(lambda: algo.status != AlgoStatus.RUNNING)
        self.assertEqual(algo.status, AlgoStatus.EXPIRED)
        self.assertIn("0.001 BTC unfilled", algo.status_reason)

    async def test_kill_switch_pauses_and_blocks_resume_until_released(self) -> None:
        entry = StubEntry()
        engine = self.make(entry)
        algo = await engine.create(params(slices=3, duration=1.5, qty=0.003), "sarmad")
        await wait_for(lambda: len(entry.requested) == 1)
        await self.risk.kill_switch.activate(reason="panel demo", activated_by="risk_mgr")
        await wait_for(lambda: algo.status == AlgoStatus.PAUSED)
        self.assertIn("Kill switch engaged", algo.status_reason)
        await asyncio.sleep(0.6)
        self.assertEqual(len(entry.requested), 1)  # nothing sent while paused
        with self.assertRaises(AlgoRejectedError):
            await engine.resume(algo.id, "sarmad")
        await self.risk.kill_switch.deactivate("risk_mgr")
        await engine.resume(algo.id, "sarmad")
        await wait_for(lambda: algo.status == AlgoStatus.COMPLETED)
        self.assertAlmostEqual(algo.filled_quantity, 0.003)

    async def test_three_rejections_in_a_row_pause_with_the_reason(self) -> None:
        entry = StubEntry([None, None, None, None])
        engine = self.make(entry)
        algo = await engine.create(params(), "sarmad")
        await wait_for(lambda: algo.status == AlgoStatus.PAUSED)
        self.assertEqual(len(entry.requested), 3)
        self.assertIn("position limit", algo.status_reason)

    async def test_cancel_stops_the_schedule(self) -> None:
        entry = StubEntry()
        engine = self.make(entry)
        algo = await engine.create(params(slices=4, duration=2.0), "sarmad")
        await wait_for(lambda: len(entry.requested) == 1)
        await engine.cancel(algo.id, "sarmad")
        await asyncio.sleep(0.7)
        self.assertEqual(algo.status, AlgoStatus.CANCELLED)
        self.assertEqual(len(entry.requested), 1)
        self.assertTrue(all(s.status == "SKIPPED" for s in algo.slices[1:]))

    async def test_slices_below_the_lot_size_are_refused_upfront(self) -> None:
        engine = self.make(StubEntry())
        with self.assertRaises(AlgoRejectedError) as ctx:
            await engine.create(params(qty=0.004, slices=5), "sarmad")  # 0.0008 < the 0.001 lot
        self.assertIn("below the lot size", ctx.exception.message)

    async def test_slices_below_every_venue_minimum_value_are_refused_upfront(self) -> None:
        engine = self.make(StubEntry())
        StubAdapter.rules_min_notional = 1.0  # 4 slices of 0.001 BTC at $100 = $0.10 each, below $1
        try:
            with self.assertRaises(AlgoRejectedError) as ctx:
                await engine.create(params(qty=0.004, slices=4), "sarmad")
            self.assertIn("smaller than any venue accepts", ctx.exception.message)
        finally:
            StubAdapter.rules_min_notional = 0.05

    async def test_parent_value_limit_stops_slicing_around_the_order_limit(self) -> None:
        engine = self.make(StubEntry(), limits=RiskLimits(max_algo_notional_usdt=0.3))
        with self.assertRaises(AlgoRejectedError) as ctx:
            await engine.create(params(), "sarmad")  # 0.004 BTC x $100 = $0.40
        self.assertIn("algo limit", ctx.exception.message)

    async def test_restart_leaves_a_running_twap_paused(self) -> None:
        store = AuditStore(":memory:")
        await store.open()
        self.addAsyncCleanup(store.close)
        running = TwapState(id="TWAP-ABC123", created_by="sarmad", params=params(), interval_s=0.05,
                            nominal_slice=0.001, lot_step=0.001,
                            slices=plan_schedule(params(), 0.004, datetime.now(UTC), 0.001))
        store.upsert_algo(running)
        await store.flush()

        engine = self.make(StubEntry(), store=store)
        await engine.load()
        restored = engine.get("TWAP-ABC123")
        self.assertEqual(restored.status, AlgoStatus.PAUSED)
        self.assertIn("restarted", restored.status_reason)


if __name__ == "__main__":
    unittest.main()
