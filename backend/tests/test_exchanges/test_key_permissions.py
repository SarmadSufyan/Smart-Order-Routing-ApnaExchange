"""Real money: a venue trades only with a key its exchange confirms can trade spot and can NOT withdraw.

The parsers are fed the exchanges' documented answers (Binance apiRestrictions, Bybit query-api, OKX account
config, Gate main_keys); the adapter tests check that a mainnet venue is blocked until the exchange has answered,
stays blocked on a bad answer, and that testnet is untouched.
"""

import asyncio
import unittest

import ccxt

from backend.exchanges.ccxt_venue import CcxtVenueAdapter
from backend.exchanges.key_permissions import (
    KeyPermissions,
    fetch_key_permissions,
    key_note,
    key_warnings,
    parse_binance,
    parse_bybit,
    parse_gate,
    parse_okx,
    real_money_problem,
)

# Binance's documented example, with trading on and withdrawals off (the key ApnaSOR needs)
BINANCE = {"ipRestrict": False, "createTime": 1623840271000, "enableReading": True, "enableWithdrawals": False,
           "enableInternalTransfer": True, "enableMargin": False, "enableFutures": False,
           "permitsUniversalTransfer": True, "enableVanillaOptions": False, "enableFixApiTrade": False,
           "enableFixReadOnly": True, "enableSpotAndMarginTrading": True, "enablePortfolioMarginTrading": True}


def bybit(read_only: int = 0, spot: list | None = None, wallet: list | None = None, ips: list | None = None,
          expired_at: str = "1970-01-01T00:00:00Z") -> dict:
    return {"retCode": 0, "retMsg": "", "result": {
        "id": "1", "note": "apnasor", "apiKey": "K", "readOnly": read_only, "secret": "",
        "permissions": {"ContractTrade": [], "Spot": ["SpotTrade"] if spot is None else spot,
                        "Wallet": ["AccountTransfer"] if wallet is None else wallet, "Options": [],
                        "Derivatives": [], "Exchange": ["ExchangeHistory"]},
        "ips": ips if ips is not None else ["*"], "type": 1, "expiredAt": expired_at, "isMaster": True}}


def gate(key: str = "K", perms: list | None = None, ips: list | None = None, state: int = 1) -> list:
    return [{"key": "OTHER", "perms": [{"name": "wallet", "read_only": False}], "state": 1},
            {"key": key, "mode": 1, "name": "apnasor", "state": state, "ip_whitelist": ips or [],
             "perms": perms if perms is not None else [{"name": "spot", "read_only": False},
                                                       {"name": "wallet", "read_only": True}]}]


