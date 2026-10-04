"""A venue that refuses doesn't sink the order: re-route rounds and trading blocks.

The whole pipeline runs (order manager, real pre-trade risk, best-price
strategy, routing engine) against scripted stub venues.
"""

import unittest

from backend.exchanges.base import ExecutionResult, SymbolRules
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.routing_engine.engine import MAX_REROUTES, RoutingEngine
from backend.shared.events.event_bus import event_bus
from backend.shared.models.execution_report import ExecType
from backend.shared.models.market_data import NBBO, VenueQuote
from backend.shared.models.order import OrderCreate, OrderSide, OrderStatus

PERMISSION = "OKX refused this API key's trading permission: This API Key does not have trading permission"


class StubVenue:
    """Scripted answers: 'fill', 'refuse' (e.g. balance), 'refuse_permission', 'expire'."""

    can_trade = True
    execution_note = None

    def __init__(self, venue_id: str, ask: float, outcome: str = "fill", ask_size: float = 5.0) -> None:
        self.venue_id, self.name = venue_id, f"Venue {venue_id}"
        self.ask, self.ask_size, self.outcome = ask, ask_size, outcome
        self.sent: list[float] = []
        self.on_send = None  # optional async hook, e.g. engage the kill switch mid-order

    def rules(self, symbol: str) -> SymbolRules:
        return SymbolRules(symbol=symbol, venue_symbol="BTCUSDT", base="BTC", quote="USDT", amount_step=0.001,
                           min_amount=0.001, price_tick=0.01, min_notional=1.0)

    async def place_order(self, *, quantity: float, **_) -> ExecutionResult:
        self.sent.append(quantity)
        if self.on_send:
            await self.on_send()
        if self.outcome == "fill":
            return ExecutionResult(exec_type=ExecType.FILL, requested_qty=quantity, filled_qty=quantity,
                                   avg_price=self.ask)
        if self.outcome == "expire":
            return ExecutionResult(exec_type=ExecType.REJECT, requested_qty=quantity, exchange_status="expired",
                                   reason="IOC: no liquidity at the limit price")
        permission = self.outcome == "refuse_permission"
        return ExecutionResult(exec_type=ExecType.REJECT, requested_qty=quantity, retry_elsewhere=True,
                               blocks_trading=permission,
                               reason=PERMISSION if permission else f"insufficient balance on {self.name}")


class StubMarket:
    """Aggregator, collector and venue monitor in one, built from the stub venues' books."""

    symbols = ["BTC/USDT"]

    def __init__(self, venues: list[StubVenue]) -> None:
        self.venues = {v.venue_id: v for v in venues}

    async def compute_nbbo(self, symbol: str) -> NBBO:
        quotes = [VenueQuote(venue_id=v.venue_id, symbol=symbol, bid_price=v.ask - 0.1, ask_price=v.ask,
                             bid_size=v.ask_size, ask_size=v.ask_size, last_price=v.ask, volume=0)
                  for v in self.venues.values()]
        bid = max(quotes, key=lambda q: q.bid_price)
        ask = min(quotes, key=lambda q: q.ask_price)
        return NBBO(symbol=symbol, best_bid=bid.bid_price, best_bid_venue=bid.venue_id, best_bid_size=bid.bid_size,
                    best_ask=ask.ask_price, best_ask_venue=ask.venue_id, best_ask_size=ask.ask_size,
                    spread=ask.ask_price - bid.bid_price, venue_quotes={q.venue_id: q for q in quotes})

    def get_depth(self, venue_id: str, symbol: str, levels: int) -> dict:
        v = self.venues[venue_id]
        return {"asks": [(v.ask, v.ask_size)], "bids": [(v.ask - 0.1, v.ask_size)]}

    def get_routable_venues(self) -> list[str]:
        return list(self.venues)

    def get_excluded_venues(self) -> dict[str, str]:
        return {}

    def record_order_result(self, venue_id: str, filled: bool, venue_fault: bool = True) -> None:
        pass

    # registry
    def all(self) -> list[StubVenue]:
        return list(self.venues.values())

    def get(self, venue_id: str) -> StubVenue | None:
        return self.venues.get(venue_id)


class RerouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[tuple[str, dict]] = []

        async def on_audit(data: dict) -> None:
            self.events.append(("audit", data))

        async def on_decision(data: dict) -> None:
            self.events.append(("routing_decision", data))

        for channel, handler in (("audit", on_audit), ("routing_decision", on_decision)):
            event_bus.subscribe(channel, handler)
            self.addCleanup(event_bus.unsubscribe, channel, handler)
        self.now = 1_000.0

    def make(self, *venues: StubVenue) -> RoutingEngine:
        market = StubMarket(list(venues))
        self.orders = OrderManager()
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker())
        return RoutingEngine(market, market, self.risk, market, self.orders, market, trading_block_s=600,
                             clock=lambda: self.now)

    async def buy(self, engine: RoutingEngine, qty: float = 1.0):
        order = await self.orders.create_order(OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=qty),
                                               submitted_by="trader1")
        await engine.route_order(order)
        return self.orders.get_order(order.id)

    def audits(self, event_type: str) -> list[dict]:
        return [d for kind, d in self.events if kind == "audit" and d["event_type"] == event_type]

    def decisions(self) -> list[str]:
        return [d["summary"] for kind, d in self.events if kind == "routing_decision"]

    async def test_refused_quantity_is_rerouted_and_the_venue_blocked(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse_permission")  # best price, but refuses
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertAlmostEqual(order.filled_quantity, 1.0)
        self.assertEqual((okx.sent, binance.sent), ([1.0], [1.0]))
        self.assertEqual([c.venue_id for c in order.child_orders], ["V5", "V1"])
        self.assertIn("trading permission", order.child_orders[0].reject_reason)
        # V5 is out of routing, with the reason shown wherever exclusions are shown
        self.assertNotIn("V5", engine.executable_venues("BTC/USDT"))
        self.assertIn("trading paused for 10 more min", engine.excluded_venues("BTC/USDT")["V5"])
        [block] = self.audits("venue_trading_blocked")
        self.assertEqual((block["actor"], block["venue_id"]), ("router", "V5"))
        # The proof and the audit trail say what happened after the plan
        self.assertTrue(self.decisions()[1].startswith("Re-route after V5 refused: Routed 1 BTC -> V1"))
        self.assertTrue(any(n.startswith("Re-route after V5 refused") for n in order.routing_decision["notes"]))

    async def test_blocked_venue_gets_nothing_until_the_cooldown_ends(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse_permission")
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)
        await self.buy(engine)

        second = await self.buy(engine)
        self.assertEqual(second.status, OrderStatus.FILLED)
        self.assertEqual(len(okx.sent), 1)  # not asked again
        self.assertEqual([c.venue_id for c in second.child_orders], ["V1"])

        self.now += 601  # cooldown over: the next order there is the probe
        self.assertIn("V5", engine.executable_venues("BTC/USDT"))
        await self.buy(engine)
        self.assertEqual(len(okx.sent), 2)
        self.assertEqual(len(self.audits("venue_trading_blocked")), 2)  # refused again -> blocked again

    async def test_refusal_that_is_not_about_the_key_reroutes_without_blocking(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse")  # e.g. insufficient balance there
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertIn("V5", engine.executable_venues("BTC/USDT"))
        self.assertEqual(self.audits("venue_trading_blocked"), [])

    async def test_split_order_reroutes_only_the_refused_share(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse_permission", ask_size=0.4)
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(okx.sent, [0.4])
        self.assertEqual(binance.sent, [0.6, 0.4])  # its own share, then V5's refused 0.4

    async def test_expired_ioc_is_not_chased_on_other_venues(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="expire")  # no liquidity at our price: a price decision
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(binance.sent, [])
        self.assertEqual(len(self.decisions()), 1)

    async def test_nowhere_else_to_go_rejects_with_the_reason(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse_permission")
        engine = self.make(okx)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("trading permission", order.rejection_reason)
        self.assertTrue(any("not re-routed: no other venue can execute it" in n
                            for n in order.routing_decision["notes"]))

    async def test_kill_switch_stops_the_reroute(self) -> None:
        okx = StubVenue("V5", ask=100.0, outcome="refuse")
        binance = StubVenue("V1", ask=100.1)
        engine = self.make(okx, binance)

        async def halt() -> None:
            await self.risk.kill_switch.activate(reason="operator halt", activated_by="admin")

        okx.on_send = halt
        order = await self.buy(engine)

        self.assertEqual(binance.sent, [])
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertTrue(any("not re-routed: kill switch engaged" in n for n in order.routing_decision["notes"]))

    async def test_reroutes_are_bounded(self) -> None:
        venues = [StubVenue(vid, ask=100.0 + i / 10, outcome="refuse") for i, vid in enumerate(["V5", "V1", "V4", "V6"])]
        engine = self.make(*venues)

        order = await self.buy(engine)

        self.assertEqual(order.status, OrderStatus.REJECTED)
        tried = [v.venue_id for v in venues if v.sent]
        self.assertEqual(tried, ["V5", "V1", "V4"])  # first plan + MAX_REROUTES rounds, each venue once
        self.assertEqual(len(tried), 1 + MAX_REROUTES)
        self.assertEqual(venues[3].sent, [])


if __name__ == "__main__":
    unittest.main()
