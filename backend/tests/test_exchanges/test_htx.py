"""HTX (V7): real money only - its feed (gzip snapshots), data-only outside real money, and its key check.

The frames are the ones HTX's public WebSocket sent on 2026-10-24 (trimmed to three levels).
"""

import asyncio
import gzip
import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from backend.exchanges.htx import HtxAdapter
from backend.exchanges.htx.adapter import decode_frame
from backend.exchanges.key_permissions import fetch_key_permissions, parse_htx, real_money_problem
from backend.exchanges.registry import EXCHANGE_CATALOG, _htx

BOOK = {"ch": "market.btcusdt.mbp.refresh.20", "ts": 1792784485222,
        "tick": {"seqNum": 194806192203, "bids": [[81325.63, 1.23961], [81324.98, 6.1e-05], [81324.33, 6.1e-05]],
                 "asks": [[81325.64, 2.779606], [81334.29, 0.090716], [81334.44, 0.196761]]}}
TICKER = {"ch": "market.btcusdt.ticker", "ts": 1792784482328,
          "tick": {"open": 81362.81, "high": 82022.6, "low": 81104.0, "close": 81316.67, "amount": 4411.391137881222,
                   "vol": 359989510.47832036, "count": 2219771, "bid": 81325.63, "bidSize": 1.23961, "ask": 81325.64,
                   "askSize": 2.779606, "lastPrice": 81316.67, "lastSize": 0.018299}}


def frame(msg: dict) -> bytes:
    return gzip.compress(json.dumps(msg).encode())  # HTX gzips every frame


def htx(mode: str = "mainnet", credentials: dict | None = None) -> HtxAdapter:
    a = HtxAdapter(venue_id="V7", name="HTX", mode=mode, symbols=["BTC/USDT"], credentials=credentials,
                   require_ip_lock=True)
    a._by_venue_symbol = {"btcusdt": "BTC/USDT"}  # what load_markets builds
    return a


class FeedTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_book_message_is_a_whole_snapshot(self) -> None:
        a = htx()
        self.assertTrue(a._on_message(frame(BOOK)))
        book = a.get_book("BTC/USDT")
        self.assertTrue(book.synced)
        self.assertEqual(book.best_bid(), (81325.63, 1.23961))
        self.assertEqual(book.best_ask(), (81325.64, 2.779606))
        self.assertEqual(book.level_count, (3, 3))
        newer = {**BOOK, "tick": {**BOOK["tick"], "seqNum": 194806192210, "bids": [[81330.0, 1.0]], "asks": [[81331.0, 2.0]]}}
        a._on_message(frame(newer))
        self.assertEqual((book.best_bid(), book.level_count), ((81330.0, 1.0), (1, 1)))  # replaced, not merged
        a._on_message(frame(BOOK))  # an older snapshot arriving late
        self.assertEqual(book.best_bid(), (81330.0, 1.0))

    async def test_ticker_errors_and_noise(self) -> None:
        a = htx()
        a._on_message(frame(TICKER))
        t = a.get_ticker("BTC/USDT")
        self.assertEqual((t.last_price, t.volume_base), (81316.67, 4411.391137881222))
        for msg in ({"status": "error", "err-code": "bad-request", "err-msg": "invalid topic"},
                    {"id": "b0", "status": "ok", "subbed": "market.btcusdt.mbp.refresh.20"},
                    {"ch": "market.dogeusdt.ticker", "tick": {"lastPrice": 1}}):
            self.assertTrue(a._on_message(frame(msg)))
        self.assertTrue(a._on_message(b"not gzip at all"))
        self.assertIsNone(decode_frame(b"\x1f\x8bbroken"))
        self.assertEqual(decode_frame(json.dumps({"a": 1})), {"a": 1})  # a text frame too

    async def test_a_ping_is_answered_with_its_pong(self) -> None:
        a = htx()
        sent: list[str] = []
        a._ws = SimpleNamespace(send=mock.AsyncMock(side_effect=sent.append))
        a._on_message(frame({"ping": 1792784485000}))
        await asyncio.sleep(0)  # the pong goes out on its own task
        await asyncio.sleep(0)
        self.assertEqual(sent, [json.dumps({"pong": 1792784485000})])


