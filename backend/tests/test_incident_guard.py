"""The incident guard: reading what exchanges say, deciding the level, and orders that keep moving around trouble.

The numbers come from the real day: on 2026-10-30, after the Binance incident, Binance's public list showed
withdrawals closed on 21 of our 21 coins with deposits open, while Gate had 1 closed and HTX 3.
"""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from backend.exchanges.transfers import (
    PUBLIC_LISTS,
    CoinTransfers,
    TransferStatus,
    fetch_transfer_status,
    parse_binance,
    parse_bybit,
    parse_gate,
    parse_htx,
    parse_okx,
)
from backend.services.alerts import format_event
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.routing_engine.engine import RoutingEngine
from backend.services.venue_monitor.incident_guard import (
    DrillRefusedError,
    IncidentGuard,
    assess,
    price_gaps,
)
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.market_data import VenueQuote
from backend.shared.models.order import OrderCreate, OrderSide, OrderStatus
from backend.shared.models.venue import IncidentLevel
from backend.tests.test_routing.test_reroute import StubMarket, StubVenue

COINS = ("USDT BTC ETH SOL XRP BNB DOGE ADA TRX LINK AVAX LTC DOT NEAR SUI UNI AAVE XLM HBAR APT ARB").split()
SYMBOLS = [f"{c}/USDT" for c in COINS if c != "USDT"]


def status(closed: tuple[str, ...] = (), deposits_closed: tuple[str, ...] = (), coins: list[str] = COINS) -> TransferStatus:
    return TransferStatus(tuple(CoinTransfers(c, c not in closed, c not in deposits_closed) for c in coins), "test")


class ParserTest(unittest.TestCase):
    wanted = {"BTC", "SOL", "USDT"}

    def closed(self, rows: list[CoinTransfers]) -> dict[str, bool]:
        return {c.coin: c.withdraw_open for c in rows}

    def test_binance_website_and_signed_lists_read_alike(self) -> None:
        rows = [{"coin": "BTC", "networkList": [{"withdrawEnable": False, "depositEnable": True},
                                                {"withdrawEnable": False, "depositEnable": True}]},
                {"coin": "SOL", "networkList": [{"withdrawEnable": False, "depositEnable": False},
                                                {"withdrawEnable": True, "depositEnable": True}]},
                {"coin": "DOGE", "networkList": [{"withdrawEnable": True, "depositEnable": True}]}]
        for raw in ({"code": "000000", "data": rows}, rows):
            parsed = {c.coin: c for c in parse_binance(raw, self.wanted)}
            self.assertEqual(set(parsed), {"BTC", "SOL"})  # only coins we watch
            self.assertFalse(parsed["BTC"].withdraw_open)
            self.assertTrue(parsed["BTC"].deposit_open)
            self.assertTrue(parsed["SOL"].withdraw_open)  # one open network is enough: a coin isn't closed by maintenance

    def test_gate_uses_its_chains_and_falls_back_to_the_coin(self) -> None:
        raw = [{"currency": "BTC", "withdraw_disabled": False, "deposit_disabled": False,
                "chains": [{"withdraw_disabled": True, "deposit_disabled": False},
                           {"withdraw_disabled": False, "deposit_disabled": False}]},
               {"currency": "SOL", "withdraw_disabled": True, "deposit_disabled": False, "chains": []}]
        self.assertEqual(self.closed(parse_gate(raw, self.wanted)), {"BTC": True, "SOL": False})

    def test_htx_names_are_lower_case(self) -> None:
        raw = {"code": 200, "data": [
            {"currency": "sol", "chains": [{"withdrawStatus": "prohibited", "depositStatus": "allowed"}]},
            {"currency": "btc", "chains": [{"withdrawStatus": "prohibited", "depositStatus": "allowed"},
                                           {"withdrawStatus": "allowed", "depositStatus": "allowed"}]}]}
        self.assertEqual(self.closed(parse_htx(raw, self.wanted)), {"SOL": False, "BTC": True})

    def test_bybit(self) -> None:
        raw = {"retCode": 0, "result": {"rows": [
            {"coin": "BTC", "chains": [{"chainWithdraw": "1", "chainDeposit": "1"}]},
            {"coin": "SOL", "chains": [{"chainWithdraw": "0", "chainDeposit": "1"}]}]}}
        self.assertEqual(self.closed(parse_bybit(raw, self.wanted)), {"BTC": True, "SOL": False})

    def test_okx_has_one_row_per_chain(self) -> None:
        raw = {"code": "0", "data": [{"ccy": "BTC", "canWd": False, "canDep": True},
                                     {"ccy": "BTC", "canWd": True, "canDep": True},
                                     {"ccy": "SOL", "canWd": False, "canDep": False}]}
        self.assertEqual(self.closed(parse_okx(raw, self.wanted)), {"BTC": True, "SOL": False})

    def test_an_answer_that_says_nothing_is_not_a_closed_coin(self) -> None:
        for parser in (parse_binance, parse_gate, parse_htx, parse_bybit, parse_okx):
            for raw in (None, {}, [], "oops", {"data": None}, {"result": None}):
                self.assertEqual(parser(raw, self.wanted), [], parser.__name__)
        self.assertEqual(parse_binance([{"coin": "BTC", "networkList": []}], self.wanted), [])  # no networks listed


class FetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_public_list_is_read_without_any_key(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": [
                {"coin": "BTC", "networkList": [{"withdrawEnable": False, "depositEnable": True}]}]})

        got = await fetch_transfer_status("binance", ["BTC"], transport=httpx.MockTransport(handler))
        self.assertEqual(got.withdrawals_closed, ["BTC"])
        self.assertIn("public", got.source)
        self.assertEqual(str(seen[0].url), PUBLIC_LISTS["binance"][0])
        self.assertNotIn("authorization", seen[0].headers)

    async def test_an_exchange_that_does_not_say_is_none(self) -> None:
        self.assertIsNone(await fetch_transfer_status("bybit", ["BTC"]))
        self.assertIsNone(await fetch_transfer_status("okx", ["BTC"]))

    async def test_a_real_money_key_uses_the_documented_endpoint(self) -> None:
        class Client:
            async def private_get_v5_asset_coin_query_info(self) -> dict:
                return {"result": {"rows": [{"coin": "BTC", "chains": [{"chainWithdraw": "0", "chainDeposit": "1"}]}]}}

        got = await fetch_transfer_status("bybit", ["BTC"], Client())
        self.assertEqual(got.withdrawals_closed, ["BTC"])
        self.assertIn("account API", got.source)

    async def test_a_refused_key_falls_back_to_the_public_list(self) -> None:
        class Client:
            async def sapi_get_capital_config_getall(self) -> None:
                raise PermissionError("this key may not read that")

        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
            {"coin": "BTC", "networkList": [{"withdrawEnable": True, "depositEnable": True}]}]}))
        got = await fetch_transfer_status("binance", ["BTC"], Client(), transport=transport)
        self.assertEqual(got.withdrawals_closed, [])

    async def test_a_refusal_with_no_other_source_is_raised_not_hidden(self) -> None:
        class Client:
            async def private_get_asset_currencies(self) -> None:
                raise PermissionError("no")

        with self.assertRaises(PermissionError):
            await fetch_transfer_status("okx", ["BTC"], Client())


