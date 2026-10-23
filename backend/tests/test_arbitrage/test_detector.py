"""Arbitrage detector (5c): edge math, fees, funding, windows, the service — synthetic books only."""

import ast
import inspect
import json
import unittest
from datetime import UTC, datetime, timedelta

from backend.gateway.config import Settings
from backend.services.arbitrage import detector as detector_module
from backend.services.arbitrage.detector import (
    DETECTOR_ACTOR,
    STATS_KEY,
    ArbitrageDetector,
    WindowTracker,
    cross_venue_edges,
    describe_overall,
    describe_symbol,
    edge_between,
    fee_for,
    fundable_size,
    parse_fee_schedule,
)
from backend.shared.database.store import AuditStore
from backend.shared.events.event_bus import event_bus
from backend.shared.models.arbitrage import ArbEdge, ArbSymbolStats
from backend.shared.models.market_data import VenueQuote

BTC = "BTC/USDT"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FEES = {"V1": 10.0, "V4": 10.0, "V6": 20.0}
NAMES = {"V1": "Binance", "V4": "Bybit", "V6": "Gate"}


def q(venue: str, bid: float, ask: float, bid_size: float = 1.0, ask_size: float = 1.0,
      symbol: str = BTC) -> VenueQuote:
    return VenueQuote(venue_id=venue, symbol=symbol, bid_price=bid, ask_price=ask, bid_size=bid_size,
                      ask_size=ask_size, last_price=(bid + ask) / 2, volume=0.0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def edge(buy: str = "V1", sell: str = "V4", gross: float = 1.0, net: float = -19.0, size: float = 1.0,
         buy_price: float = 100.0) -> ArbEdge:
    return ArbEdge(symbol=BTC, buy_venue=buy, sell_venue=sell, buy_price=buy_price,
                   sell_price=buy_price * (1 + gross / 10_000), gross_bps=gross, fee_bps=gross - net,
                   net_bps=net, size=size, net_usdt=net / 10_000 * buy_price * size)


class EdgeMathTest(unittest.TestCase):
    def test_gross_and_net_after_both_taker_fees(self) -> None:
        e = edge_between(BTC, q("V4", 101, 102, bid_size=2), q("V1", 99, 100, ask_size=3), 10, 10)
        self.assertEqual((e.buy_venue, e.sell_venue, e.buy_price, e.sell_price), ("V1", "V4", 100, 101))
        self.assertAlmostEqual(e.gross_bps, 100.0)
        # sell 101 x (1 - 0.001) = 100.899; buy 100 x (1 + 0.001) = 100.1; left 0.799 per BTC
        self.assertAlmostEqual(e.net_bps, 79.9)
        self.assertAlmostEqual(e.fee_bps, 20.1)
        self.assertEqual(e.size, 2)  # the thinner of the two top levels
        self.assertAlmostEqual(e.net_usdt, 1.598)

    def test_a_typical_live_crossing_is_a_loss_after_fees(self) -> None:
        # Measured live: books crossed by ~1 bps, a taker round trip ~20 bps.
        e = edge_between(BTC, q("V4", 100_010, 100_020), q("V1", 99_990, 100_000), 10, 10)
        self.assertAlmostEqual(e.gross_bps, 1.0, places=3)
        self.assertAlmostEqual(e.net_bps, -19.0, delta=0.01)
        self.assertLess(e.net_usdt, 0)

    def test_every_direction_between_every_pair_best_net_first(self) -> None:
        quotes = {"V1": q("V1", 99.9, 100.0), "V4": q("V4", 100.3, 100.4), "V6": q("V6", 100.1, 100.2)}
        edges = cross_venue_edges(BTC, quotes, FEES)
        self.assertEqual(len(edges), 6)
        self.assertEqual(len({(e.buy_venue, e.sell_venue) for e in edges}), 6)
        self.assertEqual([e.net_bps for e in edges], sorted((e.net_bps for e in edges), reverse=True))
        self.assertEqual((edges[0].buy_venue, edges[0].sell_venue), ("V1", "V4"))  # cheapest ask, highest bid

    def test_needs_two_venues_with_real_prices(self) -> None:
        self.assertEqual(cross_venue_edges(BTC, {"V1": q("V1", 99, 100)}, FEES), [])
        self.assertEqual(cross_venue_edges(BTC, {"V1": q("V1", 99, 100), "V4": q("V4", 101, 0)}, FEES), [])

    def test_a_venue_without_a_configured_fee_pays_the_highest(self) -> None:
        self.assertEqual(fee_for(FEES, "V5"), 20.0)
        self.assertEqual(fee_for(FEES, "V1"), 10.0)


class FeeScheduleTest(unittest.TestCase):
    def test_exchange_names_or_venue_ids(self) -> None:
        self.assertEqual(parse_fee_schedule("binance:10, Bybit:7.5,v6:20"), {"V1": 10.0, "V4": 7.5, "V6": 20.0})

    def test_the_default_setting_covers_every_venue(self) -> None:
        fees = parse_fee_schedule(Settings.model_fields["arb_taker_fees_bps"].default)
        self.assertEqual(set(fees), {"V1", "V2", "V4", "V5", "V6", "V7"})

    def test_bad_entries_refuse_to_start(self) -> None:
        for text in ("binance", "binance:ten", "nasdaq:10", "binance:-1", "", " , "):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_fee_schedule(text)


class FundableTest(unittest.TestCase):
    def fund(self, e: ArbEdge, base: float | None = 10.0, quote: float | None = 1e6, blocked=None):
        return fundable_size(e, base_free_on_sell=base, quote_free_on_buy=quote, buy_fee_bps=10,
                             blocked=blocked or {}, names=NAMES)

    def test_enough_on_both_venues(self) -> None:
        self.assertEqual(self.fund(edge(size=0.5)), (0.5, []))

    def test_usdt_on_the_buy_venue_caps_it(self) -> None:
        size, why = self.fund(edge(size=0.5), quote=10.01)  # 10.01 / (100 x 1.001) = 0.1 BTC
        self.assertAlmostEqual(size, 0.1)
        self.assertEqual(why, ["Binance has 10.01 USDT to buy with"])

    def test_coins_on_the_sell_venue_cap_it(self) -> None:
        size, why = self.fund(edge(size=0.5), base=0.0002)
        self.assertEqual(size, 0.0002)
        self.assertEqual(why, ["Bybit holds 0.0002 BTC to sell"])

    def test_an_unknown_balance_is_not_a_guess(self) -> None:
        size, why = self.fund(edge(), quote=None)
        self.assertIsNone(size)
        self.assertIn("Binance USDT balance unknown", why)

    def test_a_venue_that_cannot_execute_means_zero(self) -> None:
        size, why = self.fund(edge(buy="V6"), blocked={"V6": "no USDT balance"})
        self.assertEqual(size, 0.0)
        self.assertEqual(why, ["Gate: no USDT balance"])


class WindowTrackerTest(unittest.TestCase):
    def test_a_crossing_over_consecutive_scans_is_one_window(self) -> None:
        t = WindowTracker()
        self.assertEqual(t.observe(BTC, [edge(gross=1.0)], at(0)), [])
        self.assertEqual(t.observe(BTC, [edge(gross=3.0, net=-17.0)], at(0.25)), [])
        self.assertEqual(len(t.open_windows(BTC)), 1)
        closed = t.observe(BTC, [edge(gross=-0.5, net=-20.5)], at(0.5))
        self.assertEqual(len(closed), 1)
        w = closed[0]
        self.assertEqual((w.scans, w.duration_s, w.peak_gross_bps, w.peak.net_bps), (2, 0.5, 3.0, -17.0))
        self.assertFalse(w.profitable)
        st = t.stats[BTC]
        self.assertEqual((st.scans, st.crossed_scans, st.profitable_scans, st.windows), (3, 2, 0, 1))
        self.assertEqual(st.duration_buckets, {"<1 s": 1})
        self.assertEqual(st.peak_gross_buckets, {"2-5 bps": 1})
        self.assertEqual(st.max_gross.gross_bps, 3.0)
        self.assertEqual(st.max_gross_at, at(0.25))
        self.assertEqual(t.open_windows(BTC), [])

    def test_a_window_profitable_after_fees_is_flagged_and_kept(self) -> None:
        t = WindowTracker(min_net_bps=0.0)
        t.observe(BTC, [edge(gross=25.0, net=4.0)], at(0))
        t.observe(BTC, [edge(gross=21.0, net=0.5)], at(1))
        (w,) = t.observe(BTC, [], at(7))  # venue gone: the window closes
        self.assertTrue(w.profitable)
        self.assertEqual(w.peak.net_bps, 4.0)
        self.assertEqual(list(t.recent_profitable), [w])
        st = t.stats[BTC]
        self.assertEqual((st.profitable_scans, st.profitable_windows), (2, 1))
        self.assertEqual(st.duration_buckets, {"5-30 s": 1})
        self.assertEqual(st.peak_gross_buckets, {"20+ bps": 1})

    def test_each_direction_and_symbol_is_its_own_window(self) -> None:
        t = WindowTracker()
        t.observe(BTC, [edge("V1", "V4"), edge("V6", "V4")], at(0))
        t.observe("ETH/USDT", [edge("V1", "V4").model_copy(update={"symbol": "ETH/USDT"})], at(0))
        closed = t.observe(BTC, [edge("V1", "V4")], at(1))  # V6->V4 uncrossed, V1->V4 still open
        self.assertEqual([(w.buy_venue, w.sell_venue) for w in closed], [("V6", "V4")])
        self.assertEqual(len(t.open_windows()), 2)
        self.assertEqual(len(t.close_all(at(2))), 2)
        self.assertEqual(t.stats[BTC].windows + t.stats["ETH/USDT"].windows, 3)

    def test_statistics_survive_a_restart(self) -> None:
        t = WindowTracker(since=T0)
        t.observe(BTC, [edge(gross=2.0)], at(0))
        t.observe(BTC, [], at(1))
        saved = json.loads(json.dumps(t.snapshot()))  # as it goes through the kv store
        again = WindowTracker()
        again.restore(saved)
        self.assertEqual(again.since, T0)
        self.assertEqual(again.stats[BTC], t.stats[BTC])

    def test_plain_english_summaries(self) -> None:
        t = WindowTracker(since=T0)
        self.assertIn("waiting for live quotes", describe_symbol(ArbSymbolStats(symbol=BTC)))
        t.observe(BTC, [edge(gross=1.0, net=-19.0)], at(0))
        t.observe(BTC, [edge(gross=-1.0, net=-21.0)], at(0.25))
        line = describe_symbol(t.stats[BTC])
        self.assertIn("crossed in 50% of scans", line)
        self.assertIn("+0.0 bps apart before fees; the best pair after taker fees was -20.0 bps", line)
        self.assertIn("0 of 1 crossed windows were profitable after fees; 100% lasted under 5 s", line)
        overall = describe_overall(list(t.stats.values()), T0)
        self.assertTrue(overall.startswith("Since 04 Oct 12:00 UTC: the books were crossed in 50% of scans"))
        self.assertNotIn("Best seen", overall)  # nothing profitable: no best-seen line
        self.assertEqual(describe_overall([], T0), "Waiting for live quotes from at least two venues.")


class FakeCollector:
    def __init__(self) -> None:
        self.quotes: dict[str, dict[str, VenueQuote]] = {}

    def get_all_quotes_for_symbol(self, symbol: str) -> dict[str, VenueQuote]:
        return dict(self.quotes.get(symbol, {}))


class FakeVenueMonitor:
    def __init__(self, healthy: list[str]) -> None:
        self.healthy = healthy

    def get_routable_venues(self) -> list[str]:
        return list(self.healthy)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


class DetectorServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = AuditStore(":memory:")
        await self.store.open()
        self.collector = FakeCollector()
        self.monitor = FakeVenueMonitor(["V1", "V4", "V6"])
        self.clock = Clock()
        self.balances = {("V1", "USDT"): 1_000.0, ("V4", "BTC"): 0.0002}
        self.excluded: dict[str, str] = {}
        self.audits: list[dict] = []

        async def on_audit(data: dict) -> None:
            self.audits.append(data)

        self.on_audit = on_audit
        event_bus.subscribe("audit", on_audit)

    async def asyncTearDown(self) -> None:
        event_bus.unsubscribe("audit", self.on_audit)
        await self.store.close()

    def make(self, **kw) -> ArbitrageDetector:
        return ArbitrageDetector(
            self.collector, self.monitor, self.store, [BTC], FEES, venue_names=NAMES,
            excluded_venues=lambda symbol: dict(self.excluded),
            balance=lambda venue, asset: self.balances.get((venue, asset)), clock=self.clock, **kw,
        )

    def market(self, **quotes: VenueQuote) -> None:
        self.collector.quotes[BTC] = quotes

    async def scan(self, det: ArbitrageDetector, seconds: float) -> None:
        self.clock.t += 0.25
        await det._report(det.scan_once(at(seconds)))

    async def test_a_profitable_window_is_audited_once_per_cooldown(self) -> None:
        det = self.make(audit_cooldown_s=60)
        crossed = {"V1": q("V1", 99.0, 100.0), "V4": q("V4", 100.5, 101.0)}  # +50 bps gross, ~+30 net
        flat = {"V1": q("V1", 99.0, 100.0), "V4": q("V4", 99.0, 100.0)}
        for i, book in enumerate([crossed, flat, crossed, flat]):  # two profitable windows 0.5 s apart
            self.market(**book)
            await self.scan(det, i * 0.25)
        await self.store.flush()
        opportunities = [a for a in self.audits if a["event_type"] == "arb_opportunity"]
        self.assertEqual(len(opportunities), 1)
        a = opportunities[0]
        self.assertEqual((a["actor"], a["symbol"], a["venue_id"]), (DETECTOR_ACTOR, BTC, "V1"))
        self.assertIn("buy on Binance at 100, sell on Bybit at 100.5", a["summary"])
        self.assertIn("Detector only - nothing was traded", a["summary"])
        self.assertEqual(det.tracker.stats[BTC].profitable_windows, 2)  # both counted

        self.clock.t += 61  # cooldown over: the next profitable window is audited again
        self.market(**crossed)
        await self.scan(det, 70)
        self.market(**flat)
        await self.scan(det, 70.25)
        self.assertEqual(len([a for a in self.audits if a["event_type"] == "arb_opportunity"]), 2)

    async def test_crossed_but_unprofitable_windows_are_counted_not_audited(self) -> None:
        det = self.make()
        self.market(V1=q("V1", 99.99, 100.0), V4=q("V4", 100.01, 100.02))  # +1 bps gross
        await self.scan(det, 0)
        self.market(V1=q("V1", 99.99, 100.0), V4=q("V4", 99.99, 100.0))
        await self.scan(det, 0.25)
        self.assertEqual(det.tracker.stats[BTC].windows, 1)
        self.assertEqual(det.tracker.stats[BTC].profitable_windows, 0)
        self.assertEqual(self.audits, [])

    async def test_an_unhealthy_venue_is_ignored(self) -> None:
        self.monitor.healthy = ["V1", "V4"]
        det = self.make()
        self.market(V1=q("V1", 99, 100), V4=q("V4", 99, 100), V6=q("V6", 150, 151))  # V6 looks crossed
        det.scan_once(at(0))
        self.assertEqual(det.tracker.stats[BTC].crossed_scans, 0)
        self.assertEqual(det.status().symbols[0].venues, ["V1", "V4"])

    async def test_status_shows_the_best_edge_with_what_we_could_fund(self) -> None:
        det = self.make()
        self.market(V1=q("V1", 99.99, 100.0, ask_size=2), V4=q("V4", 100.01, 100.02, bid_size=1))
        det.scan_once(at(0))
        status = det.status()
        self.assertEqual(status.fees_bps, FEES)
        self.assertFalse(status.running)
        sym = status.symbols[0]
        self.assertEqual((sym.best.buy_venue, sym.best.sell_venue, sym.best.size), ("V1", "V4", 1))
        self.assertEqual(sym.best.fundable_size, 0.0002)  # Bybit only holds 0.0002 BTC to sell
        self.assertEqual(sym.best.limited_by, ["Bybit holds 0.0002 BTC to sell"])
        self.assertEqual(sym.crossed_directions, 1)
        self.assertEqual(len(sym.open_windows), 1)
        self.assertIn("crossed in 100% of scans", sym.summary)
        self.assertIn("Detector only", status.note)

        self.excluded = {"V4": "trading paused for 10 more min after: no permission"}
        self.assertEqual(det.status().symbols[0].best.fundable_size, 0.0)

    async def test_statistics_are_saved_and_loaded(self) -> None:
        det = self.make()
        self.market(V1=q("V1", 99.99, 100.0), V4=q("V4", 100.01, 100.02))
        det.scan_once(at(0))
        det._persist()
        await self.store.flush()
        self.assertIsNotNone(await self.store.kv_get(STATS_KEY))
        again = self.make()
        await again.load()
        self.assertEqual(again.tracker.stats[BTC].scans, 1)
        self.assertEqual(again.tracker.since, det.tracker.since)

    async def test_windows_newest_first_with_filters(self) -> None:
        det = self.make()
        books = [
            {"V1": q("V1", 99.99, 100.0), "V4": q("V4", 100.01, 100.02)},  # +1 bps: unprofitable
            {"V1": q("V1", 99.0, 100.0), "V4": q("V4", 99.0, 100.0)},
            {"V1": q("V1", 99.0, 100.0), "V4": q("V4", 100.5, 101.0)},  # profitable
            {"V1": q("V1", 99.0, 100.0), "V4": q("V4", 99.0, 100.0)},
        ]
        for i, book in enumerate(books):
            self.market(**book)
            await self.scan(det, i)
        listed = det.windows()
        self.assertEqual([w.profitable for w in listed.windows], [True, False])
        self.assertEqual(len(det.windows(profitable_only=True).windows), 1)
        self.assertEqual(det.windows(symbol="ETH/USDT").windows, [])

    async def test_disabled_detector_does_not_run(self) -> None:
        det = self.make(enabled=False)
        await det.start()  # returns at once
        self.assertFalse(det.status().running)
        self.assertFalse(det.status().enabled)


class NoExecutionPathTest(unittest.TestCase):
    """ROADMAP 5c: no execution path may bypass risk. The detector has none at all."""

    def test_the_detector_never_calls_an_order_method(self) -> None:
        tree = ast.parse(inspect.getsource(detector_module))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(called & {"place_order", "route_order", "submit", "create_order", "cancel_order", "create"})

    def test_it_is_not_given_anything_that_can_trade(self) -> None:
        params = set(inspect.signature(ArbitrageDetector.__init__).parameters)
        self.assertFalse(params & {"order_entry", "registry", "routing_engine", "twap_engine", "adapter"})


if __name__ == "__main__":
    unittest.main()
