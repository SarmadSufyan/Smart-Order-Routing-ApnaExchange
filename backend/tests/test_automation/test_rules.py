"""Profit-cycle decisions and the advisor's rule, as pure functions."""

import unittest
from datetime import UTC, datetime, timedelta

from backend.services.automation.advisor import evaluate_coin
from backend.services.automation.cycle import (
    allocate_sale,
    buy_decision,
    buy_quantity,
    drawdown_pct,
    lots_to_sell,
    target_price,
)
from backend.shared.models.automation import AdvisorParams, CycleParams, Lot

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def lot(i: int, price: float, qty: float = 0.0001, target: float | None = None, **kw) -> Lot:
    return Lot(id=i, symbol="BTC/USDT", qty=qty, bought_qty=qty, cost_usdt=qty * price, market_cost_usdt=qty * price,
               buy_price=price, target_price=target or price * 1.005, buy_order_id=f"o{i}", **kw)


class CycleTest(unittest.TestCase):
    def test_selling_at_the_target_earns_the_threshold_after_both_fees(self) -> None:
        cost, qty, sell_fee = 10.01, 0.0000999, 0.001  # $10 + a 0.1% buy fee taken in the coin
        t = target_price(cost, qty, sell_fee, 0.3)
        proceeds = qty * t * (1 - sell_fee)
        self.assertAlmostEqual(proceeds, cost * 1.003)
        self.assertAlmostEqual((proceeds - cost) / cost * 100, 0.3)

    def test_defaults_decided_with_sarmad(self) -> None:
        p = CycleParams(symbol="btc/usdt")
        self.assertEqual((p.symbol, p.budget_usdt, p.buy_usdt, p.threshold_pct, p.lots_allowed),
                         ("BTC/USDT", 50.0, 10.0, 0.3, 5))
        with self.assertRaises(ValueError):
            CycleParams(symbol="BTC/USDT", budget_usdt=5, buy_usdt=10)

    def test_buy_decisions(self) -> None:
        p = CycleParams(symbol="BTC/USDT", cooldown_s=60, spacing_pct=0.5, max_drawdown_pct=10)
        self.assertTrue(buy_decision(p, [], 50, 100, 99.9, None, NOW).buy)  # start a cycle
        open1 = [lot(1, 100)]
        self.assertIn("cooling", buy_decision(p, open1, 40, 99, 98.9, NOW - timedelta(seconds=10), NOW).reason)
        d = buy_decision(p, open1, 40, 99.8, 99.7, NOW - timedelta(minutes=5), NOW)
        self.assertFalse(d.buy)
        self.assertIn("next buy at 99.5", d.reason)  # 0.5% under the cheapest open lot
        self.assertTrue(buy_decision(p, open1, 40, 99.4, 99.3, NOW - timedelta(minutes=5), NOW).buy)
        self.assertIn("budget in use", buy_decision(p, open1, 9.99, 99.4, 99.3, None, NOW).reason)
        five = [lot(i, 100 - i) for i in range(5)]
        self.assertIn("all 5 lots", buy_decision(p, five, 50, 90, 89.9, None, NOW).reason)
        crash = [lot(1, 100)]
        self.assertIn("under cost", buy_decision(p, crash, 40, 88, 88, None, NOW).reason)
        self.assertAlmostEqual(drawdown_pct(crash, 88), 12.0)
        self.assertEqual(drawdown_pct(crash, 120), 0.0)

    def test_buy_quantity_fits_the_budget_at_the_protected_price(self) -> None:
        q = buy_quantity(10, 100_000, 0.005, 1e-6)
        self.assertLessEqual(q * 100_000 * 1.005, 10)
        self.assertAlmostEqual(q, 0.000099)

    def test_only_lots_at_their_target_sell_and_dust_waits_for_company(self) -> None:
        lots = [lot(1, 100, target=100.5), lot(2, 99, target=99.5), lot(3, 101, target=101.5)]
        self.assertEqual([x.id for x in lots_to_sell(lots, 100.6, 0.0, NOW)], [2, 1])
        self.assertEqual(lots_to_sell(lots, 99.0, 0.0, NOW), [])
        dust = [lot(1, 100, qty=0.00001, target=100.5)]
        self.assertEqual(lots_to_sell(dust, 101, min_qty=0.00005, now=NOW), [])
        waiting = [lot(1, 100, target=100.5, retry_after=NOW + timedelta(seconds=10))]
        self.assertEqual(lots_to_sell(waiting, 101, 0.0, NOW), [])

    def test_a_partial_sale_goes_to_the_cheapest_targets_first(self) -> None:
        a, b = lot(1, 100, qty=0.0002, target=100.6), lot(2, 99, qty=0.0001, target=99.6)
        self.assertEqual([(x.id, round(q, 8)) for x, q in allocate_sale([a, b], 0.00025)], [(2, 0.0001), (1, 0.00015)])


def forecast(p_up: float = 0.6, edge: bool = False) -> dict:
    return {"p_up": p_up, "edge": edge, "call": "No proven edge", "signals": [{"text": "RSI 35: near oversold"}]}


def plan(entry: float = 100.0, expectancy: float = 0.12, samples: int = 400) -> dict:
    return {"plan": {"entry": entry, "stop": entry * 0.985, "target": entry * 1.03},
            "history": {"samples": samples, "win_rate": 0.41, "break_even_win_rate": 0.36, "expectancy_r": expectancy},
            "cost_bps_round_trip": 24}


class AdvisorRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = AdvisorParams()

    def test_why_not(self) -> None:
        self.assertIn("no forecast", evaluate_coin(self.p, "BTC/USDT", {"unavailable": "loading"}, None, 100).why)
        self.assertIn("under 55%", evaluate_coin(self.p, "BTC/USDT", forecast(0.53), plan(), 100).why)
        strict = AdvisorParams(require_edge=True)
        self.assertIn("no proven edge", evaluate_coin(strict, "BTC/USDT", forecast(0.6), plan(), 100).why)
        self.assertIn("too few", evaluate_coin(self.p, "BTC/USDT", forecast(), plan(samples=10), 100).why)
        self.assertIn("averaged -0.050 R", evaluate_coin(self.p, "BTC/USDT", forecast(), plan(expectancy=-0.05), 100).why)
        self.assertFalse(evaluate_coin(self.p, "BTC/USDT", forecast(), plan(), None).propose)

    def test_a_proposal_carries_its_reasons_and_keeps_the_bracket_distances(self) -> None:
        v = evaluate_coin(self.p, "BTC/USDT", forecast(0.6), plan(entry=100), 102,
                          lab={"n": 4, "bucket": "60–65%", "win_rate": 0.5, "avg_return_pct": 0.1},
                          costs={"n": 25, "slippage_bps": 1.2})
        self.assertTrue(v.propose)
        self.assertAlmostEqual(v.stop, 102 * 0.985)
        self.assertAlmostEqual(v.target, 102 * 1.03)
        text = " ".join(v.reasons)
        for part in ("Forecast: 60.0%", "Bracket:", "History:", "Signals: RSI 35", "Our own practice: 4",
                     "too few to trust", "measured slippage +1.20 bps on 25", "Caution: the next-hour model"):
            self.assertIn(part, text)

    def test_auto_is_a_choice_and_sizes_are_checked(self) -> None:
        self.assertEqual(AdvisorParams().mode.value, "ASK")
        with self.assertRaises(ValueError):
            AdvisorParams(budget_usdt=5, trade_usdt=10)
        self.assertEqual(AdvisorParams(symbols=["eth/usdt", "ETH/USDT"]).symbols, ["ETH/USDT"])


if __name__ == "__main__":
    unittest.main()