class PureDecisionTest(unittest.TestCase):
    def test_gap_is_measured_against_the_others_not_against_itself(self) -> None:
        gaps = price_gaps({"BTC/USDT": {"V1": 99.0, "V4": 100.0, "V6": 100.0, "V7": 100.0}})
        self.assertAlmostEqual(gaps["V1"]["BTC/USDT"], -100.0)
        self.assertAlmostEqual(gaps["V4"]["BTC/USDT"], 0.0)

    def test_two_exchanges_are_not_a_market(self) -> None:
        self.assertEqual(price_gaps({"BTC/USDT": {"V1": 99.0, "V4": 100.0}}), {})

    def test_the_real_day_blocks_binance(self) -> None:
        level, reasons = assess(status(closed=tuple(COINS)), 0, 20)
        self.assertEqual(level, IncidentLevel.GUARDED)
        self.assertEqual(reasons, ["withdrawals closed on 21 of 21 coins"])

    def test_an_ordinary_day_stays_quiet(self) -> None:
        self.assertEqual(assess(status(closed=("TRX",)), 0, 20), (IncidentLevel.NORMAL, []))  # Gate, measured
        self.assertEqual(assess(status(closed=("SUI", "HBAR", "ARB")), 0, 20), (IncidentLevel.NORMAL, []))  # HTX
        self.assertEqual(assess(None, 0, 20), (IncidentLevel.NORMAL, []))  # doesn't say: proves nothing

    def test_a_few_coins_can_not_speak_for_an_exchange(self) -> None:
        self.assertEqual(assess(status(closed=("BTC", "ETH"), coins=["BTC", "ETH", "SOL"]), 0, 3)[0], IncidentLevel.NORMAL)

    def test_watch_comes_before_the_block(self) -> None:
        self.assertEqual(assess(status(closed=tuple(COINS[:6])), 0, 20)[0], IncidentLevel.WATCH)
        self.assertEqual(assess(status(closed=("USDT",)), 0, 20), (IncidentLevel.WATCH, ["USDT withdrawals closed"]))
        self.assertEqual(assess(status(deposits_closed=tuple(COINS)), 0, 20)[0], IncidentLevel.WATCH)

    def test_price_gaps_alone(self) -> None:
        self.assertEqual(assess(None, 2, 20)[0], IncidentLevel.NORMAL)  # those coins' story
        self.assertEqual(assess(None, 3, 20)[0], IncidentLevel.WATCH)
        self.assertEqual(assess(None, 10, 20)[0], IncidentLevel.GUARDED)


class StubExchange:
    data_mode = "mainnet"
    can_trade = True

    def __init__(self, venue_id: str, name: str, answer: TransferStatus | None | Exception = None) -> None:
        self.venue_id, self.name, self.answer = venue_id, name, answer

    async def fetch_transfer_status(self, coins: list[str]) -> TransferStatus | None:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class StubRegistry:
    def __init__(self, *exchanges: StubExchange) -> None:
        self.exchanges = {e.venue_id: e for e in exchanges}

    def all(self) -> list[StubExchange]:
        return list(self.exchanges.values())

    def get(self, venue_id: str) -> StubExchange | None:
        return self.exchanges.get(venue_id)


class StubControls:
    def __init__(self) -> None:
        self.out: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def local_blacklist(self) -> set[str]:
        return set(self.out)

    async def blacklist_venue(self, venue_id: str, actor: str) -> None:
        self.out.add(venue_id)
        self.calls.append((venue_id, actor))


