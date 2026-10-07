"""Pure maths of every execution algorithm: VWAP profile, Almgren-Chriss, POV, iceberg, auto."""

import math
import unittest
from datetime import UTC, datetime

from backend.services.algos.schedules import (
    AutoInputs,
    ac_cost_risk,
    almgren_chriss_holdings,
    bucket_volumes,
    choose_algo,
    enforce_min_slice,
    iceberg_piece,
    impact_coefficients,
    market_vwap,
    minute_sigma,
    pov_quantity,
    targets_from_holdings,
    volume_between,
    volume_profile,
    weighted_schedule,
)
from backend.shared.models.market_data import MinuteBar
from backend.shared.models.strategy import Urgency

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
M0 = int(T0.timestamp() * 1000)


def bar(minute: int, volume: float, price: float = 100.0) -> MinuteBar:
    return MinuteBar(ts_ms=M0 + minute * 60_000, volume=volume, pv=price * volume)


class WeightedScheduleTest(unittest.TestCase):
    def test_targets_follow_the_weights_and_end_exactly_at_the_quantity(self) -> None:
        s = weighted_schedule([1, 2, 3, 4], 0.01, T0, 40, 0.001)
        self.assertEqual([x.target_cumulative for x in s], [0.001, 0.003, 0.006, 0.01])
        self.assertEqual([(x.scheduled_at - T0).total_seconds() for x in s], [0, 10, 20, 30])

    def test_equal_weights_are_a_twap(self) -> None:
        s = weighted_schedule([1.0] * 4, 1.0, T0, 40, 0.001)
        self.assertEqual([x.target_cumulative for x in s], [0.25, 0.5, 0.75, 1.0])


class MinSliceTest(unittest.TestCase):
    def test_a_slice_below_the_minimum_is_deferred_into_the_next(self) -> None:
        # sizes 0.5, 0.1, 0.3, 0.3 with a 0.2 minimum: the 0.1 waits and joins the next slice
        self.assertEqual(enforce_min_slice([0.5, 0.6, 0.9, 1.2], 1.2, 0.2, 0.001), [0.5, 0.5, 0.9, 1.2])

    def test_a_tiny_end_joins_the_last_slice_that_trades(self) -> None:
        # front-loaded: 0.7, 0.2, 0.06, 0.04 with a 0.15 minimum -> 0.7, 0.3, 0, 0
        self.assertEqual(enforce_min_slice([0.7, 0.9, 0.96, 1.0], 1.0, 0.15, 0.001), [0.7, 1.0, 1.0, 1.0])

    def test_nothing_changes_when_every_slice_is_big_enough(self) -> None:
        self.assertEqual(enforce_min_slice([0.25, 0.5, 0.75, 1.0], 1.0, 0.1, 0.001), [0.25, 0.5, 0.75, 1.0])
        self.assertEqual(enforce_min_slice([0.25, 0.5], 0.5, 0.0, 0.001), [0.25, 0.5])


