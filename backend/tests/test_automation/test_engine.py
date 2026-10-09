"""Bots end to end against a stand-in exchange: cycles, brackets, the fund gate, safety rules."""

import asyncio
import unittest

from backend.services.automation import (
    AutomationEngine,
    AutomationFund,
    BotRejectedError,
    PracticeLab,
)
from backend.shared.database.store import AuditStore
from backend.shared.models.automation import (
    AdvisorMode,
    AdvisorParams,
    BotStatus,
    CycleParams,
    LabSampleKind,
    LotStatus,
    ProposalStatus,
)
from backend.shared.models.order import OrderCreate, OrderSide, OrderType
from backend.tests.test_automation.fakes import (
    Adapter,
    Exchange,
    Market,
    Registry,
    Research,
    Risk,
    Router,
    Wallet,
)


class CoarseAdapter(Adapter):
    def rules(self, symbol):
        return super().rules(symbol).model_copy(update={"amount_step": 1e-5, "min_amount": 1e-5})


class CoarseRegistry(Registry):
    def get(self, venue_id):
        return CoarseAdapter()


FEES = {"V4": 10.0}


class EngineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.market = Market({"BTC/USDT": 100_000.0, "ETH/USDT": 4_000.0})
        self.wallet = Wallet(self.market, {"USDT": 10_000.0, "BTC": 0.05, "ETH": 1.0})
        self.store = AuditStore(":memory:")
        await self.store.open()
        self.engines: list[AutomationEngine] = []

    async def asyncTearDown(self) -> None:
        for e in self.engines:
            await e.stop()
        await self.store.close()

    def engine(self, mode: str = "testnet") -> AutomationEngine:
        self.risk = Risk()
        self.research = Research(self.market)
        fund = AutomationFund(self.wallet, self.market, mode, store=self.store)
        self.exchange = Exchange(self.market, self.wallet, guard=fund.check_order)
        lab = PracticeLab(self.store, self.research, None, self.market, mode, FEES)
        e = AutomationEngine(self.exchange, None, self.market, Router(), Registry(), self.risk, self.wallet, fund, lab,
                             ["BTC/USDT", "ETH/USDT"], research=self.research, store=self.store, mode=mode,
                             market_fee_bps=FEES)
        self.engines.append(e)
        return e

    async def started_cycle(self, e: AutomationEngine, **kw) -> str:
        await e.fund.deposit(500, "admin")
        bot = await e.create_cycle(CycleParams(symbol="BTC/USDT", **kw), "admin")
        await asyncio.sleep(0.05)  # the first loop turn: it buys the first lot
        await e.pause(bot.id, "test")  # the test drives the next steps itself
        return bot.id

    async def lab_bot(self, e: AutomationEngine) -> str:
        await e.fund.deposit(500, "admin")
        bot = await e.create_cycle(CycleParams(symbol="BTC/USDT", lab=True, label="lab: BTC"), "admin")
        await asyncio.sleep(0.05)
        return bot.id

    async def restarted(self, mode: str = "testnet") -> AutomationEngine:
        """A clean stop of every engine so far, then a new engine on the same database (a gateway restart)."""
        for e in self.engines:
            await e.stop()
        await self.store.flush()
        again = self.engine(mode)
        await again.load()
        return again

    async def test_a_cycle_buys_waits_and_sells_at_its_target(self) -> None:
        e = self.engine()
        bot = e.get(await self.started_cycle(e))
        lot = bot.lots[0]
        self.assertEqual((bot.stats.buys, lot.status), (1, LotStatus.OPEN))
        self.assertAlmostEqual(lot.qty, lot.bought_qty * 0.999)  # the buy fee came in the coin
        self.assertGreater(lot.target_price, self.market.ask("BTC/USDT") * 1.004)  # fees + 0.3%
        self.market.mid["BTC/USDT"] = lot.target_price * 0.9995  # bid just under the target: no sale
        await e._manage_exits(bot)
        self.assertEqual(lot.status, LotStatus.OPEN)
        self.market.mid["BTC/USDT"] = lot.target_price * 1.001
        await e._manage_exits(bot)
        self.assertEqual((lot.status, lot.exit_reason), (LotStatus.CLOSED, "target"))
        self.assertGreaterEqual(lot.profit_market_usdt, lot.sold_market_cost_usdt * 0.003 - 1e-9)
        sell = self.exchange.orders[-1]
        self.assertEqual((sell.side, sell.order_type), (OrderSide.SELL, OrderType.LIMIT))
        self.assertGreaterEqual(sell.limit_price, lot.target_price)  # rounded up to the tick, never down
        pocket = e.fund.pocket(bot.id)
        self.assertAlmostEqual(pocket.realized_usdt, lot.profit_usdt)
        # cash + what the coins it still holds (dust under one lot step) cost = budget + profit
        self.assertAlmostEqual(pocket.cash_usdt + pocket.cost_usdt.get("BTC", 0.0), 50 + pocket.realized_usdt)
        kinds = [s.kind for s in e.lab.samples()]
        self.assertEqual(kinds.count(LabSampleKind.EXECUTION), 2)
        self.assertTrue(any(s.kind == LabSampleKind.CYCLE_LOT and s.closed_at for s in e.lab.samples()))

    async def test_dust_from_a_coarse_lot_step_goes_out_with_the_next_sale(self) -> None:
        e = self.engine()
        e._registry = CoarseRegistry()
        bot = e.get(await self.started_cycle(e, buy_usdt=20, budget_usdt=60, cooldown_s=0))
        first = bot.lots[0]
        self.market.mid["BTC/USDT"] = first.target_price * 1.002
        await e._manage_exits(bot)
        pocket = e.fund.pocket(bot.id)
        dust = pocket.coins["BTC"]
        self.assertGreater(dust, 0)  # 0.00019 bought, 0.00018981 held after the fee, 0.00018 sold
        self.assertLess(dust, 1e-5)
        await e._cycle_step(bot)  # a new cycle
        second = bot.open_lots[0]
        self.market.mid["BTC/USDT"] = second.target_price * 1.002
        await e._manage_exits(bot)
        sold = self.exchange.orders[-1].quantity
        self.assertGreater(sold, second.bought_qty * 0.999 - 1e-5)  # this lot AND the earlier dust
        self.assertLess(pocket.coins["BTC"], 1e-5)  # never more than one step left behind
        self.assertAlmostEqual(pocket.cash_usdt + pocket.cost_usdt.get("BTC", 0.0), 60 + pocket.realized_usdt)

    async def test_another_lot_only_after_the_price_falls_by_the_spacing(self) -> None:
        e = self.engine()
        bot = e.get(await self.started_cycle(e, cooldown_s=0))
        await e._cycle_step(bot)
        self.assertEqual(len(bot.lots), 1)
        self.assertIn("next buy at", bot.waiting_reason)
        self.market.mid["BTC/USDT"] *= 0.994
        await e._cycle_step(bot)
        self.assertEqual(len(bot.open_lots), 2)

    async def test_manual_orders_cant_spend_the_fund(self) -> None:
        e = self.engine()
        await e.fund.deposit(9_000, "admin")
        manual = OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.015, order_type=OrderType.MARKET)
        order, error = await self.exchange.submit(manual, "trader1")  # ~$1,508 needed, $1,000 free
        self.assertIsNotNone(error)
        self.assertIn("automation fund", order.rejection_reason)
        ok = OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.009, order_type=OrderType.MARKET)
        self.assertIsNone((await self.exchange.submit(ok, "trader1"))[1])

    async def test_stop_returns_the_cash_and_sell_now_closes_below_cost(self) -> None:
        e = self.engine()
        bot_id = await self.started_cycle(e)
        await e.stop_bot(bot_id, "admin")
        bot = e.get(bot_id)
        self.assertEqual((bot.status, len(bot.open_lots)), (BotStatus.STOPPED, 1))
        self.assertAlmostEqual(e.fund.pocket(bot_id).cash_usdt, 0.0)
        self.market.mid["BTC/USDT"] *= 0.98
        await e.sell_now(bot_id, None, "admin")
        lot = bot.lots[0]
        self.assertEqual((lot.status, lot.exit_reason), (LotStatus.CLOSED, "sold by owner"))
        self.assertLess(lot.profit_usdt, 0)
        self.assertAlmostEqual(e.fund.pocket(bot_id).cash_usdt, 0.0)  # its cash went back to the fund
        dust_cost = e.fund.pocket(bot_id).cost_usdt.get("BTC", 0.0)  # under one lot step, still held
        self.assertAlmostEqual(e.fund.state.unassigned_usdt + dust_cost, 500 + lot.profit_usdt, places=6)

    async def test_released_coins_leave_the_book(self) -> None:
        e = self.engine()
        bot_id = await self.started_cycle(e)
        with self.assertRaises(BotRejectedError):
            await e.release_coins(bot_id, "lost", "admin")  # stop it first
        await e.stop_bot(bot_id, "admin")
        await e.release_coins(bot_id, "lost", "admin")
        self.assertEqual(e.get(bot_id).lots[0].status, LotStatus.WRITTEN_OFF)
        self.assertEqual(e.fund.reserved().get("BTC", 0.0), 0.0)

    async def test_kill_switch_pauses_and_a_restart_leaves_bots_paused(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        bot = await e.create_cycle(CycleParams(symbol="BTC/USDT"), "admin")
        await asyncio.sleep(0.05)
        self.risk.kill_switch.is_active = True
        await e._on_kill_switch({"active": True, "reason": "test"})
        self.assertEqual(e.get(bot.id).status, BotStatus.PAUSED)
        with self.assertRaises(BotRejectedError):
            await e.resume(bot.id, "admin")
        self.risk.kill_switch.is_active = False
        await e.resume(bot.id, "admin")
        await asyncio.sleep(0.05)
        await self.store.flush()
        again = self.engine()
        await again.load()
        restored = again.get(bot.id)
        self.assertEqual(restored.status, BotStatus.PAUSED)
        self.assertIn("restarted", restored.status_reason)
        self.assertEqual(len(restored.lots), 1)
        self.assertAlmostEqual(again.fund.state.allocated_usdt, 500)

    async def test_the_lab_server_resumes_only_the_lab_bots_the_restart_paused(self) -> None:
        e = self.engine()
        lab_bots = await e.start_preset("cycle_thresholds", "admin")
        await e.fund.deposit(100, "admin")
        own = await e.create_cycle(CycleParams(symbol="ETH/USDT"), "admin")  # a person's bot, not a lab bot
        await asyncio.sleep(0.05)
        await e.pause(lab_bots[0].id, "admin")  # a person paused this one: it waits for a person
        again = await self.restarted()
        self.assertTrue(all(b.status == BotStatus.PAUSED for b in again.bots().values()))
        resumed = await again.auto_resume()
        self.assertEqual(sorted(resumed), sorted(b.id for b in lab_bots[1:]))
        self.assertEqual(again.get(lab_bots[0].id).status, BotStatus.PAUSED)
        self.assertEqual(again.get(own.id).status, BotStatus.PAUSED)
        self.assertIn("automatically", again.get(lab_bots[1].id).status_reason)
        self.assertFalse(again.get(lab_bots[1].id).resume_after_restart)
        self.assertEqual(await again.auto_resume(), [])  # once: nothing is left to resume

    async def test_a_crash_counts_as_a_restart_too(self) -> None:
        e = self.engine()
        bot_id = await self.lab_bot(e)
        await self.store.flush()  # no clean stop: the database still says RUNNING
        again = self.engine()
        await again.load()
        self.assertTrue(again.get(bot_id).resume_after_restart)
        self.assertEqual(await again.auto_resume(), [bot_id])

    async def test_no_auto_resume_while_the_kill_switch_is_engaged(self) -> None:
        e = self.engine()
        bot_id = await self.lab_bot(e)
        again = await self.restarted()
        self.risk.kill_switch.is_active = True
        self.assertEqual(await again.auto_resume(), [])
        self.assertEqual(again.get(bot_id).status, BotStatus.PAUSED)
        self.risk.kill_switch.is_active = False
        self.assertEqual(await again.auto_resume(), [bot_id])  # the flag survives a refused attempt

    async def test_no_auto_resume_on_real_money(self) -> None:
        e = self.engine()
        bot_id = await self.lab_bot(e)
        again = await self.restarted(mode="mainnet")
        self.assertEqual(await again.auto_resume(), [])
        self.assertEqual(again.get(bot_id).status, BotStatus.PAUSED)

    async def test_auto_resume_waits_for_its_delay_and_a_stop_cancels_it(self) -> None:
        e = self.engine()
        bot_id = await self.lab_bot(e)
        again = await self.restarted()
        again.start(auto_resume_after_s=0.2)
        await asyncio.sleep(0.05)
        self.assertEqual(again.get(bot_id).status, BotStatus.PAUSED)  # not yet
        await asyncio.sleep(0.3)
        self.assertEqual(again.get(bot_id).status, BotStatus.RUNNING)
        third = await self.restarted()
        third.start(auto_resume_after_s=0.2)
        await third.stop()  # stopped before the delay: it never resumes anything
        await asyncio.sleep(0.3)
        self.assertEqual(third.get(bot_id).status, BotStatus.PAUSED)

    async def test_three_refusals_in_a_row_pause_the_bot(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        bot = await e.create_cycle(CycleParams(symbol="BTC/USDT", cooldown_s=0), "admin")
        self.exchange.refuse = "Risk check failed: position limit"
        bot = e.get(bot.id)
        for _ in range(3):
            e._buy_retry_at.clear()
            await e._cycle_step(bot)
        self.assertEqual(bot.status, BotStatus.PAUSED)
        self.assertIn("position limit", bot.status_reason)

    async def test_buys_smaller_than_an_exchange_accepts_are_refused_up_front(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        with self.assertRaises(BotRejectedError):
            await e.create_cycle(CycleParams(symbol="BTC/USDT", buy_usdt=5.0), "admin")  # $5 minimum + 5%

    async def test_advisor_proposes_then_the_owner_approves_and_the_stop_closes_it(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        bot = await e.create_advisor(AdvisorParams(symbols=["BTC/USDT"], budget_usdt=50, trade_usdt=10), "admin")
        await asyncio.sleep(0.05)
        bot = e.get(bot.id)
        await e.pause(bot.id, "test")
        proposal = bot.proposals[0]
        self.assertEqual(proposal.status, ProposalStatus.PROPOSED)
        self.assertTrue(any("Forecast: 60.0%" in r for r in proposal.reasons))
        await e.resume(bot.id, "admin")
        await e.pause(bot.id, "test")
        bot.status = BotStatus.RUNNING  # approve needs a running bot; keep the loop asleep
        await e.decide(proposal.id, True, "admin")
        self.assertEqual(proposal.status, ProposalStatus.APPROVED)
        lot = bot.open_lots[0]
        self.assertAlmostEqual(lot.stop_price, proposal.stop_price)
        self.market.mid["BTC/USDT"] = proposal.stop_price * 0.999
        await e._manage_exits(bot)
        self.assertEqual((lot.status, lot.exit_reason), (LotStatus.CLOSED, "stop"))
        self.assertLess(lot.profit_usdt, 0)
        self.assertEqual(bot.stats.losses, 1)

    async def test_auto_mode_approves_itself_on_testnet_only(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        bot = await e.create_advisor(AdvisorParams(symbols=["BTC/USDT"], mode=AdvisorMode.AUTO), "admin")
        await asyncio.sleep(0.05)
        self.assertEqual(e.get(bot.id).proposals[0].status, ProposalStatus.APPROVED)
        self.assertEqual(len(e.get(bot.id).open_lots), 1)
        real = self.engine(mode="mainnet")
        with self.assertRaises(BotRejectedError):
            await real.create_advisor(AdvisorParams(symbols=["BTC/USDT"], mode=AdvisorMode.AUTO), "admin")
        with self.assertRaises(BotRejectedError):
            await real.create_cycle(CycleParams(symbol="BTC/USDT", lab=True), "admin")
        with self.assertRaises(BotRejectedError):
            await real.start_preset("cycle_thresholds", "admin")

    async def test_a_proposal_is_refused_if_the_price_moved(self) -> None:
        e = self.engine()
        await e.fund.deposit(500, "admin")
        bot = await e.create_advisor(AdvisorParams(symbols=["BTC/USDT"]), "admin")
        await asyncio.sleep(0.05)
        proposal = e.get(bot.id).proposals[0]
        self.market.mid["BTC/USDT"] *= 1.02
        await e.decide(proposal.id, True, "admin")
        self.assertEqual(proposal.status, ProposalStatus.EXPIRED)
        self.assertIn("price moved", proposal.result)

    async def test_a_lab_preset_funds_and_starts_its_bots(self) -> None:
        e = self.engine()
        bots = await e.start_preset("cycle_thresholds", "admin")
        self.assertEqual(len(bots), 6)
        self.assertAlmostEqual(e.fund.state.allocated_usdt, 1_200)
        self.assertTrue(all(b.lab for b in bots))
        self.assertEqual(sorted({b.cycle.threshold_pct for b in bots}), [0.1, 0.3, 1.0])
        await asyncio.sleep(0.05)
        report = await e.lab.report(e.bots())
        self.assertEqual(len(report.scoreboard), 6)
        self.assertEqual(report.samples.get("CYCLE_LOT"), 6)


if __name__ == "__main__":
    unittest.main()
