"""The analyst's verdict rules: evidence -> BUY / NO_TRADE / REDUCE, honestly and never upgraded."""

import unittest

from backend.services.agents.analyst import MIN_ORDER_USDT, decide


def study(worked_in=(), worked_out=()):
    rows = [{"key": k, "name": k.title(), "why": "beat holding"} for k in (*worked_in, *worked_out)]
    return {"techniques": rows, "worked_in": list(worked_in), "worked_out": list(worked_out)}


def forecast(p_1h=0.5, edge_1h=False, p_1d=0.5, edge_1d=False):
    return {"horizons": [
        {"horizon": "1m", "label": "next minute", "p_up": 0.9, "edge": True},  # never counted
        {"horizon": "1h", "label": "next hour", "p_up": p_1h, "edge": edge_1h},
        {"horizon": "1d", "label": "next day", "p_up": p_1d, "edge": edge_1d}]}


def plan(positive=True, qty=0.001, entry=100_000.0):
    return {"rr": 2.0, "plan": {"verdict": "positive" if positive else "skip", "quantity": qty, "entry": entry,
                                "stop": entry * 0.98, "target": entry * 1.04},
            "history": {"samples": 4_000, "win_rate": 0.4 if positive else 0.3, "break_even_win_rate": 0.35,
                        "expectancy_r": 0.1 if positive else -0.2}}


class DecideTest(unittest.TestCase):
    def test_buy_needs_working_techniques_and_a_bracket_that_made_money(self) -> None:
        d = decide("BTC/USDT", forecast(), study(["trend", "breakout"]), study(), plan(True), max_notional=500)
        self.assertEqual((d["verdict"], d["score"]), ("BUY", 3))
        self.assertEqual(d["trade"]["side"], "BUY")
        self.assertAlmostEqual(d["trade"]["stop"], 98_000.0)
        self.assertEqual(d["confidence"], "low")  # no tested-edge forecast agrees: never more than low

    def test_a_losing_bracket_blocks_the_buy(self) -> None:
        d = decide("BTC/USDT", forecast(), study(["trend", "breakout"]), study(["momentum"]), plan(False))
        self.assertEqual(d["verdict"], "NO_TRADE")
        self.assertTrue(any("bracket" in r for r in d["against"]))

    def test_forecasts_without_an_edge_count_for_nothing(self) -> None:
        d = decide("BTC/USDT", forecast(p_1h=0.9, edge_1h=False, p_1d=0.9), study(), study(), None)
        self.assertEqual((d["verdict"], d["score"]), ("NO_TRADE", 0))
        self.assertTrue(any("no proven edge" in n for n in d["notes"]))
        with_edge = decide("BTC/USDT", forecast(p_1h=0.6, edge_1h=True), study(), study(), None)
        self.assertEqual(with_edge["score"], 1)

    def test_no_working_technique_in_means_no_buy_however_strong_the_rest(self) -> None:
        d = decide("BTC/USDT", forecast(0.6, True, 0.6, True), study(), study(), plan(True))
        self.assertEqual((d["score"], d["verdict"]), (3, "NO_TRADE"))

    def test_reduce_only_when_holding_and_the_evidence_is_against(self) -> None:
        args = (forecast(0.4, True), study(worked_out=["trend", "breakout"]), study(), plan(False))
        self.assertEqual(decide("ETH/USDT", *args, holding_value_usdt=0.0)["verdict"], "NO_TRADE")
        d = decide("ETH/USDT", *args, holding_value_usdt=200.0)
        self.assertEqual(d["verdict"], "REDUCE")
        self.assertEqual(d["confidence"], "medium")  # strong agreement incl. a tested-edge forecast

    def test_size_is_capped_and_a_too_small_trade_becomes_no_trade(self) -> None:
        d = decide("BTC/USDT", forecast(), study(["trend", "breakout"]), study(), plan(True, qty=0.01), max_notional=50)
        self.assertAlmostEqual(d["trade"]["notional_usdt"], 50.0)
        tiny = decide("BTC/USDT", forecast(), study(["trend", "breakout"]), study(), plan(True, qty=0.00001),
                      max_notional=1_000)
        self.assertEqual(tiny["verdict"], "NO_TRADE")
        self.assertLess(0.00001 * 100_000, MIN_ORDER_USDT)

    def test_missing_evidence_is_said_not_guessed(self) -> None:
        d = decide("SOL/USDT", None, None, None, None)
        self.assertEqual(d["verdict"], "NO_TRADE")
        self.assertTrue(any("still downloading" in n for n in d["notes"]))
        self.assertIn("not a promise", d["disclaimer"])


if __name__ == "__main__":
    unittest.main()