class ParserTest(unittest.TestCase):
    def test_binance(self) -> None:
        p = parse_binance(BINANCE)
        self.assertEqual((p.can_trade_spot, p.can_withdraw, p.ip_restricted), (True, False, False))
        self.assertIsNone(real_money_problem(p, "Binance"))
        self.assertIn("WITHDRAW", real_money_problem(parse_binance({**BINANCE, "enableWithdrawals": True}), "Binance"))
        self.assertIn("can't trade spot",
                      real_money_problem(parse_binance({**BINANCE, "enableSpotAndMarginTrading": False}), "Binance"))
        # A field the answer didn't carry is "unknown", and unknown is never good enough
        missing = {k: v for k, v in BINANCE.items() if k != "enableWithdrawals"}
        self.assertIsNone(parse_binance(missing).can_withdraw)
        self.assertIn("couldn't confirm", real_money_problem(parse_binance(missing), "Binance"))
        self.assertEqual(parse_binance({**BINANCE, "tradingAuthorityExpirationTime": 1_800_000_000_000}).expires,
                         "2027-01-15")

    def test_bybit(self) -> None:
        p = parse_bybit(bybit())
        self.assertEqual((p.can_trade_spot, p.can_withdraw, p.ip_restricted), (True, False, False))
        self.assertIsNone(real_money_problem(p, "Bybit"))
        self.assertTrue(parse_bybit(bybit(wallet=["AccountTransfer", "Withdraw"])).can_withdraw)
        self.assertFalse(parse_bybit(bybit(read_only=1)).can_trade_spot)  # read-only key: can't trade
        self.assertFalse(parse_bybit(bybit(spot=[])).can_trade_spot)
        self.assertTrue(parse_bybit(bybit(ips=["203.0.113.7"])).ip_restricted)
        self.assertEqual(parse_bybit(bybit(expired_at="2026-12-29T00:00:00Z")).expires, "2026-12-29")
        self.assertIsNone(parse_bybit(bybit()).expires)  # 1970 = no expiry
        self.assertIsNone(parse_bybit({"retCode": 10003, "result": {}}).can_withdraw)

    def test_okx(self) -> None:
        def answer(perm: str, ip: str = "") -> dict:
            return {"code": "0", "data": [{"uid": "1", "perm": perm, "ip": ip, "acctLv": "1"}], "msg": ""}

        self.assertIsNone(real_money_problem(parse_okx(answer("read_only,trade")), "OKX"))
        self.assertTrue(parse_okx(answer("read_only,withdraw,trade")).can_withdraw)
        self.assertFalse(parse_okx(answer("read_only")).can_trade_spot)
        self.assertTrue(parse_okx(answer("read_only,trade", ip="1.2.3.4")).ip_restricted)
        self.assertIsNone(parse_okx({"code": "0", "data": []}).can_withdraw)

    def test_gate_is_strict_about_the_wallet_area(self) -> None:
        self.assertIsNone(real_money_problem(parse_gate(gate(), "K"), "Gate"))
        # Gate lists no separate withdrawal flag: a writable wallet area may include it
        writable_wallet = parse_gate(gate(perms=[{"name": "spot", "read_only": False},
                                                 {"name": "wallet", "read_only": False}]), "K")
        self.assertIsNone(writable_wallet.can_withdraw)
        self.assertIsNotNone(real_money_problem(writable_wallet, "Gate"))
        self.assertTrue(parse_gate(gate(perms=[{"name": "withdraw", "read_only": False}]), "K").can_withdraw)
        self.assertFalse(parse_gate(gate(state=2), "K").can_trade_spot)  # a frozen key can't trade
        self.assertIsNone(parse_gate(gate(), "NOT-OURS").can_withdraw)  # our key isn't in the list
        self.assertTrue(parse_gate(gate(ips=["1.2.3.4"]), "K").ip_restricted)

    def test_no_answer_is_refused_and_notes_read_plainly(self) -> None:
        self.assertIn("can't tell us", real_money_problem(None, "Coinbase"))
        ok = KeyPermissions(True, False, False, "2026-12-29", "trade spot: yes, withdraw: no, IP list: no")
        self.assertEqual(len(key_warnings(ok)), 2)
        self.assertTrue(key_note(ok, None).startswith("Trade-only key confirmed"))
        self.assertEqual(key_note(ok, "the key can WITHDRAW"), "the key can WITHDRAW")

    def test_the_real_money_server_requires_an_ip_locked_key(self) -> None:
        unlocked, locked = parse_binance(BINANCE), parse_binance({**BINANCE, "ipRestrict": True})
        self.assertIsNone(real_money_problem(unlocked, "Binance"))  # the rule is opt-in for the pure check...
        self.assertIn("isn't locked to an IP", real_money_problem(unlocked, "Binance", require_ip_lock=True))
        self.assertIsNone(real_money_problem(locked, "Binance", require_ip_lock=True))
        unknown = {k: v for k, v in BINANCE.items() if k != "ipRestrict"}
        self.assertIn("couldn't be confirmed", real_money_problem(parse_binance(unknown), "Binance", True))
        # Withdrawal is still checked first: an IP lock never makes a withdrawing key acceptable
        self.assertIn("WITHDRAW", real_money_problem(parse_binance({**BINANCE, "enableWithdrawals": True,
                                                                     "ipRestrict": True}), "Binance", True))
        for parsed in (parse_bybit(bybit(ips=["203.0.113.7"])), parse_gate(gate(ips=["203.0.113.7"]), "K")):
            self.assertIsNone(real_money_problem(parsed, "X", True))
        self.assertIn("listed IP addresses", key_note(locked, None))

    def test_the_gateway_is_strict_unless_env_says_otherwise(self) -> None:
        from types import SimpleNamespace

        from backend.exchanges.registry import _ccxt_venue_kwargs

        base = dict(exchange_mode="mainnet", market_data_mode="mainnet", symbol_list=["BTC/USDT"],
                    exchange_credentials=lambda _e: None)
        self.assertTrue(_ccxt_venue_kwargs(SimpleNamespace(**base), "binance", "V1", "Binance")["require_ip_lock"])
        relaxed = SimpleNamespace(**base, mainnet_require_ip_lock=False)
        self.assertFalse(_ccxt_venue_kwargs(relaxed, "binance", "V1", "Binance")["require_ip_lock"])


