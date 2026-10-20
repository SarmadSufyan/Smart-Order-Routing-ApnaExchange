"""The account's own taker fee on real money: reading it, and the live fee view the router and the bots share."""

import unittest

import ccxt.async_support as ccxt

from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.fees import (
    LiveFees,
    account_fee_note,
    fetch_account_taker_bps,
    taker_bps_from_ccxt,
)
from backend.services.routing_engine.strategies.best_price import fee_bps_for

SYMBOLS = ["BTC/USDT", "ETH/USDT"]


class FeeClient:
    """A ccxt client that answers the fee calls from a script."""

    def __init__(self, answers: list, has: dict | None = None) -> None:
        self.answers = list(answers)
        self.has = has if has is not None else {"fetchTradingFees": True}
        self.calls: list[str] = []

    async def _next(self):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def fetch_trading_fees(self):
        self.calls.append("all")
        return await self._next()

    async def fetch_trading_fee(self, symbol: str):
        self.calls.append(symbol)
        return await self._next()


class ParseTest(unittest.TestCase):
    def test_highest_rate_among_our_symbols_in_bps(self) -> None:
        raw = {"BTC/USDT": {"taker": 0.00075, "maker": 0.0006}, "ETH/USDT": {"taker": 0.001},
               "DOGE/USDT": {"taker": 0.002}}  # not ours: ignored
        self.assertEqual(taker_bps_from_ccxt(raw, SYMBOLS), 10.0)

    def test_unusable_answers_give_none(self) -> None:
        self.assertIsNone(taker_bps_from_ccxt(None, SYMBOLS))
        self.assertIsNone(taker_bps_from_ccxt({"BTC/USDT": {"taker": None}}, SYMBOLS))
        self.assertIsNone(taker_bps_from_ccxt({"BTC/USDT": {"taker": True}}, SYMBOLS))
        self.assertIsNone(taker_bps_from_ccxt({"BTC/USDT": {"taker": 0.5}}, SYMBOLS))  # 50 %: not a fee
        self.assertEqual(taker_bps_from_ccxt({"BTC/USDT": {"taker": 0}}, SYMBOLS), 0.0)  # a zero fee is real

    def test_note(self) -> None:
        self.assertIsNone(account_fee_note(None, "Binance"))
        self.assertIn("0.075%", account_fee_note(7.5, "Binance"))


class FetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_where_the_exchange_has_it(self) -> None:
        client = FeeClient([{"BTC/USDT": {"taker": 0.001}, "ETH/USDT": {"taker": 0.001}}])
        self.assertEqual(await fetch_account_taker_bps(client, SYMBOLS), 10.0)
        self.assertEqual(client.calls, ["all"])

    async def test_per_symbol_where_only_that_exists(self) -> None:  # OKX
        client = FeeClient([{"taker": 0.0008}, {"taker": 0.001}], has={"fetchTradingFee": True})
        self.assertEqual(await fetch_account_taker_bps(client, SYMBOLS), 10.0)
        self.assertEqual(client.calls, SYMBOLS)

    async def test_no_fee_call_at_all(self) -> None:
        self.assertIsNone(await fetch_account_taker_bps(FeeClient([], has={}), SYMBOLS))


class LiveFeesTest(unittest.TestCase):
    def test_the_account_rate_replaces_the_public_one_as_soon_as_it_is_read(self) -> None:
        account: dict[str, float] = {}
        fees = LiveFees({"V1": 10.0, "V4": 10.0}, account.get)
        self.assertEqual(dict(fees), {"V1": 10.0, "V4": 10.0})
        account["V1"] = 7.5  # an adapter read Binance's account fee
        self.assertEqual((fees["V1"], fees.get("V4"), fees.get("V9", 99.0)), (7.5, 10.0, 99.0))
        self.assertEqual(fee_bps_for(fees, "V1"), 7.5)  # the router reads it through the same view
        self.assertEqual(fees.public, {"V1": 10.0, "V4": 10.0})


class Venue(CcxtVenueAdapter):
    ccxt_id = "binance"
    exchange_id = "binance"

    async def _run_feed(self) -> None:
        return None


def venue(mode: str, client: FeeClient) -> Venue:
    v = Venue(venue_id="V1", name="Binance", mode=mode, symbols=SYMBOLS, credentials={"apiKey": "K", "secret": "S"})
    v._client = client
    return v


class AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_money_reads_the_account_fee_and_keeps_it_through_a_failure(self) -> None:
        v = venue("mainnet", FeeClient([{"BTC/USDT": {"taker": 0.00075}, "ETH/USDT": {"taker": 0.00075}},
                                        ccxt.NetworkError("timeout")]))
        self.assertIsNone(v.account_taker_fee_bps)
        self.assertTrue(await v._check_account_fee())
        self.assertEqual(v.account_taker_fee_bps, 7.5)
        self.assertIn("0.075%", v.fee_note)
        self.assertFalse(await v._check_account_fee())  # no answer: the last rate stays
        self.assertEqual(v.account_taker_fee_bps, 7.5)

    async def test_the_sandbox_never_asks(self) -> None:
        v = venue("testnet", FeeClient([]))
        self.assertFalse(v.key_check_required)  # the fee watcher starts only with the key check
        self.assertIsNone(v.fee_note)


if __name__ == "__main__":
    unittest.main()
