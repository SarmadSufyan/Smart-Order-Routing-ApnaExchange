"""The trade desk's pure parts: the setups, the opening range, the time machine and the history check."""

import unittest

import numpy as np

from backend.services.research import scanner as sc
from backend.services.research import timemachine as tm

HOUR = 3_600_000
FIVE_MIN = 300_000
DAY = 86_400_000


def bars(close, ts0=0, tf_ms=HOUR, volume=None, spread=0.5):
    """Candles around a close series: open = previous close, high/low a little either side."""
    c = np.asarray(close, dtype=float)
    o = np.r_[c[0], c[:-1]]
    v = np.full(len(c), 10.0) if volume is None else np.asarray(volume, dtype=float)
    return {"ts": ts0 + tf_ms * np.arange(len(c)), "open": o, "high": np.maximum(o, c) + spread,
            "low": np.minimum(o, c) - spread, "close": c, "volume": v}


def calm(n, level=100.0, wobble=0.3, seed=1):
    rng = np.random.default_rng(seed)
    return level + rng.uniform(-wobble, wobble, size=n)


class SetupTest(unittest.TestCase):
    def test_breakout_fires_on_a_close_above_the_previous_20_closes_only(self):
        c = np.linspace(110, 100, 150)  # drifting down: never a new 20-bar high
        c[-1] = 120.0  # then a close above every earlier one
        m = sc.raw_mask("breakout", bars(c), "1h")
        self.assertTrue(m[-1])
        self.assertFalse(m[:-1].any())

    def test_fresh_counts_each_run_once(self):
        m = np.array([0, 1, 1, 1, 0, 1, 0, 0, 1, 1], dtype=bool)
        self.assertEqual(list(np.where(sc.fresh(m))[0]), [1, 5, 8])

    def test_reclaim_ma20_needs_a_cross_from_below(self):
        c = calm(150, level=100)
        c[-12:-1] = 97.0  # sits below the average
        c[-1] = 100.8  # closes back above it
        self.assertTrue(sc.raw_mask("reclaim_ma20", bars(c), "1h")[-1])
        c2 = calm(150)
        self.assertFalse(sc.raw_mask("reclaim_ma20", bars(c2), "1h")[-1])

    def test_oversold_bounce_after_a_slide(self):
        c = np.r_[calm(120), np.linspace(100, 80, 20)]  # a long slide: RSI under 30
        c = np.r_[c, 80.9]  # then one higher close
        d = sc.details("oversold_bounce", bars(c), len(c) - 1, "1h")
        self.assertTrue(sc.raw_mask("oversold_bounce", bars(c), "1h")[-1])
        self.assertLess(d["rsi_previous"], 30)
        self.assertGreater(d["bounce_pct"], 0)

    def test_volume_surge_needs_volume_and_a_rising_bar(self):
        c = calm(100)
        v = np.full(100, 10.0)
        v[-1] = 80.0
        c[-1] = c[-2] + 0.8  # rising bar (close above open = previous close)
        b = bars(c, volume=v)
        self.assertTrue(sc.raw_mask("volume_surge", b, "1h")[-1])
        c[-1] = c[-2] - 0.8  # same volume on a falling bar: no
        self.assertFalse(sc.raw_mask("volume_surge", bars(c, volume=v), "1h")[-1])

    def test_pullback_in_an_uptrend(self):
        c = np.linspace(100, 130, 140)  # a steady uptrend
        c[-1] = c[-2] - 1.2  # a dip back toward the 20-average, below the 10-bar high
        b = bars(c, spread=1.0)
        self.assertTrue(sc.raw_mask("pullback", b, "1h")[-1])
        down = np.linspace(130, 100, 140)
        self.assertFalse(sc.raw_mask("pullback", bars(down), "1h")[-1])

    def test_bull_flag_is_a_run_then_a_tight_range(self):
        c = np.r_[calm(100, level=100), np.linspace(100, 115, 10), np.full(5, 114.5) + np.array([0, 0.3, -0.2, 0.1, 0.2])]
        b = bars(c)
        self.assertTrue(sc.raw_mask("bull_flag", b, "1h")[-1])
        d = sc.details("bull_flag", b, len(c) - 1, "1h")
        self.assertGreater(d["run_pct"], 10)
        self.assertLess(d["flag_range_pct"], 5)
        self.assertGreaterEqual(d["trigger"], c[-5:].max())  # the flag's high

    def test_squeeze_is_the_narrowest_tenth_of_100_bars(self):
        rng = np.random.default_rng(2)
        c = np.r_[100 + rng.normal(0, 2.0, 150), 100 + rng.normal(0, 0.05, 25)]  # wild, then very quiet
        b = bars(c, spread=0.01)
        m = sc.raw_mask("squeeze", b, "1h")
        self.assertTrue(m[-1])
        self.assertTrue(m[-5:].all())  # a watch condition that holds for a while, not one bar
        self.assertFalse(m[100:140].any())  # the wild stretch is never a squeeze
        d = sc.details("squeeze", b, len(c) - 1, "1h")
        self.assertGreater(d["trigger"], c[-1])

    def test_scan_symbol_reports_fresh_entries_and_live_watches(self):
        c = np.linspace(110, 100, 150)
        c[-1] = 120.0
        hits = sc.scan_symbol("BTC/USDT", {"1h": bars(c)})
        names = {h.setup for h in hits}
        self.assertIn("breakout", names)
        for h in hits:
            self.assertEqual(h.symbol, "BTC/USDT")
            self.assertEqual(h.timeframe, "1h")
            self.assertGreater(h.dollar_volume, 0)
        old = np.linspace(110, 100, 150)
        old[-5] = 120.0  # fired 4 bars ago: too old for an entry setup
        self.assertNotIn("breakout", {h.setup for h in sc.scan_symbol("X/USDT", {"1h": bars(old)})})

    def test_too_few_bars_scans_nothing(self):
        self.assertEqual(sc.scan_symbol("X/USDT", {"1h": bars(calm(30))}), [])


