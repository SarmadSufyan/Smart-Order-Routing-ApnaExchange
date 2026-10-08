"""The automation fund: fill accounting, real-market fees, moving money, and the order gate."""

import unittest

from backend.services.automation.fund import (
    AutomationFund,
    FundError,
    pocket_value,
    reserved_assets,
    summarize_order,
)
from backend.shared.models.automation import FundState, Pocket
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderType
from backend.tests.test_automation.fakes import Market, Wallet

FEES = {"V1": 10.0, "V4": 10.0}


def order(side: OrderSide, fills: list[tuple[str, float, float, float, str | None]], symbol: str = "BTC/USDT") -> Order:
    """fills: (venue, qty, price, fee, fee_asset)"""
    o = Order(symbol=symbol, side=side, quantity=sum(f[1] for f in fills), order_type=OrderType.MARKET)
    for venue, q, px, fee, asset in fills:
        o.child_orders.append(ChildOrder(parent_order_id=o.id, venue_id=venue, quantity=q, price=px,
                                         filled_quantity=q, fill_price=px, fee=fee, fee_asset=asset))
    return o


def price(asset: str, venue: str) -> float | None:
    return {"USDT": 1.0, "BNB": 600.0, "BTC": 100_000.0}.get(asset)


class SummarizeTest(unittest.TestCase):
    def test_a_fee_in_the_coin_means_fewer_coins(self) -> None:
        s = summarize_order(order(OrderSide.BUY, [("V4", 0.001, 100_000, 0.000001, "BTC")]), price, FEES)
        self.assertAlmostEqual(s.base_change, 0.000999)
        self.assertAlmostEqual(s.quote_change, -100.0)
        self.assertAlmostEqual(s.fee_usdt, 0.1)
        self.assertAlmostEqual(s.market_fee_usdt, 0.1)  # charged = the real-market rate here
        self.assertEqual(s.venues, ["V4"])

    def test_a_fee_free_testnet_still_counts_the_real_market_fee(self) -> None:
        s = summarize_order(order(OrderSide.BUY, [("V1", 0.001, 100_000, 0.0, "BTC")]), price, FEES)
        self.assertAlmostEqual(s.fee_usdt, 0.0)
        self.assertAlmostEqual(s.market_fee_usdt, 0.1)  # 10 bps of $100

    def test_sell_fee_in_usdt_and_a_third_coin_fee_comes_out_of_cash(self) -> None:
        s = summarize_order(order(OrderSide.SELL, [("V4", 0.001, 100_000, 0.1, "USDT"),
                                                   ("V1", 0.001, 100_000, 0.0001, "BNB")]), price, FEES)
        self.assertAlmostEqual(s.base_change, -0.002)
        self.assertAlmostEqual(s.quote_change, 200 - 0.1 - 0.06)  # BNB fee valued at 600
        self.assertAlmostEqual(s.fee_usdt, 0.16)
        self.assertAlmostEqual(s.avg_price, 100_000)

    def test_reserved_and_pocket_value(self) -> None:
        state = FundState(unassigned_usdt=20, pockets={
            "A": Pocket(cash_usdt=30, coins={"BTC": 0.001}, cost_usdt={"BTC": 99}),
            "B": Pocket(cash_usdt=5, coins={"BTC": 0.002, "ETH": 0.0}, cost_usdt={"BTC": 210})})
        self.assertEqual(reserved_assets(state), {"USDT": 55, "BTC": 0.003})
        value, unrealized = pocket_value(state.pockets["A"], lambda a: 100_000.0)
        self.assertAlmostEqual(value, 130.0)
        self.assertAlmostEqual(unrealized, 1.0)
        self.assertEqual(pocket_value(state.pockets["A"], lambda a: None), (None, None))


class FundTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.market = Market({"BTC/USDT": 100_000.0})
        self.wallet = Wallet(self.market, {"USDT": 1_000.0, "BTC": 0.01})
        self.fund = AutomationFund(self.wallet, self.market, "testnet", protection_bps=50)

    async def test_money_moves_in_and_out_only_within_what_is_free(self) -> None:
        with self.assertRaises(FundError):
            await self.fund.deposit(1_000.5, "admin")
        await self.fund.deposit(300, "admin")
        with self.assertRaises(FundError):
            await self.fund.deposit(700.5, "admin")  # 300 is already set aside
        await self.fund.assign("CYCLE-1", 120, "admin")
        with self.assertRaises(FundError):
            await self.fund.assign("CYCLE-2", 200, "admin")  # only 180 unassigned
        with self.assertRaises(FundError):
            await self.fund.withdraw(200, "admin")  # the bot's 120 isn't unassigned
        await self.fund.withdraw(80, "admin")
        self.assertAlmostEqual(self.fund.state.allocated_usdt, 220)
        self.assertAlmostEqual(self.fund.state.unassigned_usdt, 100)
        self.assertAlmostEqual(await self.fund.release("CYCLE-1", "admin"), 120)
        self.assertAlmostEqual(self.fund.state.unassigned_usdt, 220)
        self.assertEqual([e.action for e in self.fund.state.history], ["deposit", "assign", "withdraw", "release"])

    async def test_without_a_fund_manual_trading_is_unchanged(self) -> None:
        big = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET)
        self.assertIsNone(await self.fund.check_order(big, None))  # the router's own funding gate decides

    async def test_manual_orders_cant_use_what_the_fund_holds(self) -> None:
        await self.fund.deposit(800, "admin")
        await self.fund.assign("CYCLE-1", 100, "admin")
        self.fund.state.pockets["CYCLE-1"].coins["BTC"] = 0.008
        buy = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.003, order_type=OrderType.MARKET)
        reason = await self.fund.check_order(buy, None)  # ~$301.5 needed, $200 free for manual trading
        self.assertIn("automation fund", reason)
        small = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.0015, order_type=OrderType.MARKET)
        self.assertIsNone(await self.fund.check_order(small, None))
        sell = Order(symbol="BTC/USDT", side=OrderSide.SELL, quantity=0.003, order_type=OrderType.MARKET)
        self.assertIn("belongs to bots", await self.fund.check_order(sell, None))  # 0.01 - 0.008 free
        self.assertIsNone(await self.fund.check_order(
            Order(symbol="BTC/USDT", side=OrderSide.SELL, quantity=0.002, order_type=OrderType.MARKET), None))
        algo_child = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.003, order_type=OrderType.MARKET)
        self.assertIsNotNone(await self.fund.check_order(algo_child, "TWAP-ABC123"))  # an algo is manual money

    async def test_a_bot_order_may_only_use_its_own_pocket(self) -> None:
        await self.fund.deposit(500, "admin")
        await self.fund.assign("CYCLE-1", 50, "admin")
        too_big = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.0006, order_type=OrderType.MARKET)
        self.assertIn("left in its pocket", await self.fund.check_order(too_big, "CYCLE-1"))
        ok = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.0004, order_type=OrderType.MARKET)
        self.assertIsNone(await self.fund.check_order(ok, "CYCLE-1"))
        sell = Order(symbol="BTC/USDT", side=OrderSide.SELL, quantity=0.0001, order_type=OrderType.MARKET)
        self.assertIn("can't sell", await self.fund.check_order(sell, "CYCLE-1"))

    async def test_buy_then_sell_books_the_profit_at_both_fee_levels(self) -> None:
        await self.fund.deposit(500, "admin")
        await self.fund.assign("B", 100, "admin")
        buy = summarize_order(order(OrderSide.BUY, [("V1", 0.001, 100_000, 0.0, "BTC")]), price, FEES)  # fee-free
        self.fund.apply_buy("B", "BTC", buy)
        pocket = self.fund.pocket("B")
        self.assertAlmostEqual(pocket.cash_usdt, 0.0)
        self.assertAlmostEqual(pocket.cost_usdt["BTC"], 100.0)
        self.assertAlmostEqual(pocket.market_cost_usdt["BTC"], 100.1)
        sell = summarize_order(order(OrderSide.SELL, [("V1", 0.001, 101_000, 0.0, "USDT")]), price, FEES)
        self.fund.apply_sell("B", "BTC", sell, cost=100.0, market_cost=100.1)
        self.assertAlmostEqual(pocket.realized_usdt, 1.0)
        self.assertAlmostEqual(pocket.realized_market_usdt, 101 - 0.101 - 100.1)
        self.assertAlmostEqual(pocket.coins["BTC"], 0.0)
        view = self.fund.view()
        self.assertAlmostEqual(view.pnl_usdt, 1.0)
        self.assertAlmostEqual(view.pnl_market_usdt, 101 - 0.101 - 100.1)

    async def test_releasing_coins_to_manual_keeps_the_pnl_and_lost_coins_cost_their_value(self) -> None:
        await self.fund.deposit(500, "admin")
        await self.fund.assign("B", 100, "admin")
        self.fund.apply_buy("B", "BTC", summarize_order(order(OrderSide.BUY, [("V4", 0.001, 100_000, 0, "BTC")]),
                                                        price, FEES))
        before = self.fund.view().pnl_usdt
        await self.fund.release_coins("B", "BTC", "to_manual", "admin")
        self.assertAlmostEqual(self.fund.view().pnl_usdt, before)
        self.assertEqual(self.fund.reserved().get("BTC", 0.0), 0.0)

        self.fund.apply_buy("B", "BTC", summarize_order(order(OrderSide.BUY, [("V4", 0.0001, 100_000, 0, "BTC")]),
                                                        price, FEES))
        before = self.fund.view().pnl_usdt
        await self.fund.release_coins("B", "BTC", "lost", "admin")
        self.assertAlmostEqual(self.fund.view().pnl_usdt, before - 10.0)


if __name__ == "__main__":
    unittest.main()
