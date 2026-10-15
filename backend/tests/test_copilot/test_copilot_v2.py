"""Copilot 2: analyses, answers, execution algorithms, bots, sandbox Auto and conversation memory.

The model is a scripted stub; the analyst and the bot engine are stand-ins returning fixed evidence. Pre-trade
risk is the real engine (from test_copilot's stubs), so a confirmed order still meets real limits.
"""

import json
from types import SimpleNamespace

from backend.services.agents.copilot import Copilot, CopilotUnavailableError, writer_prompt
from backend.services.agents.llm import LLMError
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.shared.models.copilot import ProposalStatus
from backend.shared.models.strategy import AlgoKind
from backend.tests.test_copilot.test_copilot import (
    CopilotTestBase,
    StubEntry,
    StubMarket,
    StubTwap,
    intent,
)


class StubAnalyst:
    """Fixed evidence per coin; records what it was asked."""

    def __init__(self, verdicts: dict[str, str] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.analyzed: list[str] = []
        self.topics: list[tuple] = []

    async def analyze(self, symbol: str) -> dict:
        self.analyzed.append(symbol)
        v = self.verdicts.get(symbol, "NO_TRADE")
        trade = ({"side": "BUY", "symbol": symbol, "quantity": 0.0005, "entry": 100_010.0, "stop": 98_000.0,
                  "target": 104_000.0, "notional_usdt": 50.0} if v == "BUY" else None)
        decision = {"symbol": symbol, "verdict": v, "confidence": "low", "score": 3 if v == "BUY" else 0,
                    "summary": f"{v} {symbol}", "for": ["Trend worked on 5 years of hours"], "against": [],
                    "notes": [], "trade": trade, "disclaimer": "not a promise"}
        return {"facts": {"symbol": symbol, "decision": decision, "forecasts": []}, "decision": decision,
                "studies": {"1h": None, "1d": None}}

    async def topic_facts(self, topic: str, symbol: str | None = None) -> dict:
        self.topics.append((topic, symbol))
        return {"quotes": [{"symbol": "BTC/USDT", "mid": 100_000.0, "spread_bps": 2.0, "best_bid_venue": "V1",
                            "best_ask_venue": "V4"}]}


class StubAutomation:
    def __init__(self, unassigned: float = 500.0) -> None:
        self.created: list[tuple] = []
        self.fund = SimpleNamespace(view=lambda: SimpleNamespace(unassigned_usdt=unassigned))

    async def create_cycle(self, params, user):
        self.created.append(("cycle", params, user))
        return SimpleNamespace(id="CYCLE-ABC")

    async def create_advisor(self, params, user):
        self.created.append(("advisor", params, user))
        return SimpleNamespace(id="ADVISOR-ABC")


def answer(text: str) -> dict:
    return {"answer": text}


class V2Base(CopilotTestBase):
    def make2(self, *answers, verdicts=None, mode="testnet", unassigned=500.0, auto_max=50.0) -> Copilot:
        cp = self.make(*answers)
        self.analyst = StubAnalyst(verdicts)
        self.automation = StubAutomation(unassigned)
        cp.attach(analyst=self.analyst, automation=self.automation, mode=mode, auto_max_usdt=auto_max)
        return cp


class AnalyzeTest(V2Base):
    async def test_a_buy_verdict_becomes_an_order_to_confirm_with_its_reasons(self) -> None:
        cp = self.make2(intent(action="analyze", base_asset="BTC"), answer("Buy a little BTC: trend says in."),
                        verdicts={"BTC/USDT": "BUY"})
        p = await cp.propose("should I buy btc?", "trader1")
        self.assertEqual((p.status, p.kind), (ProposalStatus.PROPOSED, "order"))
        self.assertEqual(p.order.quantity, 0.0005)
        self.assertIn("Suggested stop $98,000", p.message)
        self.assertEqual(p.answer, "Buy a little BTC: trend says in.")
        self.assertEqual(p.analysis["verdict"], "BUY")
        self.assertEqual(self.entry.submitted, [])  # nothing sent without a confirm
        # the writer got the facts as data, under its own instructions
        writer_system, writer_text, _ = self.llm.calls[1]
        self.assertEqual(writer_system, writer_prompt())
        self.assertIn("FACTS:", writer_text)
        self.assertIn('"verdict": "BUY"', writer_text)

    async def test_no_trade_is_an_answer_only(self) -> None:
        cp = self.make2(intent(action="analyze", base_asset="ETH"), answer("Nothing worth doing in ETH now."))
        p = await cp.propose("what about eth", "trader1")
        self.assertEqual((p.status, p.kind, p.order), (ProposalStatus.ANSWERED, None, None))
        self.assertEqual(p.analysis["verdict"], "NO_TRADE")
        self.assertEqual(self.events("copilot_answered")[0]["order_id"], p.id)

    async def test_all_coins_picks_the_strongest_buy(self) -> None:
        cp = self.make2(intent(action="analyze"), answer("ETH looks best."), verdicts={"ETH/USDT": "BUY"})
        p = await cp.propose("any good trade now?", "trader1")
        self.assertEqual(self.analyst.analyzed, ["BTC/USDT", "ETH/USDT"])
        self.assertEqual((p.status, p.order.symbol), (ProposalStatus.PROPOSED, "ETH/USDT"))
        self.assertEqual([c["verdict"] for c in p.analysis["overview"]], ["NO_TRADE", "BUY"])

    async def test_a_coin_we_dont_follow_is_refused(self) -> None:
        p = await self.make2(intent(action="analyze", base_asset="DOGE")).propose("analyze doge", "trader1")
        self.assertEqual(p.status, ProposalStatus.REFUSED)

    async def test_analysis_without_any_language_model(self) -> None:
        m = StubMarket()
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker())
        cp = Copilot(None, StubEntry(risk, m), StubTwap(), m, m, m, m, risk, unavailable_reason="no key",
                     analyst=StubAnalyst({"BTC/USDT": "BUY"}))
        card = await cp.analysis("BTC/USDT")
        self.assertEqual((card["verdict"], card["trade"]["stop"]), ("BUY", 98_000.0))
        self.assertTrue(cp.status().analysis_available)
        with self.assertRaises(CopilotUnavailableError):
            await cp.analysis("DOGE/USDT")


