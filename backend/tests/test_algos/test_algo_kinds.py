"""VWAP, implementation shortfall, POV, iceberg and auto through the algo engine (stubbed market and entry)."""

import asyncio
import unittest
from unittest import mock

import backend.shared.models.strategy as strategy_models
from backend.exchanges.base import SymbolRules
from backend.services.algos.engine import AlgoEngine, AlgoRejectedError
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.shared.database.store import AuditStore
from backend.shared.models.market_data import NBBO, MinuteBar, VenueQuote
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderType, RoutingStrategy
from backend.shared.models.risk import RiskLimits
from backend.shared.models.strategy import (
    AlgoCreate,
    AlgoKind,
    AlgoStatus,
    RecommendRequest,
    Urgency,
)


class Entry:
    """Fills every child in full at 100.5 and remembers what was asked."""

    def __init__(self) -> None:
        self.sent: list[tuple[float, OrderType, float | None, RoutingStrategy]] = []

    async def submit(self, order_in, submitted_by, parent_id=None):
        self.sent.append((order_in.quantity, order_in.order_type, order_in.limit_price, order_in.strategy))
        order = Order(symbol=order_in.symbol, side=order_in.side, quantity=order_in.quantity,
                      order_type=order_in.order_type, submitted_by=submitted_by, parent_id=parent_id)
        child = ChildOrder(parent_order_id=order.id, venue_id="V1", quantity=order_in.quantity, price=100.0)
        child.filled_quantity, child.fill_price = order_in.quantity, 100.5
        order.child_orders = [child]
        order.filled_quantity, order.avg_fill_price = order_in.quantity, 100.5
        return order, None

    @property
    def quantities(self) -> list[float]:
        return [q for q, *_ in self.sent]


class Adapter:
    def rules(self, symbol: str) -> SymbolRules:
        return SymbolRules(symbol=symbol, venue_symbol="BTCUSDT", base="BTC", quote="USDT", amount_step=0.001,
                           min_amount=0.001, price_tick=0.01, min_notional=0.05)


class Market:
    """Aggregator + collector + router + registry + wallet, with a settable ask."""

    symbols = ["BTC/USDT"]

    def __init__(self) -> None:
        self.ask = 100.1
        self.depth = {"asks": [(100.1, 0.002), (100.2, 0.002), (100.4, 1.0)], "bids": [(99.9, 1.0)]}

    async def compute_nbbo(self, symbol: str) -> NBBO:
        q = VenueQuote(venue_id="V1", symbol=symbol, bid_price=99.9, ask_price=self.ask, bid_size=1, ask_size=1,
                       last_price=100, volume=0)
        return NBBO(symbol=symbol, best_bid=99.9, best_ask=self.ask, best_bid_venue="V1", best_ask_venue="V1",
                    best_bid_size=1, best_ask_size=1, spread=self.ask - 99.9, venue_quotes={"V1": q})

    def get_depth(self, venue_id, symbol, levels):
        return self.depth

    def executable_venues(self, symbol=None):
        return ["V1"]

    def excluded_venues(self, symbol=None):
        return {}

    async def venue_capacity(self, order, venues, nbbo):
        return {}

    def get(self, venue_id):
        return Adapter()

    def price_usdt(self, asset, venue_id):
        return None


class TwoVenueMarket(Market):
    """V1 needs 0.003 per order; V6 would take 0.001 but holds no money for this order."""

    def executable_venues(self, symbol=None):
        return ["V1", "V6"]

    def get(self, venue_id):
        rules = Adapter().rules("BTC/USDT").model_copy(update={"min_amount": 0.003 if venue_id == "V1" else 0.001})
        return type("A", (), {"rules": lambda self, s: rules})()

    async def venue_capacity(self, order, venues, nbbo):
        return {"V1": 1.0, "V6": 0.0}


