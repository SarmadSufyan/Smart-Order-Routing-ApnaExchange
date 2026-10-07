"""Backtester (next-open execution, fees, no look-ahead) and the trade planner (bracket replay, Kelly, sizing)."""

import unittest

import numpy as np

from backend.services.research.backtest import positions, run_backtest
from backend.services.research.sizing import BracketStats, bracket_stats, plan_trade


class BacktestTest(unittest.TestCase):
    def test_buy_and_hold_earns_the_price_change_minus_one_entry_cost(self) -> None:
        o = np.array([100.0, 100, 110, 121, 121])
        res = run_backtest(np.arange(5), o, o, positions("buy_hold", {}, o), fee_bps=10, slippage_bps=0, capital=1000)
        # decided at close 0, bought at open 1 (100), held to the last open (121): x1.21, minus 0.1 %
        self.assertAlmostEqual(res.final_equity, 1000 * 0.999 * 1.21, places=6)
        self.assertEqual(res.trades, 1)
        self.assertAlmostEqual(res.fees_paid, 1.0)

    def test_a_decision_never_earns_the_candle_it_was_made_on(self) -> None:
        # The price jumps on candle 2; a rule that switches on at the close of candle 2 must miss it.
        o = np.array([100.0, 100, 100, 150, 150, 150])
        want = np.array([0, 0, 1, 1, 1, 1.0])
        res = run_backtest(np.arange(6), o, o, want, fee_bps=0, slippage_bps=0)
        self.assertAlmostEqual(res.total_return, 0.0)  # bought at the open of candle 3 = 150

    def test_rules_produce_positions_from_past_data_only(self) -> None:
        c = np.concatenate((np.linspace(100, 80, 60), np.linspace(80, 120, 60)))
        pos = positions("sma_cross", {"fast": 5, "slow": 20}, c)
        self.assertEqual(pos[30], 0)  # falling: fast below slow
        self.assertEqual(pos[-1], 1)  # rising: fast above slow
        full = positions("rsi_reversion", {}, c)
        np.testing.assert_array_equal(full[:80], positions("rsi_reversion", {}, c[:80]))
        with self.assertRaises(ValueError):
            positions("model", {}, c)
        with self.assertRaises(ValueError):
            positions("astrology", {}, c)

    def test_sharpe_counts_the_fees(self) -> None:
        o = np.full(40, 100.0)  # a flat market: churning can only lose the fees
        res = run_backtest(np.arange(40), o, o, np.array([1, 1, 0, 0] * 10, dtype=float), fee_bps=10, slippage_bps=0)
        self.assertLess(res.sharpe, 0)

    def test_fees_are_paid_on_every_switch(self) -> None:
        o = np.full(10, 100.0)
        want = np.array([1, 0] * 5, dtype=float)  # flip every candle
        res = run_backtest(np.arange(10), o, o, want, fee_bps=10, slippage_bps=5, capital=1000)
        self.assertLess(res.final_equity, 1000 * (1 - 0.0015) ** 7)
        self.assertGreaterEqual(res.trades, 4)


class SizingTest(unittest.TestCase):
    def path(self, moves: list[tuple[float, float, float]]):
        """[(high, low, close)] after a first candle closing at 100; ATR fixed at 2."""
        h = np.array([100.0] + [m[0] for m in moves])
        lo = np.array([100.0] + [m[1] for m in moves])
        c = np.array([100.0] + [m[2] for m in moves])
        return h, lo, c, np.full(len(c), 2.0)

    def test_target_first_is_a_win_stop_first_a_loss_both_a_loss(self) -> None:
        # stop = 100 - 1.5*2 = 97, target = 100 + 2*3 = 106; only the first candle is a sample (max_bars 2)
        win = self.path([(104, 99, 103), (107, 102, 106), (106, 105, 105)])
        s = bracket_stats(*win, k_atr=1.5, rr=2, max_bars=2, cost_bps_round_trip=0, start=0)
        self.assertGreaterEqual(s.wins, 1)
        both = self.path([(107, 96, 100), (100, 100, 100), (100, 100, 100)])
        s = bracket_stats(*both, k_atr=1.5, rr=2, max_bars=2, cost_bps_round_trip=0, start=0)
        self.assertGreaterEqual(s.losses, 1)  # touched both in one candle: counted as a loss

    def test_break_even_and_kelly(self) -> None:
        s = bracket_stats(*self.path([(101, 99, 100)] * 30), k_atr=1, rr=2, max_bars=3, cost_bps_round_trip=0)
        self.assertAlmostEqual(s.break_even_win_rate, 1 / 3, places=4)  # (1 + 0) / (2 + 1)
        self.assertGreater(s.timeouts, 0)
        # Kelly for p = 0.5, b = 2: 0.5 - 0.5 / 2 = 0.25
        stats = BracketStats(100, 50, 50, 0, 0.5, 0.5, 1 / 3, 0.25)
        plan = plan_trade(100.0, 2.0, equity=10_000, risk_pct=1, k_atr=1.5, rr=2, cost_bps_round_trip=20, stats=stats)
        self.assertAlmostEqual(plan.quantity, 100 / 3)  # 1 % of 10,000 / 3 per unit at risk
        self.assertEqual(plan.verdict, "positive")

    def test_no_edge_means_skip_and_size_is_capped(self) -> None:
        stats = BracketStats(100, 30, 70, 0, 0.3, -0.2, 1 / 3, -0.05)
        plan = plan_trade(100.0, 0.01, equity=1_000, risk_pct=5, k_atr=1, rr=2, cost_bps_round_trip=20, stats=stats,
                          max_notional=200)
        self.assertEqual(plan.verdict, "skip")
        self.assertAlmostEqual(plan.notional, 200)  # tiny ATR would ask for 5,000 units: capped
        self.assertTrue(any("capped" in r for r in plan.reasons))


if __name__ == "__main__":
    unittest.main()