class OpeningRangeTest(unittest.TestCase):
    def session(self, closes_after, range_closes=(100, 100.5, 99.8, 100.2, 100.1, 100.4), open_ms=DAY * 10):
        c = np.r_[range_closes, closes_after]
        b = bars(c, ts0=open_ms, tf_ms=FIVE_MIN, spread=0.2)
        return b

    def test_fires_once_at_the_first_close_above_the_range_high(self):
        b = self.session([100.3, 100.9, 101.5, 101.8])  # range high = 100.5 + 0.2 spread = 100.7
        mask, ranges = sc.opening_ranges(b["ts"], b["high"], b["low"], b["close"], "5m")
        self.assertEqual(list(np.where(mask)[0]), [7])  # bar 6 (100.3) no, bar 7 (100.9) yes, once
        self.assertEqual(len(ranges), 1)
        self.assertAlmostEqual(ranges[0].high, 100.7)
        self.assertEqual(ranges[0].fired_at, 7)

    def test_an_incomplete_range_never_fires(self):
        b = self.session([105.0, 106.0], range_closes=(100, 100.5))  # only 2 of the 6 range bars
        mask, ranges = sc.opening_ranges(b["ts"], b["high"], b["low"], b["close"], "5m")
        self.assertFalse(mask.any())
        self.assertIsNone(ranges[0].high)

    def test_no_entries_after_the_window(self):
        quiet = [100.0] * 60  # 5 hours of nothing after the range, then a breakout past the 4-hour window
        b = self.session(quiet + [103.0])
        mask, _ = sc.opening_ranges(b["ts"], b["high"], b["low"], b["close"], "5m")
        self.assertFalse(mask.any())

    def test_session_opens_follow_the_configured_hours(self):
        ts = np.array([DAY * 3 + 7 * HOUR + 55 * 60_000, DAY * 3 + 8 * HOUR, DAY * 3 + 13 * HOUR + 30 * 60_000 + FIVE_MIN])
        opens = sc.session_open_of(ts)
        self.assertEqual(list(opens), [DAY * 3, DAY * 3 + 8 * HOUR, DAY * 3 + 13 * HOUR + 30 * 60_000])

    def test_entry_state(self):
        self.assertEqual(sc.entry_state("entry", 100, 101, 0, 1)[0], "triggered")
        self.assertEqual(sc.entry_state("entry", 100, 101, 3, 1)[0], "expired")
        self.assertEqual(sc.entry_state("watch", 100, 99, 0, 12)[0], "waiting")
        self.assertEqual(sc.entry_state("watch", 100, 100.5, 0, 12)[0], "triggered")
        self.assertEqual(sc.entry_state("entry", 100, 101, 0, 1, window_end_ms=50, now_ms=60)[0], "expired")