class VolumeTest(unittest.TestCase):
    def test_a_minute_is_spread_over_the_buckets_it_covers(self) -> None:
        # 2 minutes, 4 buckets of 30 s: minute 0 (60) -> 30 + 30, minute 1 (120) -> 60 + 60
        self.assertEqual(bucket_volumes([bar(0, 60), bar(1, 120)], M0, 120, 4), [30, 30, 60, 60])

    def test_a_minute_straddling_the_window_start_counts_pro_rata(self) -> None:
        start = M0 + 30_000  # the window starts half way through minute 0
        # bucket 0 = 0:30-1:00 gets half of minute 0; bucket 1 = 1:00-1:30 half of minute 1
        self.assertEqual(bucket_volumes([bar(0, 60), bar(1, 60)], start, 60, 2), [30, 30])

    def test_profile_averages_each_days_shape(self) -> None:
        # Day 1 is ten times busier but has the same shape as day 2 except reversed.
        day1, day2 = [bar(0, 300), bar(1, 700)], [bar(0, 70), bar(1, 30)]
        profile = volume_profile([day1, day2], [M0, M0], 120, 2)
        self.assertAlmostEqual(profile[0], (0.3 + 0.7) / 2)
        self.assertAlmostEqual(sum(profile), 1.0)
        self.assertIsNone(volume_profile([[], []], [M0, M0], 120, 2))

    def test_volume_and_vwap_between_two_instants(self) -> None:
        bars = [bar(0, 60, price=100), bar(1, 60, price=110)]
        # From 0:30 to 1:30: half of each minute
        self.assertAlmostEqual(volume_between(bars, M0 + 30_000, M0 + 90_000), 60)
        self.assertAlmostEqual(market_vwap(bars, M0 + 30_000, M0 + 90_000), 105)
        self.assertIsNone(market_vwap([], M0, M0 + 60_000))

    def test_the_current_minute_counts_what_has_happened_so_far(self) -> None:
        # Minute 1 is in progress (20 s in) and has traded 10 so far: all of it counts.
        bars = [bar(0, 60), bar(1, 10)]
        self.assertAlmostEqual(volume_between(bars, M0, M0 + 80_000, now_ms=M0 + 80_000), 70)
        # ...whereas for a finished window the same minute is a whole minute: 20 s of it = 1/3
        self.assertAlmostEqual(volume_between(bars, M0, M0 + 80_000), 60 + 10 / 3)

    def test_minute_sigma_needs_enough_data(self) -> None:
        self.assertIsNone(minute_sigma([bar(i, 1, 100 + i % 2) for i in range(5)]))
        sigma = minute_sigma([bar(i, 1, 100 + (i % 2)) for i in range(30)])
        self.assertGreater(sigma, 0.005)  # alternating 100/101: ~1 % moves


class AlmgrenChrissTest(unittest.TestCase):
    def test_zero_urgency_is_a_straight_line(self) -> None:
        self.assertEqual(almgren_chriss_holdings(1.0, 4, 0.0), [1.0, 0.75, 0.5, 0.25, 0.0])

    def test_urgency_front_loads_and_ends_at_zero(self) -> None:
        for k in (0.5, 2.0, 5.0):
            x = almgren_chriss_holdings(1.0, 10, k)
            self.assertAlmostEqual(x[0], 1.0)
            self.assertAlmostEqual(x[-1], 0.0)
            self.assertTrue(all(a > b for a, b in zip(x, x[1:], strict=False)))
        trades = [a - b for a, b in zip(almgren_chriss_holdings(1.0, 10, 5.0), almgren_chriss_holdings(1.0, 10, 5.0)[1:], strict=False)]
        self.assertGreater(trades[0], 0.3)  # HIGH urgency: about 40 % in the first tenth
        self.assertEqual(targets_from_holdings([1.0, 0.6, 0.0]), [0.4, 1.0])

    def test_impact_coefficients_from_the_book(self) -> None:
        levels = [(100.0, 1.0), (101.0, 1.0)]
        eps, eta = impact_coefficients(levels, 1.5, mid=99.5, tau_s=10)
        self.assertAlmostEqual(eps, 0.5)  # crossing half the spread
        walk = (100 * 1 + 101 * 0.5) / 1.5 - 100
        self.assertAlmostEqual(eta, walk * 10 / 1.5)

    def test_front_loading_trades_cost_for_risk(self) -> None:
        twap = almgren_chriss_holdings(10.0, 10, 0.0)
        fast = almgren_chriss_holdings(10.0, 10, 5.0)
        c_twap, r_twap = ac_cost_risk(twap, eps=0.1, eta=1.0, sigma_abs_per_s=0.5, tau_s=10)
        c_fast, r_fast = ac_cost_risk(fast, eps=0.1, eta=1.0, sigma_abs_per_s=0.5, tau_s=10)
        self.assertGreater(c_fast, c_twap)
        self.assertLess(r_fast, r_twap)
        self.assertAlmostEqual(c_twap, 0.1 * 10 + (1.0 / 10) * 10 * 1.0)  # eps*sum|n| + eta/tau*sum n^2
        self.assertAlmostEqual(r_twap, 0.5 * math.sqrt(10 * sum(x * x for x in twap[1:])))


