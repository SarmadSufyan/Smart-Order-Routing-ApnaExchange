"""How an exchange's answer is classified: may the router retry elsewhere, must it stop routing there?"""

import unittest
from uuid import uuid4

import ccxt

from backend.exchanges.base import SymbolRules
from backend.exchanges.ccxt_venue import CcxtVenueAdapter, is_permission_error
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import OrderSide, OrderType

# OKX's real answer, seen live on 2026-10-02 (code 50124 isn't in ccxt's table yet)
OKX_50124 = 'okx {"code":"50124","data":[],"msg":"This API Key does not have trading permission for the market"}'


class FakeClient:
    def __init__(self, outcome) -> None:
        self.outcome = outcome

    def price_to_precision(self, symbol: str, price: float) -> str:
        return str(price)

    async def create_order(self, *args, **kwargs) -> dict:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Venue(CcxtVenueAdapter):
    ccxt_id = "okx"

    async def _run_feed(self) -> None:
        return None

    async def _reconcile(self, symbol: str, cid: str) -> dict | None:
        return None  # the lost order is confirmed "not found" without waiting


async def place(outcome):
    v = Venue(venue_id="V5", name="OKX", mode="testnet", symbols=["BTC/USDT"],
              credentials={"apiKey": "k", "secret": "s"})
    v._client = FakeClient(outcome)
    v._rules["BTC/USDT"] = SymbolRules(symbol="BTC/USDT", venue_symbol="BTC-USDT", base="BTC", quote="USDT",
                                       amount_step=0.00001, min_amount=0.00001, price_tick=0.1, min_notional=1.0)
    return await v.place_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.01, order_type=OrderType.LIMIT,
                               client_order_id=str(uuid4()), limit_price=100.0)


class ExecutionErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_permission_refusals_block_trading_and_allow_a_retry(self) -> None:
        for exc in (ccxt.ExchangeError(OKX_50124), ccxt.PermissionDenied("binance -2015 Invalid API-key"),
                    ccxt.AuthenticationError("invalid signature")):
            with self.subTest(exc=type(exc).__name__):
                r = await place(exc)
                self.assertEqual(r.exec_type, ExecType.REJECT)
                self.assertTrue(r.retry_elsewhere)
                self.assertTrue(r.blocks_trading)
                self.assertFalse(r.venue_fault)  # our key's fault: the venue's health score is untouched

    async def test_okx_50124_is_explained(self) -> None:
        r = await place(ccxt.ExchangeError(OKX_50124))
        self.assertEqual(r.reason, "OKX refused this API key's trading permission: This API Key does not have "
                                   "trading permission for the market")

    async def test_other_refusals_allow_a_retry_without_blocking(self) -> None:
        cases = {
            "exchange error": (ccxt.ExchangeError('okx {"code":"50001","msg":"Service temporarily unavailable"}'), True),
            "insufficient funds": (ccxt.InsufficientFunds("okx balance"), False),
            "invalid order": (ccxt.InvalidOrder("okx order size too small"), False),
            "rejected status": ({"id": "1", "status": "rejected", "filled": 0.0}, True),
            "lost, confirmed not executed": (ccxt.NetworkError("timeout"), True),
        }
        for name, (outcome, venue_fault) in cases.items():
            with self.subTest(name):
                r = await place(outcome)
                self.assertTrue(r.retry_elsewhere)
                self.assertFalse(r.blocks_trading)
                self.assertEqual(r.venue_fault, venue_fault)

    async def test_expired_or_filled_orders_are_never_retried_elsewhere(self) -> None:
        for outcome in ({"id": "2", "status": "expired", "filled": 0.0},
                        {"id": "3", "status": "canceled", "filled": 0.004, "average": 100.0},
                        {"id": "4", "status": "closed", "filled": 0.01, "average": 100.0}):
            with self.subTest(status=outcome["status"]):
                r = await place(outcome)
                self.assertFalse(r.retry_elsewhere)
                self.assertFalse(r.blocks_trading)

    def test_is_permission_error(self) -> None:
        self.assertTrue(is_permission_error(ccxt.ExchangeError(OKX_50124)))
        self.assertTrue(is_permission_error(ccxt.ExchangeError("user is not allowed to trade this symbol")))
        self.assertFalse(is_permission_error(ccxt.ExchangeError('{"msg":"Order price is out of range"}')))


if __name__ == "__main__":
    unittest.main()
