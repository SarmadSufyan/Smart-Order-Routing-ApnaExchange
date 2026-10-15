"""Technique study and deep-history backfill: new rules, verdicts against holding, older candles fetched once."""

import asyncio
import unittest

import numpy as np

from backend.exchanges.base import Candle
from backend.services.research import backtest as bt
from backend.services.research.candles import TIMEFRAME_MS, CandleStore, CandleSync
from backend.services.research.techniques import TECHNIQUES, judge, study

H = TIMEFRAME_MS["1h"]


def series(n=3_000, drift=0.0004, seed=7, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))
    ts = np.arange(n, dtype=np.int64) * H
    return ts, close.copy(), close  # open = close is fine for these tests


class RulesTest(unittest.TestCase):
    def test_momentum_and_breakout(self) -> None:
        c = np.array([10, 11, 12, 11, 10, 9, 10, 13, 14, 12, 8, 7, 9.0])
        np.testing.assert_array_equal(bt.positions("momentum", {"lookback": 2}, c),
                                      [0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1])
        # in when the close beats the highest of the 3 closes before it; out under the lowest of the last 2
        np.testing.assert_array_equal(bt.positions("donchian_breakout", {"period": 3, "exit_period": 2}, c),
                                      [0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0])

    def test_every_technique_names_a_known_rule(self) -> None:
        for t in TECHNIQUES:
            self.assertIn(t.strategy, bt.STRATEGIES)


class StudyTest(unittest.TestCase):
    def test_study_reports_every_technique_against_holding(self) -> None:
        ts, o, c = series()
        out = study(ts, o, c, "1h", fee_bps=10, slippage_bps=2)
        self.assertEqual(len(out["techniques"]), len(TECHNIQUES))
        self.assertEqual(out["bars"], 3_000)
        self.assertLess(out["recent_from_ts"], out["to_ts"])
        for row in out["techniques"]:
            self.assertIn(row["verdict"], ("worked", "mixed", "didn't work", "too few trades"))
            self.assertIn(row["signal"], ("in", "out"))
            self.assertIn("after costs", row["why"]) if row["verdict"] != "too few trades" else None
        self.assertEqual(set(out["worked_in"]) | set(out["worked_out"]),
                         {r["key"] for r in out["techniques"] if r["verdict"] == "worked"})
        self.assertIn("evidence, not proof", out["note"])

    def test_too_little_history_is_refused(self) -> None:
        ts, o, c = series(n=250)
        with self.assertRaises(ValueError):
            study(ts, o, c, "1h", 10, 2)

    def test_judge(self) -> None:
        def r(sharpe, ret, trades=10):
            return bt.BacktestResult(bars=100, start_ts=0, end_ts=1, total_return=ret, buy_hold_return=0.0, cagr=None,
                                     max_drawdown=0.1, buy_hold_max_drawdown=0.1, sharpe=sharpe, exposure=0.5,
                                     trades=trades, win_rate=0.5, avg_trade_return=0.0, fees_paid=0.0,
                                     final_equity=1.0)
        hold = r(0.5, 0.05)
        self.assertEqual(judge(r(1.0, 0.1), r(1.0, 0.02), hold, hold)[0], "worked")
        self.assertEqual(judge(r(1.0, 0.1), r(0.1, -0.02), hold, hold)[0], "mixed")
        self.assertEqual(judge(r(0.1, -0.1), r(0.1, -0.02), hold, hold)[0], "didn't work")
        self.assertEqual(judge(r(1.0, 0.1, trades=1), r(1.0, 0.1), hold, hold)[0], "too few trades")
        self.assertEqual(judge(r(1.0, 0.1), r(1.0, -0.01), hold, hold)[0], "mixed")  # beat holding but lost money


class FakeAdapter:
    venue_id = "V1"

    def __init__(self, first_listed_ts: int, now_ts: int) -> None:
        self.first, self.now, self.calls = first_listed_ts, now_ts, 0

    async def fetch_candles(self, symbol, timeframe, since_ms, limit):
        self.calls += 1
        start = max(since_ms, self.first)
        return [Candle(ts_ms=t, open=1, high=1, low=1, close=1, volume=1)
                for t in range(start, min(start + limit * H, self.now), H)]


class BackfillTest(unittest.IsolatedAsyncioTestCase):
    async def test_older_years_are_fetched_from_the_same_exchange_once(self) -> None:
        now = 10_000 * H
        store = CandleStore(":memory:")
        await asyncio.to_thread(store.open)
        adapter = FakeAdapter(first_listed_ts=2_000 * H, now_ts=now)
        registry = type("R", (), {"get": staticmethod(lambda vid: adapter if vid == "V1" else None)})()
        sync = CandleSync(registry, store, ["BTC/USDT"], clock=lambda: now / 1000,
                          history_bars={"1m": 10, "1h": 9_000, "1d": 10})
        sync._running = True
        # the newest 1,000 hours, as an earlier version would have stored them
        store.upsert("BTC/USDT", "1h", [(t, 1, 1, 1, 1, 1) for t in range(9_000 * H, now, H)], "V1")
        added = await sync.backfill("BTC/USDT", "1h")
        n, first, last, _ = store.info("BTC/USDT", "1h")
        self.assertEqual(first, 2_000 * H)  # back to the listing: the exchange has nothing older
        self.assertEqual((added, n, last), (7_000, 8_000, now - H))
        calls = adapter.calls
        self.assertEqual(await sync.backfill("BTC/USDT", "1h"), 0)  # nothing older exists: one more ask, no rows
        self.assertLessEqual(adapter.calls - calls, 1)


if __name__ == "__main__":
    unittest.main()