class StubCollector:
    def __init__(self) -> None:
        self.mids: dict[str, dict[str, float]] = {}

    def get_all_quotes_for_symbol(self, symbol: str) -> dict[str, VenueQuote]:
        return {v: VenueQuote(venue_id=v, symbol=symbol, bid_price=mid, ask_price=mid, bid_size=1, ask_size=1,
                              last_price=mid, volume=0) for v, mid in self.mids.get(symbol, {}).items()}


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[tuple[str, dict]] = []

        async def on_audit(data: dict) -> None:
            self.events.append(("audit", data))

        async def on_alert(data: dict) -> None:
            self.events.append(("risk_alert", data))

        for channel, handler in (("audit", on_audit), ("risk_alert", on_alert)):
            event_bus.subscribe(channel, handler)
            self.addCleanup(event_bus.unsubscribe, channel, handler)
        self.now = 1_000.0
        self.binance = StubExchange("V1", "Binance", status())
        self.bybit = StubExchange("V4", "Bybit")
        self.gate = StubExchange("V6", "Gate", status(closed=("TRX",)))
        self.controls = StubControls()
        self.collector = StubCollector()
        self.wallet = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(
            as_of=datetime.now(UTC), total_value_usdt=30.0,
            venues=[SimpleNamespace(venue_id="V1", error=None, total_value_usdt=15.0)]))
        self.guard = self.make()

    def make(self, **options) -> IncidentGuard:
        return IncidentGuard(StubRegistry(self.binance, self.bybit, self.gate), self.collector, self.wallet,
                             self.controls, SYMBOLS, clock=lambda: self.now, clear_after_s=300.0, gap_hold_s=30.0,
                             **options)

    def alerts(self) -> list[str]:
        return [d["message"] for kind, d in self.events if kind == "risk_alert"]

    async def incident(self) -> None:
        self.binance.answer = status(closed=tuple(COINS))
        await self.guard.refresh_transfers()
        await self.guard.evaluate()

    def later(self, seconds: float) -> None:
        self.now += seconds

    async def test_a_quiet_day_does_nothing(self) -> None:
        await self.guard.refresh_transfers()
        await self.guard.evaluate()
        self.assertEqual(self.controls.calls, [])
        self.assertEqual(self.alerts(), [])
        state = self.guard.state("V1")
        self.assertEqual((state.level, state.transfers_known, state.coins_checked), (IncidentLevel.NORMAL, True, 21))
        self.assertEqual(self.guard.state("V6").withdrawals_closed, 1)
        self.assertFalse(self.guard.state("V4").transfers_known)  # Bybit doesn't say without a key

    async def test_closed_withdrawals_block_the_exchange_and_tell_the_owner(self) -> None:
        await self.incident()
        self.assertEqual(self.controls.calls, [("V1", "incident-guard")])
        state = self.guard.state("V1")
        self.assertEqual((state.level, state.blocked), (IncidentLevel.GUARDED, True))
        self.assertEqual((state.funds_usdt, state.funds_share_pct), (15.0, 50.0))
        [message] = self.alerts()
        self.assertIn("Binance: withdrawals closed on 21 of 21 coins", message)
        self.assertIn("route through the other exchanges", message)
        self.assertIn("$15.00 (50% of the portfolio)", message)
        self.assertIn("incident guard: withdrawals closed", self.guard.venue_reason("V1"))
        self.assertEqual(self.guard.routing_notes("BTC/USDT"),
                         ["Binance left out: incident guard: withdrawals closed on 21 of 21 coins"])
        audited = [d for kind, d in self.events if kind == "audit" and d["event_type"] == "venue_incident"]
        self.assertEqual(audited[0]["actor"], "incident-guard")

    async def test_the_alert_never_promises_an_exchange_that_is_not_there(self) -> None:
        self.bybit.can_trade = self.gate.can_trade = False
        await self.incident()
        self.assertIn("no other exchange can take them", self.alerts()[0])
        self.assertNotIn("route through", self.alerts()[0])

    async def test_a_sandbox_balance_is_called_a_sandbox_balance(self) -> None:
        await self.incident()
        self.assertIn("Our sandbox balance there", self.alerts()[0])

    async def test_it_blocks_and_alerts_once_per_episode(self) -> None:
        await self.incident()
        for _ in range(5):
            self.later(5)
            await self.guard.evaluate()
        self.assertEqual(len(self.controls.calls), 1)
        self.assertEqual(len(self.alerts()), 1)

    async def test_a_person_lifting_it_is_respected_while_the_episode_lasts(self) -> None:
        await self.incident()
        self.controls.out.clear()  # lifted on the Venues page
        self.later(5)
        await self.guard.evaluate()
        self.assertEqual(len(self.controls.calls), 1)  # not blocked again
        state = self.guard.state("V1")
        self.assertTrue(state.lifted)
        self.assertFalse(state.blocked)
        self.assertIn("a person lifted the block", state.reasons[-1])

    async def test_clearing_does_not_lift_the_block(self) -> None:
        await self.incident()
        self.binance.answer = status()
        await self.guard.refresh_transfers()
        await self.guard.evaluate()
        self.assertEqual(self.guard.state("V1").level, IncidentLevel.GUARDED)  # not yet: a quiet spell first
        self.later(301)
        await self.guard.evaluate()
        self.assertEqual(self.guard.state("V1").level, IncidentLevel.NORMAL)
        self.assertEqual(self.controls.out, {"V1"})  # still out: only a person lets it back
        self.assertIn("stays blocked until a person lifts it", self.alerts()[-1])
        self.assertIn("a person lifts the block", self.guard.venue_reason("V1"))

    async def test_a_new_episode_blocks_again(self) -> None:
        await self.incident()
        self.controls.out.clear()
        self.binance.answer = status()
        await self.guard.refresh_transfers()
        self.later(5)
        await self.guard.evaluate()
        self.later(301)
        await self.guard.evaluate()
        await self.incident()
        self.assertEqual(len(self.controls.calls), 2)

    async def test_a_blacklist_from_before_a_restart_is_adopted_silently(self) -> None:
        self.controls.out = {"V1"}
        await self.incident()
        self.assertEqual(self.controls.calls, [])
        self.assertTrue(self.guard.state("V1").blocked)

    async def test_a_failed_read_is_never_an_incident_and_the_answer_ages_out(self) -> None:
        await self.guard.refresh_transfers()
        self.binance.answer = RuntimeError("the list moved")
        await self.guard.refresh_transfers()
        await self.guard.evaluate()
        self.assertEqual(self.controls.calls, [])
        self.assertTrue(self.guard.state("V1").transfers_known)  # the last answer stands for a while
        self.later(60 * 5 + 1)
        await self.guard.evaluate()
        self.assertFalse(self.guard.state("V1").transfers_known)
        self.assertEqual(self.controls.calls, [])

    async def test_auto_block_off_warns_but_leaves_the_decision_to_a_person(self) -> None:
        self.guard = self.make(auto_block=False)
        await self.incident()
        self.assertEqual(self.controls.calls, [])
        self.assertEqual(self.guard.state("V1").level, IncidentLevel.GUARDED)
        self.assertIn("Automatic blocking is off", self.alerts()[0])

    def set_mids(self, binance: float) -> None:
        self.collector.mids = {s: {"V1": binance, "V4": 100.0, "V6": 100.0, "V7": 100.0} for s in SYMBOLS}

    async def test_a_price_gap_must_last_before_it_counts(self) -> None:
        self.set_mids(binance=98.0)
        self.guard = IncidentGuard(StubRegistry(self.binance, self.bybit, self.gate, StubExchange("V7", "HTX")),
                                   self.collector, self.wallet, self.controls, SYMBOLS, clock=lambda: self.now)
        await self.guard.evaluate()
        self.assertEqual(self.controls.calls, [])  # one bad tick
        self.later(31)
        await self.guard.evaluate()
        self.assertEqual(self.controls.calls, [("V1", "incident-guard")])
        self.assertEqual(self.guard.state("V1").median_gap_bps, -200.0)
        self.assertIn("prices far from the other exchanges on 20 of 20 coins", self.guard.state("V1").reasons[0])

    async def test_a_gap_that_closes_starts_over(self) -> None:
        self.set_mids(binance=98.0)
        self.guard = IncidentGuard(StubRegistry(self.binance, self.bybit, self.gate, StubExchange("V7", "HTX")),
                                   self.collector, self.wallet, self.controls, SYMBOLS, clock=lambda: self.now)
        await self.guard.evaluate()
        self.later(20)
        self.set_mids(binance=100.0)
        await self.guard.evaluate()
        self.later(20)
        self.set_mids(binance=98.0)
        await self.guard.evaluate()
        self.later(20)
        await self.guard.evaluate()
        self.assertEqual(self.controls.calls, [])

    async def test_one_odd_coin_leaves_routing_for_that_coin_alone(self) -> None:
        self.collector.mids = {"SOL/USDT": {"V1": 98.0, "V4": 100.0, "V6": 100.0, "V7": 100.0},
                               "BTC/USDT": {"V1": 100.0, "V4": 100.0, "V6": 100.0, "V7": 100.0}}
        self.guard = IncidentGuard(StubRegistry(self.binance, self.bybit, self.gate, StubExchange("V7", "HTX")),
                                   self.collector, self.wallet, self.controls, SYMBOLS, clock=lambda: self.now)
        await self.guard.evaluate()
        self.assertIsNone(self.guard.blocking_reason("V1", "SOL/USDT"))
        self.later(31)
        await self.guard.evaluate()
        self.assertIn("warning, not a bargain", self.guard.blocking_reason("V1", "SOL/USDT"))
        self.assertIsNone(self.guard.blocking_reason("V1", "BTC/USDT"))
        self.assertEqual(self.controls.calls, [])  # the exchange itself stays in routing

    async def test_a_testnet_book_says_nothing_about_an_exchange(self) -> None:
        self.binance.data_mode = "testnet"
        self.set_mids(binance=98.0)
        self.guard = IncidentGuard(StubRegistry(self.binance, self.bybit, self.gate, StubExchange("V7", "HTX")),
                                   self.collector, self.wallet, self.controls, SYMBOLS, clock=lambda: self.now)
        await self.guard.evaluate()
        self.assertEqual(self.guard.state("V1").coins_compared, 0)

    async def test_the_drill_runs_the_whole_response_and_says_it_is_a_drill(self) -> None:
        state = await self.guard.set_drill("V1", True, "risk_mgr")
        self.assertEqual((state.level, state.blocked, state.drill), (IncidentLevel.GUARDED, True, True))
        self.assertTrue(state.reasons[0].startswith("DRILL: withdrawals closed on 21 of 21 coins"))
        self.assertIn("DRILL", self.alerts()[0])

        state = await self.guard.set_drill("V1", False, "risk_mgr")
        self.assertEqual((state.level, state.drill), (IncidentLevel.NORMAL, False))
        self.assertEqual(self.controls.out, {"V1"})  # as after a real incident: a person lifts it

    async def test_there_is_no_drill_on_real_money(self) -> None:
        self.guard = self.make(real_money=True)
        with self.assertRaises(DrillRefusedError) as caught:
            await self.guard.set_drill("V1", True, "admin")
        self.assertIsInstance(caught.exception, DomainError)
        self.assertEqual(self.controls.calls, [])

    async def test_off_means_off(self) -> None:
        self.guard = self.make(enabled=False)
        await self.guard.start()  # returns at once instead of looping
        self.assertEqual(self.controls.calls, [])