class TimeMachineTest(unittest.TestCase):
    def flat_pool(self, h=1.001, lo=0.999, c=1.0, tf="1h", n=24 * 30):
        ts = np.arange(n) * HOUR
        close = np.full(n, 100.0)
        high = close * h
        low = close * lo
        close_next = close * c
        return tm.build_pool(ts, high, low, close_next, tf)

    def test_pool_groups_by_time_of_day(self):
        pool = self.flat_pool()
        self.assertEqual(pool.slots, 24)
        self.assertEqual(pool.sessions, 30)
        self.assertEqual(tm.slot_of(DAY * 5 + 14 * HOUR, "1h"), 14)
        self.assertEqual(tm.slot_of(DAY * 5 + 14 * HOUR, "1d"), 0)

    def test_nothing_moves_nothing_decides(self):
        odds = tm.simulate(self.flat_pool(), start_slot=0, bars=24, entry=100, stop=99, target=102, replays=500)
        self.assertEqual(odds.p_neither, 1.0)
        self.assertEqual(odds.verdict, "coin_flip")
        self.assertEqual(len(odds.bands), 24)

    def test_a_pool_that_only_rises_hits_the_target(self):
        n = 24 * 30
        ts = np.arange(n) * HOUR
        close = 100 * 1.01 ** np.arange(n)  # +1% per bar
        pool = tm.build_pool(ts, close * 1.002, close * 0.999, close, "1h")
        odds = tm.simulate(pool, 0, 10, entry=100, stop=98, target=103, replays=300)
        self.assertEqual(odds.p_target, 1.0)
        self.assertEqual(odds.verdict, "favourable")
        self.assertGreater(odds.avg_r, 0)

    def test_a_bar_touching_both_counts_as_the_stop(self):
        n = 100
        ts = np.arange(n) * HOUR
        close = np.full(n, 100.0)
        pool = tm.build_pool(ts, close * 1.10, close * 0.90, close, "1h")  # every bar spans +-10%
        odds = tm.simulate(pool, 0, 3, entry=100, stop=99, target=101, replays=200)
        self.assertEqual(odds.p_stop, 1.0)
        self.assertEqual(odds.p_target, 0.0)

    def test_same_seed_same_answer(self):
        rng = np.random.default_rng(7)
        n = 24 * 40
        ts = np.arange(n) * HOUR
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        pool = tm.build_pool(ts, close * 1.004, close * 0.996, close, "1h")
        a = tm.simulate(pool, 3, 24, 100, 98, 103, replays=1000, seed=5)
        b = tm.simulate(pool, 3, 24, 100, 98, 103, replays=1000, seed=5)
        self.assertEqual((a.p_target, a.p_stop, a.avg_r), (b.p_target, b.p_stop, b.avg_r))
        self.assertAlmostEqual(a.p_target + a.p_stop + a.p_neither, 1.0, places=6)
        for band in a.bands:
            self.assertLessEqual(band["p5"], band["p50"])
            self.assertLessEqual(band["p50"], band["p95"])

    def test_break_even_share_and_verdict(self):
        self.assertAlmostEqual(tm.break_even_share(2.0), 1 / 3)
        self.assertAlmostEqual(tm.break_even_share(1.0, 0.1), 0.55)
        self.assertEqual(tm.verdict_for(0.60, 0.40)[0], "favourable")
        self.assertEqual(tm.verdict_for(0.42, 0.40)[0], "coin_flip")
        self.assertEqual(tm.verdict_for(0.20, 0.40)[0], "unfavourable")
        self.assertEqual(tm.verdict_for(None, 0.40)[0], "coin_flip")

    def test_bad_bracket_refused(self):
        with self.assertRaises(ValueError):
            tm.simulate(self.flat_pool(), 0, 5, entry=100, stop=101, target=102)


class HistoryCheckTest(unittest.TestCase):
    def test_counts_what_really_happened_after_each_fire(self):
        # 3 fires: the first reaches the target, the second the stop, the third neither; the fourth has no bars after it
        close = np.array([100, 100, 103, 100, 100, 97, 100, 100, 100.5, 100, 100], dtype=float)
        high = close + 0.2
        low = close - 0.2
        ts = np.arange(len(close)) * HOUR
        check = tm.replay_history(ts, high, low, close, entries=[0, 3, 6, 9], stop_dist=2.0, target_dist=2.0, max_bars=2)
        self.assertEqual((check.samples, check.wins, check.losses, check.timeouts), (3, 1, 1, 1))
        self.assertEqual(check.win_rate, 0.5)
        self.assertEqual([r["outcome"] for r in check.recent], ["target", "stop", "timeout"])
        self.assertEqual(check.break_even_win_rate, 0.5)  # R = 1, no costs

    def test_costs_lower_the_result_and_raise_the_bar(self):
        close = np.array([100, 103, 100, 100, 100], dtype=float)
        ts = np.arange(5) * HOUR
        free = tm.replay_history(ts, close + 0.2, close - 0.2, close, [0], 2.0, 2.0, 2)
        costly = tm.replay_history(ts, close + 0.2, close - 0.2, close, [0], 2.0, 2.0, 2, cost_r=0.2)
        self.assertGreater(free.expectancy_r, costly.expectancy_r)
        self.assertGreater(costly.break_even_win_rate, free.break_even_win_rate)

    def test_verdict_needs_enough_fires(self):
        check = tm.HistoryCheck(5, 4, 1, 0, 0.8, 0.5, 0.4, 3.0, 0, 1)
        self.assertEqual(tm.history_verdict(check)[0], "too_few")
        check = tm.HistoryCheck(40, 25, 15, 0, 0.625, 0.3, 0.45, 3.0, 0, 1)
        self.assertEqual(tm.history_verdict(check)[0], "positive")
        check = tm.HistoryCheck(40, 10, 30, 0, 0.25, -0.4, 0.45, 3.0, 0, 1)
        self.assertEqual(tm.history_verdict(check)[0], "negative")


if __name__ == "__main__":
    unittest.main()