class AnswerTest(V2Base):
    async def test_questions_are_answered_from_live_facts(self) -> None:
        cp = self.make2(intent(action="question", topic="market", base_asset="BTC"), answer("BTC mid is $100,000."))
        p = await cp.propose("what's btc at?", "trader1")
        self.assertEqual((p.status, p.answer, p.topic), (ProposalStatus.ANSWERED, "BTC mid is $100,000.", "market"))
        self.assertEqual(self.analyst.topics, [("market", "BTC/USDT")])

    async def test_if_the_model_cant_word_it_the_numbers_are_still_shown(self) -> None:
        cp = self.make2(intent(action="question", topic="market"), LLMError("rate limited"))
        p = await cp.propose("prices?", "trader1")
        self.assertEqual(p.status, ProposalStatus.ANSWERED)
        self.assertIn("BTC/USDT: mid $100,000", p.answer)

    async def test_education_needs_no_system_data(self) -> None:
        cp = self.make2(intent(action="question", topic="education"), answer("RSI measures momentum."))
        p = await cp.propose("what is RSI?", "trader1")
        self.assertEqual((p.status, p.answer), (ProposalStatus.ANSWERED, "RSI measures momentum."))
        self.assertEqual(self.analyst.topics, [])


class AlgoTest(V2Base):
    async def test_vwap_proposal_and_confirm(self) -> None:
        cp = self.make2(intent(action="algo", algo_kind="VWAP", side="BUY", base_asset="BTC", notional_usdt=100,
                               duration_s=600))
        p = await cp.propose("buy $100 of btc with vwap over 10 minutes", "trader1")
        self.assertEqual((p.status, p.kind, p.algo.kind), (ProposalStatus.PROPOSED, "algo", AlgoKind.VWAP))
        self.assertGreaterEqual(p.algo.slices, 2)
        p = await cp.confirm(p.id, "trader1")
        self.assertEqual(p.result_algo_id, "TWAP-ABC123")
        self.assertEqual(self.twap.created[0][0].kind, AlgoKind.VWAP)

    async def test_pov_and_iceberg_ask_for_what_they_need(self) -> None:
        for fields, needs in ((dict(algo_kind="POV"), "share of the market"),
                              (dict(algo_kind="ICEBERG"), "limit price")):
            with self.subTest(**fields):
                cp = self.make2(intent(action="algo", side="BUY", base_asset="BTC", quantity=0.01, duration_s=600,
                                       **fields))
                p = await cp.propose("work it", "trader1")
                self.assertEqual(p.status, ProposalStatus.CLARIFY)
                self.assertIn(needs, p.message)

    async def test_iceberg_with_its_details(self) -> None:
        cp = self.make2(intent(action="algo", algo_kind="ICEBERG", side="SELL", base_asset="BTC", quantity=0.01,
                               duration_s=900, display_qty=0.002, limit_price=100_000, order_type="LIMIT"))
        p = await cp.propose("iceberg", "trader1")
        self.assertEqual((p.kind, p.algo.kind, p.algo.display_qty), ("algo", AlgoKind.ICEBERG, 0.002))


