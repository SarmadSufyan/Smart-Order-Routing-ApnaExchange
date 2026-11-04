"""The trade desk service end to end on fake exchanges: the universe, the candles, a scan, a plan, the entry timer."""

import unittest

from backend.exchanges.base import Candle, usdt_spot_symbols
from backend.services.research import scanner as sc
from backend.services.research.candles import CandleStore
from backend.services.research.service import ResearchUnavailableError
from backend.services.research.trade_desk import TradeDeskService, parse_session_opens

DAY = 86_400_000
HOUR = 3_600_000
FIVE = 300_000
NOW_MS = DAY * 1_000 + 40 * 60_000 + 10_000  # 00:40:10 UTC on day 1000
TF_MS = {"5m": FIVE, "1h": HOUR, "1d": DAY}


def price_for(symbol: str, tf: str, ts: int) -> tuple[float, float, float, float]:
    """A designed tape: a slow drift down into the latest closed bar, which breaks out."""
    closed_before = NOW_MS - NOW_MS % TF_MS[tf]
    k = (closed_before - TF_MS[tf] - ts) // TF_MS[tf]  # 0 = the latest closed bar
    if symbol == "FLAT/USDT":
        close = 100.0
    elif tf == "5m":
        close = 101.0 if k == 0 else 100.0
    elif tf == "1h":
        close = 130.0 if k == 0 else 100.0 + k * 0.05
    else:
        close = 130.0 if k == 0 else 100.0 + k * 0.1
    return close, close + 0.2, close - 0.2, close


class FakeExchange:
    def __init__(self, venue_id, name, markets, data_mode="mainnet", broken=False):
        self.venue_id, self.name, self.data_mode, self.broken = venue_id, name, data_mode, broken
        self._markets = markets
        self.calls = 0

    async def data_markets(self):
        if self.broken:
            raise TimeoutError("unreachable")
        return usdt_spot_symbols(self._markets)

    async def fetch_candles(self, symbol, timeframe, since_ms=None, limit=1000):
        self.calls += 1
        if symbol not in self._markets:
            return []
        tf = TF_MS[timeframe]
        start = since_ms - since_ms % tf
        out = []
        for i in range(limit):
            ts = start + i * tf
            if ts > NOW_MS:
                break
            o, h, lo, c = price_for(symbol, timeframe, ts)
            out.append(Candle(ts_ms=ts, open=o, high=h, low=lo, close=c, volume=10.0))
        return out


class Registry:
    def __init__(self, *exchanges):
        self.ex = {e.venue_id: e for e in exchanges}

    def get(self, vid):
        return self.ex.get(vid)

    def all(self):
        return list(self.ex.values())


class NoBook:
    async def compute_nbbo(self, symbol):
        return None


class Limits:
    max_order_notional = 50.0


class Risk:
    limits = Limits()


def market(symbol, spot=True, active=True):
    return {"symbol": symbol, "spot": spot, "quote": symbol.split("/")[1].split(":")[0], "active": active}


BINANCE = {s: market(s) for s in ("AAA/USDT", "BBB/USDT", "BTC/USDT", "FLAT/USDT")}
BINANCE["SWAP/USDT:USDT"] = market("SWAP/USDT:USDT", spot=False)
BINANCE["DEAD/USDT"] = market("DEAD/USDT", active=False)
BYBIT = {s: market(s) for s in ("AAA/USDT", "BTC/USDT", "BBB/USDT", "FLAT/USDT")}
OKX = {s: market(s) for s in ("AAA/USDT", "BTC/USDT", "FLAT/USDT", "ZZZ/USDT")}