class History:
    """Past days with a rising volume shape; live volume that grows by ``per_call`` each call."""

    def __init__(self, per_call: float = 0.01, fail: bool = False) -> None:
        self.per_call, self.fail, self.calls, self.live = per_call, fail, 0, 0.0

    async def past_days(self, symbol, start_ms, duration_s, days):
        if self.fail:
            raise TimeoutError("exchange too slow")
        starts = [start_ms - d * 86_400_000 for d in range(1, days + 1)]
        # 4 equal minutes per window with volumes 1, 2, 3, 4 -> weights 0.1 .. 0.4 for 4 slices
        bars = [[MinuteBar(ts_ms=s + i * int(duration_s * 1000 / 4), volume=i + 1, pv=100 * (i + 1))
                 for i in range(4)] for s in starts]
        return bars, starts, ["V1", "V4"]

    async def minute_bars(self, symbol, start_ms, end_ms):
        if self.fail:
            raise TimeoutError("exchange too slow")
        self.calls += 1
        self.live += self.per_call
        return [MinuteBar(ts_ms=start_ms, volume=self.live, pv=100.2 * self.live)], ["V1"]

    async def recent_bars(self, symbol, minutes):
        return [MinuteBar(ts_ms=0, volume=0.06 * minutes, pv=0)], ["V1"]


async def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


def algo(kind: AlgoKind, qty: float = 0.01, **kw) -> AlgoCreate:
    return AlgoCreate(kind=kind, symbol="BTC/USDT", side=kw.pop("side", OrderSide.BUY), quantity=qty,
                      duration_s=kw.pop("duration", 0.4), **kw)


class AlgoKindsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(strategy_models, "MIN_SLICE_INTERVAL_S", 0.01)  # 100x faster than real
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, history=None, limits: RiskLimits | None = None, store=None) -> AlgoEngine:
        self.entry, self.market = Entry(), Market()
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), limits or RiskLimits())
        m = self.market
        engine = AlgoEngine(self.entry, None, m, m, m, m, self.risk, m, store=store, history=history)
        engine.start()
        self.addAsyncCleanup(engine.stop)
        return engine

    # ── VWAP ──
    async def test_vwap_follows_the_volume_profile_and_is_judged_against_the_market(self) -> None:
        engine = self.make(History())
        # A real window is minutes long; this one is 0.4 s, so the profile (tested on its own in
        # test_schedules) is given: the market trades 10 / 20 / 30 / 40 % of the window's volume per slice.
        with mock.patch("backend.services.algos.engine.volume_profile", return_value=[0.1, 0.2, 0.3, 0.4]) as vp:
            a = await engine.create(algo(AlgoKind.VWAP, slices=4), "sarmad")
        days, starts, duration, buckets = vp.call_args.args
        self.assertEqual((len(days), duration, buckets), (5, 0.4, 4))  # 5 past days of the same window
        self.assertEqual(a.plan["profile"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(a.plan["profile_venues"], ["V1", "V4"])
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual(self.entry.quantities, [0.001, 0.002, 0.003, 0.004])
        self.assertAlmostEqual(a.market_vwap, 100.2)
        self.assertAlmostEqual(a.vs_market_vwap_bps, (100.5 - 100.2) / 100.2 * 1e4, places=3)
        self.assertIn("vs market VWAP", a.status_reason)

    async def test_vwap_without_history_falls_back_to_even_slices_and_says_so(self) -> None:
        engine = self.make(History(fail=True))
        a = await engine.create(algo(AlgoKind.VWAP, slices=4, qty=0.004), "sarmad")
        self.assertTrue(any("falls back to even slices" in n for n in a.notes))
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual(self.entry.quantities, [0.001] * 4)

    async def test_children_use_the_chosen_routing_strategy(self) -> None:
        engine = self.make(History())
        a = await engine.create(algo(AlgoKind.TWAP, slices=2, qty=0.002, routing=RoutingStrategy.SMART), "sarmad")
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual({s for *_, s in self.entry.sent}, {RoutingStrategy.SMART})

    # ── implementation shortfall ──
    async def test_is_front_loads_by_urgency_and_shows_cost_and_risk(self) -> None:
        engine = self.make(History())
        fast = await engine.create(algo(AlgoKind.IS, qty=0.02, slices=5, urgency=Urgency.HIGH), "sarmad")
        await wait_for(lambda: fast.status == AlgoStatus.COMPLETED)
        sizes = self.entry.quantities
        self.assertGreater(sizes[0], sizes[-1])
        self.assertAlmostEqual(sum(sizes), 0.02)
        self.assertGreater(fast.plan["front_load_pct"], 40)
        self.assertGreater(fast.plan["expected_cost_usdt"], fast.plan["twap_cost_usdt"])
        self.assertTrue(any("Almgren" in n for n in fast.notes))

        slow = await engine.create(algo(AlgoKind.IS, qty=0.02, slices=5, urgency=Urgency.LOW), "sarmad")
        self.assertLess(slow.plan["front_load_pct"], fast.plan["front_load_pct"])

    # ── POV ──
    async def test_pov_trades_its_share_of_the_live_volume(self) -> None:
        engine = self.make(History(per_call=0.01))
        a = await engine.create(algo(AlgoKind.POV, qty=0.004, participation_pct=10, check_interval_s=0.02,
                                     duration=5.0), "sarmad")
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertAlmostEqual(sum(self.entry.quantities), 0.004)
        self.assertTrue(all(q >= 0.001 for q in self.entry.quantities))  # never below a tradable size
        self.assertGreater(a.participation_actual_pct, 5)
        self.assertLessEqual(a.participation_actual_pct, 12)

    async def test_pov_waits_while_the_market_is_quiet_and_expires_at_its_deadline(self) -> None:
        engine = self.make(History(per_call=0.0001))  # 10 % of a trickle never reaches one lot
        a = await engine.create(algo(AlgoKind.POV, qty=0.004, participation_pct=10, check_interval_s=0.02,
                                     duration=0.3), "sarmad")
        await wait_for(lambda: a.status != AlgoStatus.RUNNING)
        self.assertEqual(a.status, AlgoStatus.EXPIRED)
        self.assertEqual(self.entry.sent, [])
        self.assertIn("Deadline reached", a.status_reason)

    async def test_pov_pause_moves_the_deadline_on(self) -> None:
        engine = self.make(History(per_call=0.0001))
        a = await engine.create(algo(AlgoKind.POV, qty=0.004, participation_pct=10, check_interval_s=0.02,
                                     duration=10.0), "sarmad")
        deadline = a.deadline_at
        await engine.pause(a.id, "sarmad")
        await asyncio.sleep(0.2)
        await engine.resume(a.id, "sarmad")
        self.assertGreaterEqual((a.deadline_at - deadline).total_seconds(), 0.2)

    async def test_pov_needs_a_volume_source(self) -> None:
        engine = self.make(history=None)
        a = await engine.create(algo(AlgoKind.POV, qty=0.004, participation_pct=10, check_interval_s=0.02,
                                     duration=5.0), "sarmad")
        await wait_for(lambda: a.status == AlgoStatus.PAUSED)
        self.assertIn("real-market volume", a.status_reason)

    # ── iceberg ──
    async def test_iceberg_takes_small_pieces_at_the_limit(self) -> None:
        engine = self.make()
        a = await engine.create(algo(AlgoKind.ICEBERG, qty=0.01, limit_price=100.2, display_qty=0.003,
                                     refresh_s=0.02, size_variance_pct=0, duration=5.0), "sarmad")
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual(self.entry.quantities, [0.003, 0.003, 0.003, 0.001])  # 0.001 is still a tradable piece
        self.assertTrue(all(t == OrderType.LIMIT and lim == 100.2 for _, t, lim, _ in self.entry.sent))

    async def test_iceberg_waits_until_the_price_comes_to_the_limit(self) -> None:
        engine = self.make()
        self.market.ask = 100.5  # above the limit
        a = await engine.create(algo(AlgoKind.ICEBERG, qty=0.002, limit_price=100.2, display_qty=0.002,
                                     refresh_s=0.02, size_variance_pct=0, duration=5.0), "sarmad")
        await asyncio.sleep(0.15)
        self.assertEqual(self.entry.sent, [])
        self.assertIn("beyond the limit", a.waiting_reason)
        self.market.ask = 100.15
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual(self.entry.quantities, [0.002])

    async def test_iceberg_display_below_any_venue_minimum_is_refused(self) -> None:
        engine = self.make()
        with self.assertRaises(AlgoRejectedError):
            await engine.create(algo(AlgoKind.ICEBERG, qty=0.01, limit_price=100.2, display_qty=0.0005,
                                     refresh_s=0.02, duration=5.0), "sarmad")

    # ── auto ──
    async def test_auto_recommends_one_order_for_a_small_order(self) -> None:
        engine = self.make(History())
        rec = await engine.recommend(RecommendRequest(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.002))
        self.assertEqual(rec.choice, "SINGLE")
        self.assertIsNone(rec.params)
        self.assertAlmostEqual(rec.metrics["volume_per_min"], 0.06)

    async def test_auto_recommends_a_startable_algo_for_a_large_order(self) -> None:
        engine = self.make(History())
        rec = await engine.recommend(RecommendRequest(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.05,
                                                      urgency=Urgency.HIGH, max_duration_s=600))
        self.assertEqual(rec.choice, "IS")
        # Walking the book for 0.05: (0.002 @ 100.1 + 0.002 @ 100.2 + 0.046 @ 100.4) / 0.05 = 100.38 vs mid 100.0
        self.assertAlmostEqual(rec.metrics["impact_now_bps"], 38.0, places=1)
        self.assertTrue(any("High urgency" in r for r in rec.reasons))
        started = await engine.create(rec.params, "sarmad")
        self.assertEqual(started.kind, "IS")

    # ── minimums ──
    async def test_the_minimum_comes_from_venues_that_can_fund_the_order(self) -> None:
        engine = self.make()
        engine._aggregator = engine._collector = engine._router = engine._registry = TwoVenueMarket()
        a = await engine.create(algo(AlgoKind.IS, qty=0.012, slices=4, urgency=Urgency.HIGH), "sarmad")
        self.assertEqual(a.plan["min_tradable"], 0.003)  # not V6's 0.001: V6 can't pay
        # Almgren-Chriss alone would plan 0.008, 0.003, 0, 0.001; the 0.001 joins the last slice that trades.
        self.assertEqual([s.target_cumulative for s in a.slices], [0.008, 0.012, 0.012, 0.012])
        self.assertTrue(any("merged into their neighbours" in n for n in a.notes))
        await wait_for(lambda: a.status == AlgoStatus.COMPLETED)
        self.assertEqual(self.entry.quantities, [0.008, 0.004])

    # ── restart ──
    async def test_a_running_pov_comes_back_paused_after_a_restart(self) -> None:
        store = AuditStore(":memory:")
        await store.open()
        self.addAsyncCleanup(store.close)
        engine = self.make(History(per_call=0.0001), store=store)
        a = await engine.create(algo(AlgoKind.POV, qty=0.004, participation_pct=10, check_interval_s=0.02,
                                     duration=10.0), "sarmad")
        await store.flush()
        fresh = self.make(History(), store=store)
        await fresh.load()
        restored = fresh.get(a.id)
        self.assertEqual((restored.kind, restored.status), ("POV", AlgoStatus.PAUSED))
        self.assertIsNotNone(restored.deadline_at)


class ModelTest(unittest.TestCase):
    def test_each_kind_requires_its_own_parameters(self) -> None:
        with self.assertRaises(ValueError):
            AlgoCreate(kind=AlgoKind.VWAP, symbol="BTC/USDT", side=OrderSide.BUY, quantity=1, duration_s=60)
        with self.assertRaises(ValueError):
            AlgoCreate(kind=AlgoKind.POV, symbol="BTC/USDT", side=OrderSide.BUY, quantity=1, duration_s=60)
        with self.assertRaises(ValueError):
            AlgoCreate(kind=AlgoKind.ICEBERG, symbol="BTC/USDT", side=OrderSide.BUY, quantity=1, duration_s=60,
                       display_qty=0.1)

    def test_an_old_stored_twap_still_loads(self) -> None:
        old = {"symbol": "BTC/USDT", "side": "BUY", "quantity": 0.004, "duration_s": 60, "slices": 4,
               "limit_price": None, "jitter_pct": 0.0}
        p = AlgoCreate.model_validate(old)
        self.assertEqual((p.kind, p.routing), (AlgoKind.TWAP, RoutingStrategy.BEST_PRICE))


if __name__ == "__main__":
    unittest.main()
