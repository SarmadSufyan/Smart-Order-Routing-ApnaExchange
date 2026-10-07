"""Candle store + sync (closed candles only, fallback exchange), and the research service end to end."""

import unittest

import numpy as np

from backend.exchanges.base import Candle
from backend.services.research.candles import TIMEFRAME_MS, CandleStore, CandleSync
from backend.services.research.service import ResearchService, ResearchUnavailableError
from backend.shared.models.market_data import NBBO, VenueQuote

HOUR = 3_600_000
NOW = 1_000 * HOUR + 30 * 60_000  # half way through an hour


class FakeExchange:
    def __init__(self, venue_id: str, fail: bool = False, empty: bool = False) -> None:
        # ``symbols`` stays empty, as a real adapter's does until its markets have loaded
        self.venue_id, self.fail, self.empty, self.symbols, self.calls = venue_id, fail, empty, [], 0

    async def fetch_candles(self, symbol, timeframe, since_ms=None, limit=1000):
        self.calls += 1
        if self.fail:
            raise TimeoutError("unreachable")
        if self.empty:
            return []
        tf = TIMEFRAME_MS[timeframe]
        start = since_ms - since_ms % tf
        out = []
        for i in range(limit):
            ts = start + i * tf
            if ts > NOW:  # the exchange returns the forming candle too
                break
            price = 100 + np.sin(ts / tf / 7) * 5
            out.append(Candle(ts_ms=ts, open=price, high=price + 1, low=price - 1, close=price + 0.1, volume=10))
        return out


class Registry:
    def __init__(self, *exchanges) -> None:
        self.ex = {e.venue_id: e for e in exchanges}

    def get(self, vid):
        return self.ex.get(vid)


class CandleTest(unittest.IsolatedAsyncioTestCase):
    def store(self) -> CandleStore:
        s = CandleStore(":memory:")
        s.open()
        self.addCleanup(s.close)
        return s

    async def test_sync_stores_closed_candles_only_and_falls_back(self) -> None:
        store = self.store()
        binance, bybit = FakeExchange("V1", fail=True), FakeExchange("V4")
        sync = CandleSync(Registry(binance, bybit), store, ["BTC/USDT"], clock=lambda: NOW / 1000,
                          history_bars={"1m": 10, "1h": 48, "1d": 5})
        added = await sync.sync("BTC/USDT", "1h")
        self.assertEqual(added, 48)
        self.assertEqual(store.last_ts("BTC/USDT", "1h"), 999 * HOUR)  # the 1000th hour is still forming
        self.assertEqual(store.info("BTC/USDT", "1h")[3], "V4")
        self.assertEqual(await sync.sync("BTC/USDT", "1h"), 0)  # nothing new yet

    async def test_an_exchange_with_no_candles_falls_through_to_the_next(self) -> None:
        store = self.store()
        sync = CandleSync(Registry(FakeExchange("V1", empty=True), FakeExchange("V4")), store, ["BTC/USDT"],
                          clock=lambda: NOW / 1000, history_bars={"1m": 10, "1h": 10, "1d": 5})
        self.assertEqual(await sync.sync("BTC/USDT", "1h"), 10)
        self.assertEqual(store.info("BTC/USDT", "1h")[3], "V4")
        nothing = CandleSync(Registry(FakeExchange("V1", empty=True)), self.store(), ["BTC/USDT"],
                             clock=lambda: NOW / 1000, history_bars={"1m": 10, "1h": 10, "1d": 5})
        await nothing.sync("BTC/USDT", "1h")
        self.assertIn("no candles", nothing.status()[1]["error"])

    async def test_listeners_hear_about_new_candles(self) -> None:
        heard = []

        async def listener(symbol, tf):
            heard.append((symbol, tf))

        sync = CandleSync(Registry(FakeExchange("V1")), self.store(), ["BTC/USDT"], clock=lambda: NOW / 1000,
                          history_bars={"1m": 10, "1h": 10, "1d": 5})
        sync.on_new_candles(listener)
        await sync.sync("BTC/USDT", "1d")
        self.assertEqual(heard, [("BTC/USDT", "1d")])

    def test_forecast_log_is_scored_once(self) -> None:
        s = self.store()
        s.log_forecast("BTC/USDT", "1h", 5, 100.0, 0.6, 99.0, 101.0, True)
        s.log_forecast("BTC/USDT", "1h", 5, 999.0, 0.1, None, None, False)  # same candle: ignored
        self.assertEqual(s.unscored("BTC/USDT", "1h"), [(5, 100.0, 99.0, 101.0)])
        s.score_forecast("BTC/USDT", "1h", 5, 100.5, True, True)
        self.assertEqual(s.unscored("BTC/USDT", "1h"), [])
        self.assertEqual(s.scored("BTC/USDT", "1h"), [(5, 0.6, 1, 1, 1)])