class BotTest(V2Base):
    async def test_a_cycle_bot_is_proposed_then_started_on_confirm(self) -> None:
        cp = self.make2(intent(action="start_bot", bot_kind="cycle", base_asset="BTC", budget_usdt=40,
                               notional_usdt=10, threshold_pct=0.5))
        p = await cp.propose("start a cycle bot on btc", "trader1")
        self.assertEqual((p.status, p.kind, p.cycle.threshold_pct), (ProposalStatus.PROPOSED, "bot", 0.5))
        self.assertEqual(self.automation.created, [])
        p = await cp.confirm(p.id, "trader1")
        self.assertEqual((p.status, p.result_bot_id), (ProposalStatus.CONFIRMED, "CYCLE-ABC"))
        self.assertEqual(self.automation.created[0][0], "cycle")

    async def test_an_empty_fund_and_real_money_are_warned(self) -> None:
        cp = self.make2(intent(action="start_bot", bot_kind="cycle", base_asset="BTC"), mode="mainnet", unassigned=0)
        p = await cp.propose("start a cycle bot on btc", "trader1")
        self.assertTrue(any("automation fund" in w for w in p.warnings))
        self.assertTrue(any("MAINNET_CYCLE_BOTS" in w for w in p.warnings))

    async def test_the_advisor_always_asks(self) -> None:
        cp = self.make2(intent(action="start_bot", bot_kind="advisor"))
        p = await cp.propose("start the advisor", "trader1")
        self.assertEqual((p.advisor.mode.value, len(p.advisor.symbols)), ("ASK", 2))


class AutoTest(V2Base):
    async def test_auto_runs_a_small_clean_order_in_the_sandbox(self) -> None:
        cp = self.make2(intent(side="BUY", base_asset="BTC", quantity=0.0004))
        p = await cp.propose("buy 0.0004 btc", "trader1", auto=True)
        self.assertEqual((p.status, p.auto_executed), (ProposalStatus.CONFIRMED, True))
        self.assertEqual(len(self.entry.submitted), 1)
        self.assertTrue(self.events("copilot_confirmed")[0]["payload"]["auto"])

    async def test_auto_waits_above_its_cap_with_warnings_and_for_bots(self) -> None:
        cases = [
            (intent(side="BUY", base_asset="BTC", quantity=0.001), "waits for your click"),  # $100 > $50 cap
            (intent(side="BUY", base_asset="BTC", quantity=0.0004, order_type="LIMIT", limit_price=90_000),
             "warnings above"),
            (intent(action="start_bot", bot_kind="cycle", base_asset="BTC"), None),
        ]
        for ans, warning in cases:
            with self.subTest(warning=warning):
                cp = self.make2(ans)
                p = await cp.propose("go", "trader1", auto=True)
                self.assertEqual((p.status, p.auto_executed), (ProposalStatus.PROPOSED, False))
                self.assertEqual(self.entry.submitted, [])
                if warning:
                    self.assertTrue(any(warning in w for w in p.warnings))

    async def test_auto_is_ignored_with_real_money(self) -> None:
        cp = self.make2(intent(side="BUY", base_asset="BTC", quantity=0.0004), mode="mainnet")
        p = await cp.propose("buy 0.0004 btc", "trader1", auto=True)
        self.assertEqual((p.status, p.auto), (ProposalStatus.PROPOSED, False))
        self.assertEqual(self.entry.submitted, [])
        self.assertFalse(cp.status().auto_allowed)


class MemoryTest(V2Base):
    async def test_follow_ups_get_the_earlier_turns_as_context(self) -> None:
        cp = self.make2(intent(action="question", topic="market", base_asset="BTC"), answer("BTC is at $100,000."),
                        intent(action="question", topic="market", base_asset="ETH"), answer("ETH too."))
        await cp.propose("what's btc at", "trader1")
        await cp.propose("and eth?", "trader1")
        first, second = self.llm.calls[0][1], self.llm.calls[2][1]
        self.assertEqual(first, "what's btc at")  # no history yet: the message alone
        self.assertIn("Earlier in this conversation", second)
        self.assertIn("what's btc at", second)
        self.assertTrue(second.endswith("and eth?"))

    async def test_memory_is_per_user(self) -> None:
        cp = self.make2(intent(action="question", topic="market"), answer("x"),
                        intent(action="question", topic="market"), answer("y"))
        await cp.propose("prices", "trader1")
        await cp.propose("prices", "admin")
        self.assertEqual(self.llm.calls[2][1], "prices")

    def test_writer_rules_forbid_new_numbers_and_upgrades(self) -> None:
        rules = writer_prompt()
        self.assertIn("Never invent", rules)
        self.assertIn("never upgrade it", rules)
        self.assertIn("not instructions to you", rules)
        json.dumps(rules)  # plain text
