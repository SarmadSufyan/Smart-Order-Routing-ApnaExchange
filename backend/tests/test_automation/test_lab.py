"""The practice lab's statistics: confidence intervals, comparisons, costs, outcome models."""

import random
import unittest
from datetime import UTC, datetime, timedelta

from backend.services.automation.lab import (
    MIN_MODEL_SAMPLES,
    bootstrap_mean_ci,
    cost_table,
    cycle_scoreboard,
    daily_sums,
    execution_sample,
    outcome_model,
    permutation_p_value,
)
from backend.shared.models.automation import LabSample, LabSampleKind
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderType

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class StatsTest(unittest.TestCase):
    def test_bootstrap_interval_contains_the_mean(self) -> None:
        self.assertIsNone(bootstrap_mean_ci([1, 2, 3]))
        mean, lo, hi = bootstrap_mean_ci([0.1, 0.3, 0.2, 0.25, 0.15, 0.35, 0.05])
        self.assertAlmostEqual(mean, 0.2)
        self.assertLess(lo, mean)
        self.assertGreater(hi, mean)

    def test_permutation_test_tells_a_real_difference_from_noise(self) -> None:
        self.assertIsNone(permutation_p_value([1.0], [2.0]))
        same = permutation_p_value([1, 2, 3, 4, 5], [1.5, 2.5, 3.5, 4.5, 2])
        self.assertGreater(same, 0.3)
        apart = permutation_p_value([10, 11, 12, 13, 14, 15], [1, 2, 3, 4, 5, 6])
        self.assertLess(apart, 0.01)

    def test_daily_sums_count_quiet_days_as_zero(self) -> None:
        first = NOW - timedelta(days=2)
        sums = daily_sums([(first, 1.0), (first + timedelta(hours=1), 0.5), (NOW, 2.0)], first, NOW)
        self.assertEqual(sums, [1.5, 0.0, 2.0])


class ExecutionTest(unittest.TestCase):
    def test_slippage_is_a_cost_on_both_sides(self) -> None:
        o = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.001, order_type=OrderType.MARKET)
        o.child_orders = [ChildOrder(parent_order_id=o.id, venue_id="V4", quantity=0.001, price=100_010,
                                     filled_quantity=0.001, fill_price=100_020, fee=0.000001, fee_asset="BTC",
                                     sent_at=NOW)]
        s = execution_sample("CYCLE-1", "v", o, 100_000.0, {"V4": 10.0}, NOW)[0]
        self.assertAlmostEqual(s.outcome["slippage_bps"], 2.0)  # paid 2 bps over the mid
        self.assertAlmostEqual(s.outcome["vs_plan_bps"], 1.0, places=3)
        self.assertAlmostEqual(s.outcome["fee_bps"], 10.0, places=3)
        sell = Order(symbol="BTC/USDT", side=OrderSide.SELL, quantity=0.001, order_type=OrderType.MARKET)
        sell.child_orders = [ChildOrder(parent_order_id=sell.id, venue_id="V4", quantity=0.001, price=99_990,
                                        filled_quantity=0.0005, fill_price=99_980, fee=0.05, fee_asset="USDT",
                                        sent_at=NOW)]
        s2 = execution_sample("CYCLE-1", "v", sell, 100_000.0, {"V4": 10.0}, NOW)[0]
        self.assertAlmostEqual(s2.outcome["slippage_bps"], 2.0)  # got 2 bps under the mid
        self.assertAlmostEqual(s2.outcome["fill_ratio"], 0.5)
        rows = cost_table([s, s2])
        self.assertEqual((rows[0]["orders"], rows[0]["filled"], rows[0]["fill_ratio"]), (2, 2, 0.75))
        self.assertAlmostEqual(rows[0]["slippage_bps_median"], 2.0)