class TradeDeskTest(unittest.IsolatedAsyncioTestCase):
    def desk(self, *exchanges, universe="common", **kw) -> TradeDeskService:
        store = CandleStore(":memory:")
        store.open()
        self.addCleanup(store.close)
        self.store = store
        registry = Registry(*exchanges)
        return TradeDeskService(store, registry, NoBook(), ["BTC/USDT"], universe=universe, replays=300,
                                pool_days=10, risk_engine=Risk(), clock=lambda: NOW_MS / 1000, pace_s=0.0, **kw)

    def exchanges(self):
        return (FakeExchange("V1", "Binance", BINANCE), FakeExchange("V4", "Bybit", BYBIT),
                FakeExchange("V5", "OKX", OKX), FakeExchange("V6", "Gate", BINANCE, data_mode="testnet"))

    def test_usdt_spot_symbols_keeps_active_spot_markets_only(self):
        self.assertEqual(usdt_spot_symbols(BINANCE), ["AAA/USDT", "BBB/USDT", "BTC/USDT", "FLAT/USDT"])
        self.assertEqual(usdt_spot_symbols(None), [])

    def test_session_opens_parse(self):
        self.assertEqual(parse_session_opens("00:00,08:00,13:30"), (0, 480, 810))
        self.assertEqual(parse_session_opens(" 13:30, 00:00 "), (0, 810))
        self.assertEqual(parse_session_opens(""), sc.DEFAULT_SESSION_OPENS_MIN)

    async def test_universe_is_the_intersection_with_the_traded_coins_first(self):
        desk = self.desk(*self.exchanges())
        universe = await desk.discover_universe()
        self.assertEqual(universe, ["BTC/USDT", "AAA/USDT", "FLAT/USDT"])  # BBB missing on OKX, ZZZ only there
        self.assertIn("Binance, Bybit, OKX", desk.status()["universe"]["source"])
        self.assertNotIn("Gate", desk.status()["universe"]["source"])  # a sandbox's list is not the market

    async def test_universe_falls_back_when_fewer_than_two_lists_answer(self):
        desk = self.desk(FakeExchange("V1", "Binance", BINANCE), FakeExchange("V4", "Bybit", BYBIT, broken=True))
        self.assertEqual(await desk.discover_universe(), ["BTC/USDT"])
        self.assertIn("fewer than two", desk.status()["universe"]["source"])
        only = self.desk(*self.exchanges(), universe="symbols")
        self.assertEqual(await only.discover_universe(), ["BTC/USDT"])

    async def test_scan_finds_the_designed_setups_with_their_history_and_state(self):
        desk = self.desk(*self.exchanges())
        # the research sync owns the traded coins' hourly history: seed it and make sure the desk never trims it
        rows = [(NOW_MS - NOW_MS % HOUR - (i + 1) * HOUR, 100, 100.2, 99.8, 100, 1) for i in range(3_000)]
        self.store.upsert("BTC/USDT", "1h", rows, "V1")
        result = await desk.scan_now()
        self.assertEqual(self.store.info("BTC/USDT", "1h")[0], 3_000)
        self.assertEqual(result["universe"]["coins"], 3)
        self.assertEqual(result["universe"]["with_data"], 3)
        by_key = {(h["symbol"], h["setup"], h["timeframe"]): h for h in result["hits"]}
        self.assertIn(("AAA/USDT", "breakout", "1h"), by_key)
        self.assertIn(("AAA/USDT", "breakout", "1d"), by_key)
        self.assertIn(("AAA/USDT", "range_breakout", "5m"), by_key)
        self.assertIn(("BTC/USDT", "range_breakout", "5m"), by_key)
        self.assertNotIn("FLAT/USDT", {h["symbol"] for h in result["hits"]})  # nothing happens on a flat tape
        hit = by_key[("AAA/USDT", "breakout", "1h")]
        self.assertEqual(hit["age_bars"], 0)
        self.assertEqual(hit["state"], "triggered")
        self.assertEqual(hit["history_verdict"], "too_few")  # the designed tape never broke out before
        self.assertEqual(hit["history"]["samples"], 0)
        self.assertAlmostEqual(hit["details"]["level"], 101.0)
        self.assertFalse(hit["traded_here"])
        rng = by_key[("AAA/USDT", "range_breakout", "5m")]
        self.assertAlmostEqual(rng["details"]["range_high"], 100.2)
        self.assertEqual(rng["details"]["session_open_ts"], DAY * 1_000)
        self.assertEqual(desk.status()["hits"], len(result["hits"]))
        # the entry timer reads the same hit live
        live = await desk.entry("AAA/USDT", "breakout", "1h")
        self.assertEqual(live["state"], "triggered")
        with self.assertRaises(ResearchUnavailableError):
            await desk.entry("FLAT/USDT", "breakout", "1h")

    async def test_time_machine_and_plan(self):
        desk = self.desk(*self.exchanges())
        await desk.scan_now()
        odds = await desk.time_machine("AAA/USDT", "1h", entry=130, stop=129, target=131.5, max_bars=12)
        self.assertEqual(odds["odds"]["replays"], 300)
        self.assertAlmostEqual(odds["odds"]["p_target"] + odds["odds"]["p_stop"] + odds["odds"]["p_neither"], 1.0, places=3)
        self.assertGreater(odds["pool"]["bars"], 100)
        plan = await desk.plan("AAA/USDT", "1h", setup="breakout", equity=1_000, risk_pct=1.0)
        self.assertEqual(plan["side"], "BUY")
        self.assertIn(plan["verdict"], ("favourable", "mixed", "coin_flip", "skip"))
        self.assertGreater(plan["plan"]["quantity"], 0)
        self.assertLessEqual(plan["plan"]["notional"], 50.0 + 1e-6)  # the per-order limit caps it
        self.assertEqual(plan["history_verdict"], "too_few")
        self.assertTrue(any("every past time" in r for r in plan["reasons"]))
        plain = await desk.plan("AAA/USDT", "1h", equity=1_000)  # no setup: every past candle is the history
        self.assertGreater(plain["history"]["samples"], 100)
        with self.assertRaises(ResearchUnavailableError):
            await desk.plan("NOPE/USDT", "1h")
        with self.assertRaises(ResearchUnavailableError):
            await desk.plan("AAA/USDT", "1h", setup="not_a_setup")

    async def test_range_mid_stop_belongs_to_the_opening_range(self):
        desk = self.desk(*self.exchanges())
        await desk.scan_now()
        plan = await desk.plan("AAA/USDT", "5m", setup="range_breakout", stop_rule="range_mid")
        self.assertAlmostEqual(plan["plan"]["stop"], 100.0)  # the midpoint of a 99.8-100.2 range
        with self.assertRaises(ResearchUnavailableError):
            await desk.plan("AAA/USDT", "1h", setup="breakout", stop_rule="range_mid")

    async def test_no_scan_yet_and_disabled(self):
        desk = self.desk(*self.exchanges(), enabled=False)
        self.assertIsNone(desk.latest())
        await desk.start()  # disabled: no task
        self.assertIsNone(desk._task)
        with self.assertRaises(ResearchUnavailableError):
            await desk.entry("AAA/USDT", "breakout", "1h")


if __name__ == "__main__":
    unittest.main()
