"""The routing engine runs the strategy each order asks for, and measures each venue's fill ratio."""

import unittest

from backend.exchanges.base import ExecutionResult
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.routing_engine.engine import RoutingEngine
from backend.shared.models.order import OrderCreate, OrderSide, OrderStatus, RoutingStrategy
from backend.tests.test_routing.test_reroute import StubMarket, StubVenue


class RecordingVenue(StubVenue):
    """Remembers the limit price each child was sent with."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.limits: list[float] = []

    async def place_order(self, *, quantity: float, limit_price: float | None = None, **kw) -> ExecutionResult:
        self.limits.append(limit_price)
        return await super().place_order(quantity=quantity, **kw)


class EngineStrategyTest(unittest.IsolatedAsyncioTestCase):
    def make(self, *venues: StubVenue, fees: dict[str, float] | None = None) -> RoutingEngine:
        market = StubMarket(list(venues))
        self.orders = OrderManager()
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker())
        return RoutingEngine(market, market, self.risk, market, self.orders, market, taker_fees_bps=fees,
                             sweep_band_bps=20)

    async def submit(self, engine: RoutingEngine, qty: float, strategy: RoutingStrategy):
        o = await self.orders.create_order(
            OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=qty, strategy=strategy), submitted_by="t")
        await engine.route_order(o)
        return self.orders.get_order(o.id)

    async def test_sweep_order_hits_every_venue_with_the_cap_as_its_limit(self) -> None:
        v1, v4 = RecordingVenue("V1", ask=100.00, ask_size=3.0), RecordingVenue("V4", ask=100.01, ask_size=1.0)
        engine = self.make(v1, v4)
        done = await self.submit(engine, 2.0, RoutingStrategy.LIQUIDITY_SWEEP)
        self.assertEqual(done.status, OrderStatus.FILLED)
        self.assertEqual((v1.sent, v4.sent), ([1.5], [0.5]))
        cap = 100.00 * 1.002
        # Sent as is (rounded to the 0.01 tick toward the safe side), not widened by the 50 bps band.
        self.assertEqual(v1.limits, [round(cap - 0.005, 2)])
        self.assertEqual(done.routing_decision["strategy"], "liquidity_sweep")

    async def test_best_price_order_is_unchanged_by_default(self) -> None:
        v1, v4 = RecordingVenue("V1", ask=100.00, ask_size=3.0), RecordingVenue("V4", ask=100.01, ask_size=1.0)
        engine = self.make(v1, v4)
        await self.submit(engine, 2.0, RoutingStrategy.BEST_PRICE)
        self.assertEqual((v1.sent, v4.sent), ([2.0], []))
        self.assertAlmostEqual(v1.limits[0], 100.5)  # planned 100.00 widened by the 50 bps protection band

    async def test_fees_reach_the_strategy(self) -> None:
        v1, v4 = StubVenue("V1", ask=100.00), StubVenue("V4", ask=100.05)
        engine = self.make(v1, v4, fees={"V1": 20, "V4": 10})
        await self.submit(engine, 1.0, RoutingStrategy.BEST_PRICE)
        self.assertEqual((v1.sent, v4.sent), ([], [1.0]))

    async def test_fill_ratio_is_measured_and_steers_smart_routing(self) -> None:
        v1, v4 = StubVenue("V1", ask=100.00, outcome="expire"), StubVenue("V4", ask=100.02)
        engine = self.make(v1, v4)
        for _ in range(5):  # V1 is the best price, but its IOCs keep expiring
            await self.submit(engine, 0.5, RoutingStrategy.BEST_PRICE)
        self.assertEqual(engine.fill_ratios()["V1"], (0.0, 5))
        self.assertEqual(engine.stats["fill_ratios"]["V1"], {"ratio": 0.0, "orders": 5})
        smart = await self.submit(engine, 0.5, RoutingStrategy.SMART)
        self.assertEqual([c.venue_id for c in smart.child_orders], ["V4"])
        self.assertEqual(smart.status, OrderStatus.FILLED)

    async def test_refusals_for_our_own_reasons_do_not_count_against_a_venue(self) -> None:
        v1 = StubVenue("V1", ask=100.00, outcome="refuse")  # e.g. our balance - not the venue's reliability
        engine = self.make(v1, StubVenue("V4", ask=100.02))
        await self.submit(engine, 0.5, RoutingStrategy.BEST_PRICE)
        self.assertNotIn("V1", engine.fill_ratios())

    async def test_restored_history_seeds_the_fill_ratio(self) -> None:
        engine = self.make(StubVenue("V1", ask=100.0))
        o = await self.submit(engine, 0.5, RoutingStrategy.BEST_PRICE)
        fresh = self.make(StubVenue("V1", ask=100.0))
        fresh.restore_stats([o])
        self.assertEqual(fresh.fill_ratios()["V1"], (1.0, 1))


if __name__ == "__main__":
    unittest.main()