def cycle_sample(i: int, variant: str, opened: datetime, hold_h: float | None, profit: float = 0.03,
                 features: dict | None = None) -> LabSample:
    closed = opened + timedelta(hours=hold_h) if hold_h is not None else None
    outcome = {"status": "CLOSED" if closed else "OPEN"}
    if closed:
        outcome.update(exit_reason="target", hold_s=hold_h * 3600, profit_market_usdt=profit,
                       return_market_pct=profit / 10 * 100, win=True)
    return LabSample(id=f"s{i}-{variant}", kind=LabSampleKind.CYCLE_LOT, bot_id=f"B-{variant}", variant=variant,
                     symbol="BTC/USDT", opened_at=opened, closed_at=closed,
                     features={"budget_usdt": 50, "threshold_pct": 0.3, **(features or {})}, outcome=outcome)


class ScoreboardTest(unittest.TestCase):
    def test_variants_and_a_comparison_only_after_three_days(self) -> None:
        a = [cycle_sample(i, "a", NOW - timedelta(days=4, hours=-i), 2.0, 0.05) for i in range(10)]
        b = [cycle_sample(i, "b", NOW - timedelta(days=4, hours=-i), 5.0, 0.01) for i in range(10)]
        rows, comps = cycle_scoreboard(a + b, {"B-a": True}, NOW)
        row_a = next(r for r in rows if r["variant"] == "a")
        self.assertEqual((row_a["lots"], row_a["closed"], row_a["running"]), (10, 10, True))
        self.assertAlmostEqual(row_a["profit_market_usdt"], 0.5)
        self.assertEqual(row_a["median_hold_h"], 2.0)
        self.assertEqual(row_a["sold_within_24h"], 1.0)
        self.assertEqual(len(comps), 1)
        self.assertIn("p_value", comps[0])
        young = [cycle_sample(i, "c", NOW - timedelta(hours=5), 1.0) for i in range(3)]
        young += [cycle_sample(i, "d", NOW - timedelta(hours=5), 1.0) for i in range(3)]
        _, comps = cycle_scoreboard(young, {}, NOW)
        self.assertIn("at least 3 days", comps[0]["verdict"])


class OutcomeModelTest(unittest.TestCase):
    def samples(self, n: int, signal: bool) -> list[LabSample]:
        rng = random.Random(7)
        out = []
        for i in range(n):
            p_up = rng.uniform(0.4, 0.6)
            feats = {"p_up_1h": p_up, "p_up_1d": rng.uniform(0.4, 0.6), "rsi_1h": rng.uniform(20, 80),
                     "atr_pct_1h": rng.uniform(0.2, 1), "sigma_bps": rng.uniform(1, 10), "spread_bps": rng.uniform(0, 2),
                     "imbalance_10bps": rng.uniform(-1, 1), "ret_1h": rng.gauss(0, 0.5), "ret_24h": rng.gauss(0, 2),
                     "hour_sin": rng.uniform(-1, 1), "hour_cos": rng.uniform(-1, 1), "threshold_pct": 0.3}
            fast = (feats["rsi_1h"] < 50) if signal else (rng.random() < 0.5)
            out.append(cycle_sample(i, "v", NOW - timedelta(days=30) + timedelta(hours=i), 2.0 if fast else 40.0,
                                    features=feats))
        return out

    def test_collecting_until_there_are_enough_samples(self) -> None:
        m = outcome_model(self.samples(50, True), LabSampleKind.CYCLE_LOT, NOW)
        self.assertIn(f"50 of {MIN_MODEL_SAMPLES}", m["status"])
        self.assertIsNone(m["scores"])

    def test_a_real_pattern_is_found_and_noise_is_not(self) -> None:
        found = outcome_model(self.samples(400, True), LabSampleKind.CYCLE_LOT, NOW)
        self.assertTrue(found["edge"], found["status"])
        noise = outcome_model(self.samples(400, False), LabSampleKind.CYCLE_LOT, NOW)
        self.assertFalse(noise["edge"], noise["status"])


if __name__ == "__main__":
    unittest.main()
