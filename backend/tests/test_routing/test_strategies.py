"""The three routing strategies: fee-aware best price, liquidity sweep, smart (fill reliability)."""

import unittest

from pydantic import ValidationError

from backend.services.routing_engine.strategies import (
    BestPriceStrategy,
    LiquiditySweepStrategy,
    SmartStrategy,
    build_strategies,
)
from backend.services.routing_engine.strategies.best_price import all_in_price, fee_bps_for
from backend.services.routing_engine.strategies.smart import MIN_SAMPLES, miss_penalty_bps
from backend.services.routing_engine.strategies.sweep import proportional_shares
from backend.shared.models.market_data import NBBO, VenueQuote
from backend.shared.models.order import Order, OrderCreate, OrderSide, OrderType, RoutingStrategy
from backend.shared.models.routing import RoutingStatus


def quote(vid: str, bid: float, ask: float, size: float = 1.0) -> VenueQuote:
    return VenueQuote(venue_id=vid, symbol="BTC/USDT", bid_price=bid, ask_price=ask,
                      bid_size=size, ask_size=size, last_price=ask, volume=0)


def nbbo_of(*quotes: VenueQuote) -> NBBO:
    bid = max(quotes, key=lambda q: q.bid_price)
    ask = min(quotes, key=lambda q: q.ask_price)
    return NBBO(symbol="BTC/USDT", best_bid=bid.bid_price, best_bid_venue=bid.venue_id, best_bid_size=bid.bid_size,
                best_ask=ask.ask_price, best_ask_venue=ask.venue_id, best_ask_size=ask.ask_size,
                spread=ask.ask_price - bid.bid_price, venue_quotes={q.venue_id: q for q in quotes})


def order(qty: float, side: OrderSide = OrderSide.BUY, limit: float | None = None,
          strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE) -> Order:
    return Order(symbol="BTC/USDT", side=side, quantity=qty, strategy=strategy,
                 order_type=OrderType.LIMIT if limit else OrderType.MARKET, limit_price=limit)


class FeeAwareBestPriceTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_higher_fee_loses_to_a_slightly_worse_price(self) -> None:
        # V1 asks 100.00 but charges 20 bps (all-in 100.20); V2 asks 100.05 at 10 bps (all-in 100.15).
        nbbo = nbbo_of(quote("V1", 99.9, 100.00), quote("V2", 99.9, 100.05))
        result = await BestPriceStrategy().route(order(0.5), nbbo, ["V1", "V2"], fees_bps={"V1": 20, "V2": 10})
        self.assertEqual([c.venue_id for c in result.child_orders], ["V2"])
        d = result.decision
        self.assertEqual(d.strategy, "best_price")
        self.assertAlmostEqual(d.expected_fees_quote, 0.5 * 100.05 * 0.001)
        self.assertAlmostEqual(d.all_in_avg_price, 100.05 * 1.001)
        self.assertIn("quoted the better raw ask", " ".join(d.notes))
        v1 = next(c for c in d.candidates if c.venue_id == "V1")
        self.assertEqual(v1.fee_bps, 20)
        self.assertAlmostEqual(v1.effective_price, 100.2)

    async def test_sell_ranks_by_what_it_brings_in_after_fees(self) -> None:
        # Selling: V1 bids 100.00 at 20 bps (receives 99.80); V2 bids 99.95 at 10 bps (99.85).
        nbbo = nbbo_of(quote("V1", 100.00, 100.1), quote("V2", 99.95, 100.1))
        result = await BestPriceStrategy().route(order(0.5, OrderSide.SELL), nbbo, ["V1", "V2"],
                                                 fees_bps={"V1": 20, "V2": 10})
        self.assertEqual([c.venue_id for c in result.child_orders], ["V2"])
        self.assertAlmostEqual(result.decision.all_in_avg_price, 99.95 * 0.999)

    async def test_the_walk_interleaves_levels_by_all_in_price(self) -> None:
        depth = {"V1": {"asks": [(100.0, 1.0), (100.3, 5.0)], "bids": []},
                 "V2": {"asks": [(100.1, 1.0), (100.2, 5.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.9, 100.0), quote("V2", 99.9, 100.1))
        # With V1 at 15 bps: V1@100.0 -> 100.15, V2@100.1 -> 100.20 (5 bps), V2@100.2 -> 100.25, V1@100.3 -> 100.45
        result = await BestPriceStrategy().route(order(2.5), nbbo, ["V1", "V2"], depth=depth,
                                                 fees_bps={"V1": 15, "V2": 5})
        self.assertEqual({c.venue_id: c.quantity for c in result.child_orders}, {"V1": 1.0, "V2": 1.5})

    def test_unknown_fee_is_the_highest_listed_and_no_table_means_none(self) -> None:
        self.assertEqual(fee_bps_for({"V1": 10, "V4": 20}, "V6"), 20)
        self.assertEqual(fee_bps_for({}, "V6"), 0)
        self.assertAlmostEqual(all_in_price(100.0, 10, True), 100.1)
        self.assertAlmostEqual(all_in_price(100.0, 10, False), 99.9)

    async def test_without_fees_routing_is_unchanged(self) -> None:
        nbbo = nbbo_of(quote("V1", 99.9, 100.00), quote("V2", 99.9, 100.05))
        result = await BestPriceStrategy().route(order(0.5), nbbo, ["V1", "V2"])
        self.assertEqual([c.venue_id for c in result.child_orders], ["V1"])
        self.assertEqual(result.decision.expected_fees_quote, 0)
        self.assertAlmostEqual(result.decision.all_in_avg_price, 100.0)


class LiquiditySweepTest(unittest.IsolatedAsyncioTestCase):
    def test_shares_follow_the_available_liquidity(self) -> None:
        shares = proportional_shares(1.0, {"V1": 3.0, "V2": 1.0}, {"V1": 0.001, "V2": 0.001}, ["V1", "V2"])
        self.assertEqual(shares, {"V1": 0.75, "V2": 0.25})

    def test_lots_lost_to_rounding_go_to_the_best_venue_first(self) -> None:
        shares = proportional_shares(0.007, {"V1": 0.005, "V2": 0.005}, {"V1": 0.001, "V2": 0.001}, ["V2", "V1"])
        self.assertEqual(shares, {"V1": 0.003, "V2": 0.004})
        self.assertAlmostEqual(sum(shares.values()), 0.007)

    def test_not_enough_liquidity_takes_all_of_it(self) -> None:
        self.assertEqual(proportional_shares(5.0, {"V1": 1.0, "V2": 2.0}, {}, ["V1", "V2"]), {"V1": 1.0, "V2": 2.0})

    async def test_every_venue_gets_a_share_with_a_final_price_cap(self) -> None:
        # Best price would put everything on V1 (enough at 100.00); the sweep spreads it by depth.
        depth = {"V1": {"asks": [(100.00, 3.0)], "bids": []},
                 "V2": {"asks": [(100.01, 1.0), (101.00, 9.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.9, 100.00, 3.0), quote("V2", 99.9, 100.01))
        best = await BestPriceStrategy().route(order(2.0), nbbo, ["V1", "V2"], depth=depth)
        self.assertEqual([c.venue_id for c in best.child_orders], ["V1"])

        result = await LiquiditySweepStrategy(band_bps=20).route(order(2.0), nbbo, ["V1", "V2"], depth=depth)
        alloc = {c.venue_id: c.quantity for c in result.child_orders}
        self.assertEqual(alloc, {"V1": 1.5, "V2": 0.5})  # 3 : 1 inside the cap (101.00 is outside)
        cap = 100.00 * 1.002
        self.assertAlmostEqual(result.decision.price_cap, cap)
        for c in result.child_orders:
            self.assertTrue(c.limit_final)
            self.assertAlmostEqual(c.limit_price, cap)
        self.assertEqual(result.decision.strategy, "liquidity_sweep")
        self.assertIn("every venue at once", " ".join(result.decision.notes))

    async def test_liquidity_beyond_the_cap_is_never_used(self) -> None:
        depth = {"V1": {"asks": [(100.00, 0.5), (100.50, 9.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.9, 100.00, 0.5))
        result = await LiquiditySweepStrategy(band_bps=20).route(order(2.0), nbbo, ["V1"], depth=depth)
        self.assertEqual(result.status, RoutingStatus.PARTIAL)
        self.assertAlmostEqual(result.child_orders[0].quantity, 0.5)
        self.assertIn("within the price cap exhausted", " ".join(result.decision.notes))

    async def test_the_cap_includes_each_venues_fee(self) -> None:
        # Best all-in: V1 100.00 + 10 bps = 100.10; cap = 100.10 * 1.002 = 100.3002 all-in.
        # V2 charges 30 bps: its raw cap is 100.3002 / 1.003 = 100.0002 - its 100.05 ask is outside.
        depth = {"V1": {"asks": [(100.00, 1.0)], "bids": []}, "V2": {"asks": [(100.05, 5.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.9, 100.00), quote("V2", 99.9, 100.05, 5.0))
        result = await LiquiditySweepStrategy(band_bps=20).route(order(3.0), nbbo, ["V1", "V2"], depth=depth,
                                                                 fees_bps={"V1": 10, "V2": 30})
        self.assertEqual({c.venue_id for c in result.child_orders}, {"V1"})

    async def test_a_limit_tighter_than_the_band_is_the_cap(self) -> None:
        depth = {"V1": {"asks": [(100.00, 1.0), (100.10, 1.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.9, 100.00))
        result = await LiquiditySweepStrategy(band_bps=50).route(order(2.0, limit=100.05), nbbo, ["V1"], depth=depth)
        self.assertAlmostEqual(result.child_orders[0].quantity, 1.0)
        self.assertAlmostEqual(result.child_orders[0].limit_price, 100.05)

    def test_band_must_be_sane(self) -> None:
        with self.assertRaises(ValueError):
            LiquiditySweepStrategy(band_bps=0)


class SmartRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_penalty_is_miss_probability_times_miss_cost(self) -> None:
        self.assertAlmostEqual(miss_penalty_bps(0.5, gap_bps=2.0, spread_bps=10.0), 5.0)
        self.assertAlmostEqual(miss_penalty_bps(0.8, gap_bps=15.0, spread_bps=10.0), 3.0)
        self.assertEqual(miss_penalty_bps(1.0, 15.0, 10.0), 0.0)
        self.assertEqual(miss_penalty_bps(0.5, -3.0, 0.0), 0.0)  # a miss never makes a venue cheaper

    async def test_an_unreliable_best_price_loses_to_a_reliable_one(self) -> None:
        # V1 is 2 bps cheaper but fills half of what we send; spread 10 bps -> +5 bps expected.
        nbbo = nbbo_of(quote("V1", 99.90, 100.00), quote("V2", 99.90, 100.02))
        ratios = {"V1": (0.5, 10), "V2": (1.0, 10)}
        best = await BestPriceStrategy().route(order(0.5), nbbo, ["V1", "V2"], fill_ratios=ratios)
        self.assertEqual([c.venue_id for c in best.child_orders], ["V1"])

        smart = await SmartStrategy().route(order(0.5), nbbo, ["V1", "V2"], fill_ratios=ratios)
        self.assertEqual([c.venue_id for c in smart.child_orders], ["V2"])
        v1 = next(c for c in smart.decision.candidates if c.venue_id == "V1")
        self.assertAlmostEqual(v1.extra_cost_bps, 5.0, places=1)
        self.assertEqual(v1.fill_ratio, 0.5)
        self.assertIn("V1 filled 50%", " ".join(smart.decision.notes))

    async def test_no_history_means_price_and_fee_only(self) -> None:
        nbbo = nbbo_of(quote("V1", 99.90, 100.00), quote("V2", 99.90, 100.02))
        smart = await SmartStrategy().route(order(0.5), nbbo, ["V1", "V2"],
                                            fill_ratios={"V1": (0.1, MIN_SAMPLES - 1)})
        self.assertEqual([c.venue_id for c in smart.child_orders], ["V1"])
        self.assertIn("no fill history yet for V1, V2", " ".join(smart.decision.notes))


class StrategyChoiceTest(unittest.TestCase):
    def test_every_listed_strategy_has_an_implementation(self) -> None:
        self.assertEqual(set(build_strategies()), set(RoutingStrategy))

    def test_vwap_is_refused_with_where_to_find_it(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.001, strategy="vwap")
        self.assertIn("algo order", str(ctx.exception))

    def test_a_stored_vwap_order_restores_as_what_really_ran(self) -> None:
        o = Order.model_validate({"symbol": "BTC/USDT", "side": "BUY", "quantity": 1, "order_type": "MARKET",
                                  "strategy": "vwap"})
        self.assertEqual(o.strategy, RoutingStrategy.BEST_PRICE)


if __name__ == "__main__":
    unittest.main()