class RoutingAroundTroubleTest(unittest.IsolatedAsyncioTestCase):
    """The real pipeline against stub venues: the order still fills, on the others."""

    async def asyncSetUp(self) -> None:
        self.binance = StubVenue("V1", ask=100.0)  # the best price on the screen
        self.bybit = StubVenue("V4", ask=100.1)
        self.gate = StubVenue("V6", ask=100.2)

        class Market(StubMarket):
            def __init__(self, venues: list[StubVenue]) -> None:
                super().__init__(venues)
                self.out: set[str] = set()

            def get_routable_venues(self) -> list[str]:
                return [v for v in self.venues if v not in self.out]

            def get_excluded_venues(self) -> dict[str, str]:
                return dict.fromkeys(self.out, "BLACKLISTED")

            def local_blacklist(self) -> set[str]:
                return set(self.out)

            async def blacklist_venue(self, venue_id: str, actor: str) -> None:
                self.out.add(venue_id)

            async def compute_nbbo(self, symbol: str):
                nbbo = await super().compute_nbbo(symbol)
                # as the real aggregator does: a blacklisted venue isn't in the NBBO
                quotes = {v: q for v, q in nbbo.venue_quotes.items() if v not in self.out}
                best = min(quotes.values(), key=lambda q: q.ask_price)
                return nbbo.model_copy(update={"venue_quotes": quotes, "best_ask": best.ask_price,
                                               "best_ask_venue": best.venue_id})

        self.market = Market([self.binance, self.bybit, self.gate])
        for venue, answer in ((self.binance, status()), (self.bybit, None), (self.gate, status())):
            venue.data_mode = "mainnet"
            venue.answer = answer

            async def read(coins: list[str], _venue=venue) -> TransferStatus | None:
                return _venue.answer

            venue.fetch_transfer_status = read
        self.orders = OrderManager()
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker())
        self.engine = RoutingEngine(self.market, self.market, self.risk, self.market, self.orders, self.market)
        wallet = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(as_of=None, total_value_usdt=0.0, venues=[]))
        self.collector = StubCollector()
        self.guard = IncidentGuard(self.market, self.collector, wallet, self.market, SYMBOLS)
        self.engine.set_guard(self.guard)

    async def buy(self):
        order = await self.orders.create_order(OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0),
                                               submitted_by="trader1")
        await self.engine.route_order(order)
        return self.orders.get_order(order.id)

    async def test_the_best_price_goes_to_binance_on_a_quiet_day(self) -> None:
        await self.guard.refresh_transfers()
        await self.guard.evaluate()
        order = await self.buy()
        self.assertEqual([c.venue_id for c in order.child_orders], ["V1"])

    async def test_the_order_fills_elsewhere_and_the_proof_says_why(self) -> None:
        self.binance.answer = status(closed=tuple(COINS))
        await self.guard.refresh_transfers()
        await self.guard.evaluate()

        order = await self.buy()

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(self.binance.sent, [])  # nothing went to the exchange in trouble, the best price or not
        self.assertEqual([c.venue_id for c in order.child_orders], ["V4"])
        self.assertIn("withdrawals closed on 21 of 21 coins", self.engine.excluded_venues("BTC/USDT")["V1"])
        self.assertTrue(order.routing_decision["notes"][0].startswith("Venue V1 left out: incident guard"))

    async def test_the_proof_shows_the_better_price_that_was_passed_up(self) -> None:
        self.collector.mids = {"BTC/USDT": {"V1": 100.0}}  # Binance still quotes the best ask
        self.binance.answer = status(closed=tuple(COINS))
        await self.guard.refresh_transfers()
        await self.guard.evaluate()

        order = await self.buy()

        self.assertEqual(self.binance.sent, [])  # shown in the proof, never a candidate
        self.assertEqual([c.venue_id for c in order.child_orders], ["V4"])
        row = next(c for c in order.routing_decision["candidates"] if c["venue_id"] == "V1")
        self.assertIn("incident guard", row["excluded_reason"])
        self.assertFalse(row["is_winner"])
        notes = order.routing_decision["notes"]
        passed_up = [n for n in notes if "bps better" in n]
        self.assertEqual(len(passed_up), 1)
        self.assertIn("V1", passed_up[0])
        self.assertIn("but was skipped: incident guard: withdrawals closed on 21 of 21 coins", passed_up[0])
        self.assertFalse(any("left out" in n for n in notes))  # said once


class AlertWordingTest(unittest.TestCase):
    def test_an_incident_stands_out_on_the_phone(self) -> None:
        text = format_event("risk_alert", {"type": "VENUE_INCIDENT", "message": "Binance: withdrawals closed"}, False)
        self.assertEqual(text, "🚨 Venue Incident: Binance: withdrawals closed")

    def test_a_message_that_names_itself_is_not_doubled(self) -> None:
        text = format_event("risk_alert", {"type": "CONCENTRATION", "message": "Concentration: BTC is 70%"}, False)
        self.assertEqual(text, "⚠️ Concentration: BTC is 70%")


if __name__ == "__main__":
    unittest.main()