class Market:
    def get_depth(self, vid, symbol, levels):
        return {"bids": [(99.95, 3.0), (99.9, 1.0)], "asks": [(100.05, 1.0), (100.1, 1.0)]}

    async def compute_nbbo(self, symbol):
        q = VenueQuote(venue_id="V1", symbol=symbol, bid_price=99.95, ask_price=100.05, bid_size=3, ask_size=1,
                       last_price=100, volume=0)
        return NBBO(symbol=symbol, best_bid=99.95, best_ask=100.05, best_bid_venue="V1", best_ask_venue="V1",
                    best_bid_size=3, best_ask_size=1, spread=0.1, venue_quotes={"V1": q})


def fill(store: CandleStore, tf: str, n: int, seed: int = 1) -> None:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    rows = [(i * TIMEFRAME_MS[tf], c * 0.999, c * 1.004, c * 0.995, c, 10 + rng.random()) for i, c in enumerate(close)]
    store.upsert("BTC/USDT", tf, rows, "V1")


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = CandleStore(":memory:")
        self.store.open()
        self.addCleanup(self.store.close)
        for tf in ("1m", "1h", "1d"):
            fill(self.store, tf, 900)
        sync = CandleSync(Registry(), self.store, ["BTC/USDT"])
        self.svc = ResearchService(self.store, sync, Market(), Market(), ["BTC/USDT"])

    async def test_forecast_covers_three_horizons_with_honest_fields(self) -> None:
        out = await self.svc.forecast("btc/usdt")
        self.assertEqual([h["horizon"] for h in out["horizons"]], ["1m", "1h", "1d"])
        h = out["horizons"][1]
        self.assertTrue(0 < h["p_up"] < 1)
        lo, hi = h["range80"]
        self.assertLess(lo, h["price"])
        self.assertGreater(hi, h["price"])
        self.assertIn("baseline_accuracy", h["model"]["out_of_sample"])
        self.assertEqual(len(h["drivers"]), 5)
        self.assertIn("not a promise", out["disclaimer"])

    async def test_each_closed_candle_logs_a_forecast_that_the_next_one_scores(self) -> None:
        await self.svc._on_new_candles("BTC/USDT", "1h")
        self.assertEqual(len(self.store.unscored("BTC/USDT", "1h")), 1)
        last = self.store.last_ts("BTC/USDT", "1h")
        self.store.upsert("BTC/USDT", "1h", [(last + HOUR, 1, 1, 1, 1.0, 1)], "V1")
        await self.svc._on_new_candles("BTC/USDT", "1h")
        board = await self.svc.scoreboard()
        live = next(r for r in board if r["horizon"] == "1h")["live"]
        self.assertEqual(live["n"], 1)
        self.assertEqual(len(self.store.unscored("BTC/USDT", "1h")), 1)  # the newest one waits for its candle

    async def test_backtest_trade_plan_chart_and_book(self) -> None:
        res = await self.svc.backtest("BTC/USDT", "1h", "model", {"threshold": 0.5}, 900, 1000, None, None)
        self.assertGreater(res["bars"], 50)
        self.assertGreater(res["start_ts"], 500 * HOUR)  # only where forecasts were out of sample
        plan = await self.svc.trade_plan("BTC/USDT", "1h", 1000, 1, 1.5, 2, 24)
        self.assertIn(plan["plan"]["verdict"], ("positive", "skip"))
        self.assertAlmostEqual(plan["plan"]["entry"], 100.05)  # a buy pays the live ask
        chart = await self.svc.chart("BTC/USDT", "1d", 100)
        self.assertEqual(len(chart["close"]), 100)
        book = await self.svc.orderbook("BTC/USDT")
        self.assertGreater(book["imbalance_10bps"], 0)  # more size on the bid
        self.assertGreater(book["microprice_vs_mid_bps"], 0)

    async def test_unknown_coin_and_missing_history_say_why(self) -> None:
        with self.assertRaises(ResearchUnavailableError):
            await self.svc.forecast("DOGE/USDT")
        store = CandleStore(":memory:")
        store.open()
        self.addCleanup(store.close)
        svc = ResearchService(store, CandleSync(Registry(), store, ["BTC/USDT"]), Market(), Market(), ["BTC/USDT"])
        out = await svc.forecast("BTC/USDT")
        self.assertTrue(all("still downloading" in h["unavailable"] for h in out["horizons"]))


if __name__ == "__main__":
    unittest.main()