class PovAndIcebergTest(unittest.TestCase):
    def test_pov_catches_up_to_its_share(self) -> None:
        # 10 % of 0.05 = 0.005, filled 0.002 -> 0.003 (cap: 2 x 10 % of the last 0.02 = 0.004)
        self.assertAlmostEqual(pov_quantity(10, 0.05, 0.002, 0.02, 1.0, 0.0001, 0.001), 0.003)

    def test_pov_waits_below_a_tradable_size(self) -> None:
        self.assertEqual(pov_quantity(10, 0.005, 0.0, 0.005, 1.0, 0.0001, 0.001), 0.0)

    def test_pov_catch_up_is_capped_but_can_always_reach_one_minimum(self) -> None:
        # Far behind after a stall: 10 % of 1.0 = 0.1 wanted, last interval only 0.001 traded.
        self.assertAlmostEqual(pov_quantity(10, 1.0, 0.0, 0.001, 1.0, 0.0001, 0.001), 0.001)
        self.assertAlmostEqual(pov_quantity(10, 1.0, 0.0, 1.0, 0.03, 0.0001, 0.001), 0.03)  # remaining caps
        self.assertAlmostEqual(pov_quantity(10, 1.0, 0.0, 1.0, 1.0, 0.0001, 0.001, max_child=0.02), 0.02)

    def test_iceberg_piece_varies_but_never_strands_a_remainder(self) -> None:
        self.assertAlmostEqual(iceberg_piece(0.01, 20, 1.0, 1.0, 0.001, 0.001), 0.012)
        self.assertAlmostEqual(iceberg_piece(0.01, 20, -1.0, 1.0, 0.001, 0.001), 0.008)
        # 0.0105 left: a 0.01 piece would leave 0.0005, below the minimum -> take it all
        self.assertAlmostEqual(iceberg_piece(0.01, 0, 0.0, 0.0105, 0.0001, 0.001), 0.0105)
        self.assertAlmostEqual(iceberg_piece(0.0005, 0, 0.0, 1.0, 0.0001, 0.001), 0.001)  # never below the minimum


def auto(**kw) -> AutoInputs:
    base = dict(quantity=0.01, mid=100_000.0, spread_bps=1.0, impact_now_bps=0.6, volume_per_min=10.0,
                urgency=Urgency.MEDIUM, max_duration_s=3600, min_tradable=0.0001, max_order_qty=0.1,
                has_volume_history=True)
    base.update(kw)
    return AutoInputs(**base)


class AutoTest(unittest.TestCase):
    def test_small_enough_goes_as_one_order(self) -> None:
        c = choose_algo(auto())
        self.assertEqual(c.kind, "SINGLE")
        self.assertIn("splitting it over time would only add price risk", c.reasons[0])

    def test_urgency_picks_the_algorithm(self) -> None:
        big = dict(quantity=5.0, impact_now_bps=12.0, max_order_qty=1.0)
        self.assertEqual(choose_algo(auto(urgency=Urgency.HIGH, **big)).kind, "IS")
        self.assertEqual(choose_algo(auto(urgency=Urgency.MEDIUM, **big)).kind, "VWAP")
        self.assertEqual(choose_algo(auto(urgency=Urgency.MEDIUM, has_volume_history=False, **big)).kind, "TWAP")
        pov = choose_algo(auto(urgency=Urgency.LOW, **big))
        self.assertEqual((pov.kind, pov.participation_pct, pov.slices), ("POV", 5.0, None))

    def test_duration_keeps_the_participation_near_the_target(self) -> None:
        # 5 BTC at 10 % of 10 BTC/min = 5 min
        c = choose_algo(auto(quantity=5.0, impact_now_bps=12.0, max_order_qty=1.0))
        self.assertAlmostEqual(c.duration_s, 300)
        self.assertEqual(c.slices, 20)  # one every 15 s
        self.assertTrue(any("10.0 % of the volume" in r for r in c.reasons))

    def test_too_big_for_the_market_is_warned(self) -> None:
        c = choose_algo(auto(quantity=500.0, impact_now_bps=None, max_order_qty=1.0, max_duration_s=600))
        self.assertTrue(any("of the market's volume" in w for w in c.warnings))

    def test_impossible_requests_say_so(self) -> None:
        # Above the per-order limit and too small to split into accepted orders.
        c = choose_algo(auto(quantity=0.00015, impact_now_bps=20.0, max_order_qty=0.0001))
        self.assertEqual(c.kind, "NONE")


if __name__ == "__main__":
    unittest.main()
