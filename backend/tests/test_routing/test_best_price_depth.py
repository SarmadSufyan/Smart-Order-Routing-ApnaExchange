"""Consolidated-book routing: allocation, splits, limits, lot sizes, savings."""

import unittest

from backend.services.routing_engine.strategies.best_price import BestPriceStrategy
from backend.shared.models.market_data import NBBO, VenueQuote
from backend.shared.models.order import Order, OrderSide, OrderType
from backend.shared.models.routing import RoutingStatus


def quote(vid: str, bid: float, ask: float, bid_size: float = 1.0, ask_size: float = 1.0) -> VenueQuote:
    return VenueQuote(venue_id=vid, symbol="BTC/USDT", bid_price=bid, ask_price=ask,
                      bid_size=bid_size, ask_size=ask_size, last_price=ask, volume=0)


def nbbo_of(*quotes: VenueQuote) -> NBBO:
    best_bid = max(quotes, key=lambda q: q.bid_price)
    best_ask = min(quotes, key=lambda q: q.ask_price)
    return NBBO(symbol="BTC/USDT", best_bid=best_bid.bid_price, best_bid_venue=best_bid.venue_id,
                best_bid_size=best_bid.bid_size, best_ask=best_ask.ask_price, best_ask_venue=best_ask.venue_id,
                best_ask_size=best_ask.ask_size, spread=best_ask.ask_price - best_bid.bid_price,
                venue_quotes={q.venue_id: q for q in quotes})


def order(qty: float, side: OrderSide = OrderSide.BUY, limit: float | None = None) -> Order:
    return Order(symbol="BTC/USDT", side=side, quantity=qty,
                 order_type=OrderType.LIMIT if limit else OrderType.MARKET, limit_price=limit)


class BestPriceDepthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.strategy = BestPriceStrategy()

    async def test_single_venue_walks_multiple_levels(self) -> None:
        depth = {"V1": {"asks": [(100.0, 0.5), (101.0, 0.5), (102.0, 5.0)], "bids": [(99.0, 1.0)]}}
        nbbo = nbbo_of(quote("V1", 99.0, 100.0, ask_size=0.5))
        result = await self.strategy.route(order(1.2), nbbo, ["V1"], depth=depth)

        self.assertEqual(result.status, RoutingStatus.SUCCESS)
        self.assertEqual(len(result.child_orders), 1)
        self.assertAlmostEqual(result.child_orders[0].quantity, 1.2)
        # VWAP = (0.5*100 + 0.5*101 + 0.2*102) / 1.2
        self.assertAlmostEqual(result.decision.blended_avg_price, (50 + 50.5 + 20.4) / 1.2, places=6)
        self.assertEqual(result.decision.levels_used, 3)
        self.assertGreater(result.decision.expected_slippage_bps, 0)
        self.assertEqual(result.decision.total_savings, 0)  # nothing to compare against

    async def test_interleaved_levels_split_across_venues(self) -> None:
        depth = {
            "V1": {"asks": [(100.0, 1.0), (103.0, 5.0)], "bids": []},
            "V2": {"asks": [(101.0, 1.0), (102.0, 5.0)], "bids": []},
        }
        nbbo = nbbo_of(quote("V1", 99.0, 100.0), quote("V2", 98.0, 101.0))
        result = await self.strategy.route(order(3.0), nbbo, ["V1", "V2"], depth=depth)

        alloc = {c.venue_id: c.quantity for c in result.child_orders}
        # Ladder: V1@100 (1), V2@101 (1), V2@102 (1 of 5) -> V1 gets 1, V2 gets 2
        self.assertEqual(alloc, {"V1": 1.0, "V2": 2.0})
        self.assertTrue(result.decision.is_split)
        # Sending all 3 to V1 alone would cost 100 + 2*103 = 306; SOR pays 100+101+102 = 303
        self.assertAlmostEqual(result.decision.total_savings, 3.0, places=6)

    async def test_sell_walks_bids_highest_first(self) -> None:
        depth = {"V1": {"bids": [(99.0, 1.0), (98.0, 1.0)], "asks": []},
                 "V2": {"bids": [(99.5, 0.5)], "asks": []}}
        nbbo = nbbo_of(quote("V1", 99.0, 100.0), quote("V2", 99.5, 101.0, bid_size=0.5))
        result = await self.strategy.route(order(1.2, OrderSide.SELL), nbbo, ["V1", "V2"], depth=depth)
        alloc = {c.venue_id: round(c.quantity, 8) for c in result.child_orders}
        self.assertEqual(alloc, {"V2": 0.5, "V1": 0.7})
        self.assertEqual(result.child_orders[0].venue_id, "V2")  # best bid first

    async def test_limit_price_stops_the_walk(self) -> None:
        depth = {"V1": {"asks": [(100.0, 1.0), (101.0, 1.0), (105.0, 9.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.0, 100.0))
        result = await self.strategy.route(order(5.0, limit=101.0), nbbo, ["V1"], depth=depth)
        self.assertEqual(result.status, RoutingStatus.PARTIAL)
        self.assertAlmostEqual(result.child_orders[0].quantity, 2.0)
        self.assertAlmostEqual(result.child_orders[0].price, 100.5)  # expected VWAP
        self.assertEqual(result.child_orders[0].limit_price, 101.0)  # IOC at the user's limit
        self.assertAlmostEqual(result.remaining_quantity, 3.0)

    async def test_market_child_limit_is_worst_planned_level(self) -> None:
        depth = {"V1": {"asks": [(100.0, 1.0), (101.0, 1.0), (102.0, 9.0)], "bids": []}}
        result = await self.strategy.route(order(2.5), nbbo_of(quote("V1", 99.0, 100.0)), ["V1"], depth=depth)
        child = result.child_orders[0]
        self.assertEqual(child.limit_price, 102.0)  # deepest level consumed
        self.assertAlmostEqual(child.price, (100 + 101 + 0.5 * 102) / 2.5)

    async def test_data_only_venue_with_better_price_is_explained(self) -> None:
        depth = {"V1": {"asks": [(100.0, 5.0)], "bids": []}, "V2": {"asks": [(99.5, 5.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.0, 100.0), quote("V2", 99.0, 99.5))
        result = await self.strategy.route(
            order(1.0), nbbo, ["V1"], depth=depth,
            venue_names={"V1": "Binance", "V2": "Coinbase"},
            exclusion_reasons={"V2": "Coinbase has no real testnet"},
        )
        self.assertEqual([c.venue_id for c in result.child_orders], ["V1"])
        v2 = next(c for c in result.decision.candidates if c.venue_id == "V2")
        self.assertEqual(v2.excluded_reason, "Coinbase has no real testnet")
        self.assertTrue(any("Coinbase (V2) quoted a better ask" in n for n in result.decision.notes))

    async def test_limit_below_market_is_rejected(self) -> None:
        depth = {"V1": {"asks": [(100.0, 1.0)], "bids": []}}
        result = await self.strategy.route(order(1.0, limit=90.0), nbbo_of(quote("V1", 99.0, 100.0)), ["V1"], depth=depth)
        self.assertEqual(result.status, RoutingStatus.REJECTED)

    async def test_lot_step_floors_each_allocation(self) -> None:
        depth = {"V1": {"asks": [(100.0, 0.123456), (101.0, 10.0)], "bids": []}}
        result = await self.strategy.route(order(0.2), nbbo_of(quote("V1", 99.0, 100.0)), ["V1"],
                                           depth=depth, lot_steps={"V1": 0.001})
        self.assertAlmostEqual(result.child_orders[0].quantity, 0.2)
        self.assertEqual(result.decision.levels_used, 2)  # 0.123 + 0.077

    async def test_unroutable_venue_is_excluded_but_listed(self) -> None:
        depth = {"V1": {"asks": [(100.0, 5.0)], "bids": []}, "V3": {"asks": [(90.0, 5.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.0, 100.0), quote("V3", 89.0, 90.0))
        result = await self.strategy.route(order(1.0), nbbo, ["V1"], depth=depth)
        self.assertEqual([c.venue_id for c in result.child_orders], ["V1"])
        v3 = next(c for c in result.decision.candidates if c.venue_id == "V3")
        self.assertFalse(v3.eligible)
        self.assertIn("not routable", v3.excluded_reason)

    async def test_sell_is_capped_by_each_venues_holdings(self) -> None:
        # The live bug: best bid on V1, but V1 only holds part of the coins.
        depth = {"V1": {"bids": [(753.8, 10.0)], "asks": []}, "V4": {"bids": [(753.5, 10.0)], "asks": []}}
        nbbo = nbbo_of(quote("V1", 753.8, 754.0, bid_size=10), quote("V4", 753.5, 754.1, bid_size=10))
        result = await self.strategy.route(order(4.0, OrderSide.SELL), nbbo, ["V1", "V4"], depth=depth,
                                           capacity={"V1": 2.7, "V4": 1.35})
        alloc = {c.venue_id: c.quantity for c in result.child_orders}
        self.assertEqual(alloc, {"V1": 2.7, "V4": 1.3})  # V1 first (best bid) up to its holdings
        self.assertEqual(result.status, RoutingStatus.SUCCESS)
        self.assertTrue(any("capped" in n and "balance" in n for n in result.decision.notes))

    async def test_venue_with_no_balance_is_excluded_with_reason(self) -> None:
        depth = {"V1": {"asks": [(100.0, 5.0)], "bids": []}, "V4": {"asks": [(99.0, 5.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99.5, 100.0), quote("V4", 98.5, 99.0))
        result = await self.strategy.route(order(1.0), nbbo, ["V1", "V4"], depth=depth, capacity={"V4": 0.0})
        self.assertEqual([c.venue_id for c in result.child_orders], ["V1"])
        v4 = next(c for c in result.decision.candidates if c.venue_id == "V4")
        self.assertIn("no USDT balance", v4.excluded_reason)

    async def test_sliver_below_a_venues_minimum_is_rerouted(self) -> None:
        # Best ask on V1 has only 0.00003; the split would hand V1 a $3 sliver
        # it rejects (min $5), leaving a TWAP slice partly unfilled.
        depth = {"V1": {"asks": [(100_000.0, 0.00003), (100_050.0, 1.0)], "bids": []},
                 "V4": {"asks": [(100_010.0, 1.0)], "bids": []}}
        nbbo = nbbo_of(quote("V1", 99_990.0, 100_000.0, ask_size=0.00003), quote("V4", 99_980.0, 100_010.0))
        result = await self.strategy.route(
            order(0.0002), nbbo, ["V1", "V4"], depth=depth, lot_steps={"V1": 0.00001, "V4": 0.00001},
            min_sizes={"V1": (0.00001, 5.0), "V4": (0.00001, 1.0)},
        )
        self.assertEqual({c.venue_id: c.quantity for c in result.child_orders}, {"V4": 0.0002})
        v1 = next(c for c in result.decision.candidates if c.venue_id == "V1")
        self.assertIn("too small", v1.excluded_reason)
        self.assertTrue(any("re-routed" in n for n in result.decision.notes))

    async def test_order_below_every_minimum_is_rejected_with_reason(self) -> None:
        depth = {"V1": {"asks": [(100.0, 5.0)], "bids": []}}
        result = await self.strategy.route(order(0.02), nbbo_of(quote("V1", 99.0, 100.0)), ["V1"],
                                           depth=depth, min_sizes={"V1": (0.001, 5.0)})
        self.assertEqual(result.status, RoutingStatus.REJECTED)
        self.assertIn("below every venue's minimum", result.rejection_reason)

    async def test_no_depth_falls_back_to_top_of_book(self) -> None:
        result = await self.strategy.route(order(3.0), nbbo_of(quote("V1", 99.0, 100.0, ask_size=1.0)), ["V1"])
        self.assertEqual(result.status, RoutingStatus.PARTIAL)
        self.assertAlmostEqual(result.child_orders[0].quantity, 1.0)


if __name__ == "__main__":
    unittest.main()