class NoSandboxTest(unittest.TestCase):
    def test_outside_real_money_htx_is_data_only(self) -> None:
        a = htx("testnet", credentials={"apiKey": "K", "secret": "S"})  # even if keys were set somehow
        self.assertFalse(a.execution_available)
        self.assertFalse(a.has_credentials)
        self.assertFalse(a.can_trade)
        self.assertFalse(a.key_check_required)
        self.assertIn("no sandbox", a.execution_note)
        self.assertEqual(a.data_mode, "mainnet")
        client = a._make_client(sandbox=True, with_keys=True)  # never a sandbox switch, never the keys
        self.assertFalse(client.apiKey)
        self.assertEqual(client.urls["api"], client.urls["api"])  # built without NotSupported (HTX has no test URL)

    def test_on_real_money_it_trades_once_htx_confirms_the_key(self) -> None:
        a = htx("mainnet", credentials={"apiKey": "K", "secret": "S"})
        self.assertTrue(a.execution_available and a.has_credentials and a.key_check_required)
        self.assertFalse(a.can_trade)  # blocked until HTX answers what the key may do
        self.assertTrue(a._make_client(sandbox=False, with_keys=True).apiKey)

    def test_registry_and_settings(self) -> None:
        self.assertEqual(EXCHANGE_CATALOG["htx"].venue_id, "V7")
        settings = SimpleNamespace(exchange_mode="mainnet", market_data_mode="mainnet", symbol_list=["BTC/USDT"],
                                   mainnet_require_ip_lock=True,
                                   exchange_credentials=lambda e: {"apiKey": "K", "secret": "S"} if e == "htx" else None)
        a = _htx(settings)
        self.assertIsInstance(a, HtxAdapter)
        self.assertEqual((a.venue_id, a.name, a.has_credentials), ("V7", "HTX", True))


def htx_answer(permission: str = "readOnly,trade", ips: str = "203.0.113.10", status: str = "normal",
               valid_days: int = -1, key: str = "K") -> dict:
    return {"code": 200, "message": "success", "data": [
        {"accessKey": "OTHER", "permission": "readOnly,trade,withdraw", "ipAddresses": "", "status": "normal"},
        {"accessKey": key, "note": "apnasor-real", "permission": permission, "ipAddresses": ips,
         "validDays": valid_days, "status": status, "createTime": 1792784485000, "updateTime": 1792784485000}]}


class KeyCheckTest(unittest.IsolatedAsyncioTestCase):
    def test_a_trade_only_ip_locked_key_is_accepted(self) -> None:
        perms = parse_htx(htx_answer(), "K")
        self.assertEqual((perms.can_trade_spot, perms.can_withdraw, perms.ip_restricted, perms.expires),
                         (True, False, True, None))
        self.assertIsNone(real_money_problem(perms, "HTX", require_ip_lock=True))

    def test_what_htx_must_refuse(self) -> None:
        cases = {
            "withdraw": parse_htx(htx_answer("readOnly,trade,withdraw"), "K"),
            "can't trade spot": parse_htx(htx_answer("readOnly"), "K"),  # the read-only test key
            "isn't locked": parse_htx(htx_answer(ips=""), "K"),
            "status expired": parse_htx(htx_answer(status="expired"), "K"),
            "isn't in HTX's answer": parse_htx(htx_answer(key="SOMEONE-ELSE"), "K"),
        }
        for words, perms in cases.items():
            with self.subTest(words):
                problem = real_money_problem(perms, "HTX", require_ip_lock=True)
                self.assertIsNotNone(problem)
                self.assertIn(words.split()[-1].lower(), (problem + perms.summary).lower())

    def test_a_key_that_expires_says_when(self) -> None:
        perms = parse_htx(htx_answer(ips="", valid_days=90), "K", today=datetime(2026, 10, 24, tzinfo=UTC))
        self.assertEqual(perms.expires, "2027-01-22")

    async def test_htx_is_asked_for_the_uid_then_the_key(self) -> None:
        calls = []

        class Client:
            async def v2private_get_user_uid(self):
                calls.append("uid")
                return {"code": 200, "data": 12345678}

            async def v2private_get_user_api_key(self, params):
                calls.append(params)
                return htx_answer()

        perms = await fetch_key_permissions(Client(), "htx", "K")
        self.assertEqual(calls, ["uid", {"uid": 12345678, "accessKey": "K"}])
        self.assertTrue(perms.can_trade_spot)


if __name__ == "__main__":
    unittest.main()