class PermsClient:
    """The four exchanges' permission endpoints, answering from a script."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def _next(self):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else self.last
        self.last = answer
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def sapi_get_account_apirestrictions(self):
        return await self._next()

    async def private_get_v5_user_query_api(self):
        return await self._next()

    async def private_get_account_config(self):
        return await self._next()

    async def private_account_get_main_keys(self):
        return await self._next()


class Venue(CcxtVenueAdapter):
    ccxt_id = "binance"
    exchange_id = "binance"

    async def _run_feed(self) -> None:
        return None


def venue(mode: str, answers: list, require_ip_lock: bool = False) -> Venue:
    v = Venue(venue_id="V1", name="Binance", mode=mode, symbols=["BTC/USDT"],
              credentials={"apiKey": "K", "secret": "S"}, require_ip_lock=require_ip_lock)
    v._client = PermsClient(answers)
    return v


class AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_asks_the_right_endpoint(self) -> None:
        self.assertTrue((await fetch_key_permissions(PermsClient([BINANCE]), "binance", "K")).can_trade_spot)
        self.assertTrue((await fetch_key_permissions(PermsClient([bybit()]), "bybit", "K")).can_trade_spot)
        self.assertIsNone(await fetch_key_permissions(PermsClient([]), "coinbase", "K"))

    async def test_testnet_is_never_checked(self) -> None:
        v = venue("testnet", [])
        self.assertFalse(v.key_check_required)
        self.assertTrue(v.can_trade)
        self.assertIsNone(v.key_note)

    async def test_real_money_waits_for_the_exchange_then_trades(self) -> None:
        v = venue("mainnet", [ccxt.NetworkError("timeout"), BINANCE])
        self.assertFalse(v.can_trade)  # never assumed safe
        self.assertIn("can't withdraw", v.execution_note)
        self.assertFalse(await v._check_key_permissions())  # no answer: still blocked
        self.assertFalse(v.can_trade)
        self.assertTrue(await v._check_key_permissions())
        self.assertTrue(v.can_trade)
        self.assertIsNone(v.execution_note)
        self.assertTrue(v.key_note.startswith("Trade-only key confirmed"))

    async def test_a_key_that_can_withdraw_gets_no_orders_and_a_later_fix_is_seen(self) -> None:
        v = venue("mainnet", [{**BINANCE, "enableWithdrawals": True}, ccxt.NetworkError("x"), BINANCE])
        await v._check_key_permissions()
        self.assertFalse(v.can_trade)
        self.assertIn("WITHDRAW", v.execution_note)
        await v._check_key_permissions()  # a failed re-check keeps the verdict
        self.assertFalse(v.can_trade)
        await v._check_key_permissions()  # the owner made a trade-only key
        self.assertTrue(v.can_trade)

    async def test_an_unlocked_key_gets_no_orders_on_the_real_money_server(self) -> None:
        v = venue("mainnet", [BINANCE, {**BINANCE, "ipRestrict": True}], require_ip_lock=True)
        await v._check_key_permissions()
        self.assertFalse(v.can_trade)
        self.assertIn("isn't locked to an IP", v.execution_note)
        await v._check_key_permissions()  # the owner locked the key to the server's IP
        self.assertTrue(v.can_trade)

    async def test_a_confirmed_key_stays_usable_through_a_failed_recheck(self) -> None:
        v = venue("mainnet", [BINANCE, ccxt.NetworkError("x")])
        await v._check_key_permissions()
        await v._check_key_permissions()
        self.assertTrue(v.can_trade)

    async def test_the_watcher_rechecks_until_stopped(self) -> None:
        import backend.exchanges.ccxt_venue as module

        v = venue("mainnet", [ccxt.NetworkError("x"), BINANCE])
        v._running = True
        saved = (module.KEY_RECHECK_S, module.KEY_RETRY_S)
        module.KEY_RECHECK_S, module.KEY_RETRY_S = 0.01, 0.01
        try:
            task = asyncio.create_task(v._watch_key_permissions())
            await asyncio.sleep(0.1)
            v._running = False
            await asyncio.wait_for(task, 1)
        finally:
            module.KEY_RECHECK_S, module.KEY_RETRY_S = saved
        self.assertTrue(v.can_trade)
        self.assertGreaterEqual(v._client.calls, 3)


if __name__ == "__main__":
    unittest.main()
