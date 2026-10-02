"""Risk batch (Phase 6): pure checks, pre-flight projection, stress test, process pool."""

import math
import unittest

from backend.services.risk_engine.batch import (
    RiskBatchRunner,
    StressParams,
    evaluate_jobs,
    plan_jobs,
    split_round_robin,
    stress_position,
)
from backend.services.risk_engine.checks import OrderSpec, RiskContext, evaluate_order
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.risk_engine.preflight import PreflightService, PreflightSymbolError
from backend.services.risk_engine.volatility import MIN_RETURNS, sigma_per_minute
from backend.shared.models.market_data import NBBO
from backend.shared.models.order import Order, OrderSide, OrderType
from backend.shared.models.risk import PreflightOrder, PreflightRequest, RiskCheckResult, RiskLimits

BTC = "BTC/USDT"


def ctx(limits: RiskLimits | None = None, **kw) -> RiskContext:
    base = {"limits": limits or RiskLimits(), "kill_switch_active": False, "net_position": 0.0,
            "total_notional": 0.0, "orders_last_second": None}
    return RiskContext(**{**base, **kw})


def plan(orders, limits=None, positions=None, notionals=None, stress=None, kill=False, mids=None):
    return plan_jobs(orders, mids or {BTC: 100.0, "ETH/USDT": 10.0}, limits=limits or RiskLimits(),
                     kill_switch_active=kill, positions=positions or {}, notionals=notionals or {},
                     stress=stress)


class PureChecksTest(unittest.TestCase):
    def test_each_check_rejects_with_its_reason(self) -> None:
        lim = RiskLimits(max_order_size=5, max_order_notional=300, max_position_per_symbol=8,
                         max_notional_exposure=1_000, max_orders_per_second=2, restricted_symbols=["XRP/USDT"])
        cases = [
            (ctx(lim, kill_switch_active=True), OrderSpec(BTC, "BUY", 1), RiskCheckResult.REJECTED_KILL_SWITCH),
            (ctx(lim), OrderSpec(BTC, "BUY", 6), RiskCheckResult.REJECTED_SIZE_LIMIT),
            (ctx(lim), OrderSpec(BTC, "BUY", 4), RiskCheckResult.REJECTED_ORDER_NOTIONAL_LIMIT),  # $400
            (ctx(lim, orders_last_second=2), OrderSpec(BTC, "BUY", 1), RiskCheckResult.REJECTED_RATE_LIMIT),
            (ctx(lim, net_position=7.5), OrderSpec(BTC, "BUY", 1), RiskCheckResult.REJECTED_POSITION_LIMIT),
            (ctx(lim, total_notional=950), OrderSpec(BTC, "BUY", 1), RiskCheckResult.REJECTED_NOTIONAL_LIMIT),
            (ctx(lim), OrderSpec("XRP/USDT", "BUY", 1), RiskCheckResult.REJECTED_SYMBOL_RESTRICTED),
        ]
        for c, order, expected in cases:
            with self.subTest(expected=expected):
                out = evaluate_order(c, order, 100.0)
                self.assertEqual(out.result, expected)
                self.assertEqual(len(out.failed), 1)
                self.assertTrue(out.message)

    def test_a_sell_reduces_the_projected_position(self) -> None:
        out = evaluate_order(ctx(RiskLimits(max_position_per_symbol=8), net_position=8.0),
                             OrderSpec(BTC, "SELL", 3), 100.0)
        self.assertTrue(out.approved)

    def test_rate_check_is_skipped_when_there_is_no_count(self) -> None:
        out = evaluate_order(ctx(), OrderSpec(BTC, "BUY", 1), 100.0)
        self.assertTrue(out.approved)
        self.assertNotIn("rate_limit", out.passed)
        out = evaluate_order(ctx(orders_last_second=0), OrderSpec(BTC, "BUY", 1), 100.0)
        self.assertIn("rate_limit", out.passed)
        self.assertEqual(len(out.passed), 7)


class PlanTest(unittest.TestCase):
    def test_each_order_meets_the_position_left_by_the_earlier_ones(self) -> None:
        orders = [OrderSpec(BTC, "BUY", 1.0)] * 4
        jobs = plan(orders, limits=RiskLimits(max_position_per_symbol=2.5), positions={BTC: 0.5})
        self.assertEqual([j.ctx.net_position for j in jobs], [0.5, 1.5, 2.5, 3.5])
        self.assertEqual([j.projected_position for j in jobs], [1.5, 2.5, 3.5, 4.5])
        results = [i.outcome.result for i in evaluate_jobs(jobs)]
        self.assertEqual(results[:2], [RiskCheckResult.APPROVED] * 2)
        self.assertEqual(results[2], RiskCheckResult.REJECTED_POSITION_LIMIT)

    def test_exposure_is_projected_per_symbol(self) -> None:
        orders = [OrderSpec(BTC, "BUY", 2.0), OrderSpec("ETH/USDT", "BUY", 5.0)]
        jobs = plan(orders, notionals={BTC: 50.0})
        self.assertEqual(jobs[0].ctx.total_notional, 50.0)
        self.assertEqual(jobs[1].ctx.total_notional, 200.0)  # BTC now |2 x $100|, replacing the old $50

    def test_symbols_are_projected_independently(self) -> None:
        jobs = plan([OrderSpec(BTC, "BUY", 1), OrderSpec("ETH/USDT", "SELL", 3), OrderSpec(BTC, "SELL", 4)])
        self.assertEqual([j.projected_position for j in jobs], [1.0, -3.0, -3.0])

    def test_round_robin_deals_every_job_exactly_once(self) -> None:
        jobs = plan([OrderSpec(BTC, "BUY", 0.1)] * 10)
        chunks = split_round_robin(jobs, 4)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(sorted(j.index for c in chunks for j in c), list(range(10)))
        self.assertEqual(len(split_round_robin(jobs[:2], 4)), 2)  # never an empty chunk


class StressTest(unittest.TestCase):
    params = StressParams(sigma_per_min=0.001, horizon_min=30, paths=2_000)

    def test_no_position_no_risk(self) -> None:
        r = stress_position(0.0, 100.0, self.params, seed=1)
        self.assertEqual((r.var_usdt, r.es_usdt, r.paths), (0.0, 0.0, 0))

    def test_same_seed_same_answer(self) -> None:
        self.assertEqual(stress_position(2.0, 100.0, self.params, 7), stress_position(2.0, 100.0, self.params, 7))

    def test_loss_scales_with_the_position_and_tail_average_exceeds_var(self) -> None:
        one = stress_position(1.0, 100.0, self.params, 3)
        two = stress_position(2.0, 100.0, self.params, 3)
        self.assertAlmostEqual(two.var_usdt, 2 * one.var_usdt, places=3)
        self.assertGreater(one.var_usdt, 0)
        self.assertGreaterEqual(one.es_usdt, one.var_usdt)

    def test_worst_point_is_in_the_right_ballpark(self) -> None:
        # 99% of a normal move over 30 min is ~2.33 sigma*sqrt(30) ≈ 1.27% of $100;
        # the worst point WITHIN the window is a bit deeper than the end point.
        r = stress_position(1.0, 100.0, StressParams(0.001, 30, 5_000), 11)
        end_point = 100.0 * 2.326 * 0.001 * math.sqrt(30)
        self.assertGreater(r.var_usdt, 0.9 * end_point)
        self.assertLess(r.var_usdt, 1.5 * end_point)

    def test_a_short_is_hurt_by_rises(self) -> None:
        r = stress_position(-1.0, 100.0, self.params, 5)
        self.assertGreater(r.var_usdt, 0)
        self.assertEqual(r.position, -1.0)


class RunnerTest(unittest.IsolatedAsyncioTestCase):
    def test_auto_keeps_cheap_or_small_batches_in_process(self) -> None:
        runner = RiskBatchRunner(workers=2, pool_min_orders=4)
        stress = {BTC: StressParams(0.001, 5, 50)}
        self.assertEqual(runner.choose(plan([OrderSpec(BTC, "BUY", 0.1)] * 50)), "serial")  # checks only
        self.assertEqual(runner.choose(plan([OrderSpec(BTC, "BUY", 0.1)] * 3, stress=stress)), "serial")
        self.assertEqual(runner.choose(plan([OrderSpec(BTC, "BUY", 0.1)] * 4, stress=stress)), "pool")
        self.assertEqual(runner.choose(plan([OrderSpec(BTC, "BUY", 0.1)] * 4), mode="pool"), "pool")

    async def test_pool_and_single_process_give_identical_answers(self) -> None:
        runner = RiskBatchRunner(workers=2, pool_min_orders=2)
        self.addAsyncCleanup(runner.stop)
        stress = {BTC: StressParams(0.001, 10, 300), "ETH/USDT": StressParams(0.002, 10, 300)}
        orders = [OrderSpec(BTC if i % 3 else "ETH/USDT", "BUY" if i % 2 else "SELL", 0.5 + i) for i in range(9)]
        jobs = plan(orders, limits=RiskLimits(max_position_per_symbol=6), stress=stress)  # BTC reaches 7.5
        serial = await runner.run(jobs, mode="serial")
        pooled = await runner.run(jobs, mode="pool")
        self.assertEqual(serial.executor, "in-process")
        self.assertEqual(pooled.executor, "pool (2 processes)")
        self.assertEqual([i.index for i in pooled.items], list(range(9)))  # back in order
        for a, b in zip(serial.items, pooled.items, strict=True):
            self.assertEqual(a.outcome.result, b.outcome.result)
            self.assertEqual(a.stress, b.stress)  # bit-identical: per-order seeds
        self.assertFalse(serial.all_approved)  # the position limit bites part-way through
        self.assertEqual(serial.first_rejection.outcome.result, RiskCheckResult.REJECTED_POSITION_LIMIT)


class EnginePreflightTest(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_uses_live_positions_and_does_not_use_up_the_rate_limit(self) -> None:
        tracker = PositionTracker()
        tracker.update_position(BTC, OrderSide.BUY, 2.0, 100.0)
        risk = PreTradeRiskEngine(KillSwitch(), tracker, RiskLimits(max_position_per_symbol=4,
                                                                    max_orders_per_second=1))
        report = await risk.preflight([OrderSpec(BTC, "BUY", 1.0)] * 3, {BTC: 100.0})
        self.assertEqual([i.outcome.approved for i in report.items], [True, True, False])
        self.assertEqual(report.items[0].outcome.passed[-1], "symbol_restriction")
        # 3 pre-flighted orders, limit 1/s: a real order still gets through.
        detail = await risk.check_order(Order(symbol=BTC, side=OrderSide.BUY, quantity=1.0,
                                              order_type=OrderType.MARKET), mid_price=100.0)
        self.assertEqual(detail.result, RiskCheckResult.APPROVED)

    async def test_kill_switch_fails_every_order_in_a_preflight(self) -> None:
        ks = KillSwitch()
        await ks.activate("test", "tester")
        risk = PreTradeRiskEngine(ks, PositionTracker())
        report = await risk.preflight([OrderSpec(BTC, "BUY", 0.1)] * 2, {BTC: 100.0})
        self.assertEqual({i.outcome.result for i in report.items}, {RiskCheckResult.REJECTED_KILL_SWITCH})


class StubAggregator:
    async def compute_nbbo(self, symbol: str) -> NBBO | None:
        if symbol != BTC:
            return None
        return NBBO(symbol=symbol, best_bid=99.0, best_ask=101.0, best_bid_venue="V1", best_ask_venue="V1",
                    best_bid_size=1, best_ask_size=1, spread=2.0)


class StubVolatility:
    def __init__(self, sigma: float | None) -> None:
        self._sigma = sigma

    def sigma(self, symbol: str) -> float | None:
        return self._sigma

    def samples(self, symbol: str) -> int:
        return 3


class PreflightServiceTest(unittest.IsolatedAsyncioTestCase):
    def service(self, sigma: float | None = 0.001) -> PreflightService:
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), batch_runner=RiskBatchRunner(1, 1_000))
        return PreflightService(risk, StubAggregator(), [BTC, "ETH/USDT"], StubVolatility(sigma),
                                stress_paths=200, stress_horizon_min=5)

    async def test_answers_per_order_with_a_stress_test(self) -> None:
        req = PreflightRequest(orders=[PreflightOrder(symbol="btc/usdt", side="BUY", quantity=0.5)], stress=True)
        res = await self.service().run(req, actor="admin")
        self.assertTrue(res.all_approved)
        self.assertEqual(res.executor, "in-process")  # one order: not worth a process
        r = res.results[0]
        self.assertEqual((r.symbol, r.mid_price, r.position_after), (BTC, 100.0, 0.5))
        self.assertGreater(r.stress.var_99_usdt, 0)
        self.assertEqual(r.stress.paths, 200)

    async def test_says_why_a_stress_test_or_price_is_missing(self) -> None:
        req = PreflightRequest(orders=[PreflightOrder(symbol=BTC, side="BUY", quantity=0.5),
                                       PreflightOrder(symbol="ETH/USDT", side="SELL", quantity=1)], stress=True)
        res = await self.service(sigma=None).run(req, actor="admin")
        self.assertIsNone(res.results[0].stress)
        self.assertTrue(any("No live price for ETH/USDT" in n for n in res.notes))
        self.assertTrue(any("No stress test for BTC/USDT yet" in n for n in res.notes))

    async def test_unsupported_symbol_is_an_error_not_a_guess(self) -> None:
        req = PreflightRequest(orders=[PreflightOrder(symbol="DOGE/USDT", side="BUY", quantity=1)])
        with self.assertRaises(PreflightSymbolError):
            await self.service().run(req, actor="admin")


class VolatilityTest(unittest.TestCase):
    def test_needs_a_minute_of_history(self) -> None:
        self.assertIsNone(sigma_per_minute([100.0] * MIN_RETURNS, 5.0))  # 11 returns
        self.assertEqual(sigma_per_minute([100.0] * (MIN_RETURNS + 1), 5.0), 0.0)  # quiet market = 0

    def test_scales_sample_volatility_to_one_minute(self) -> None:
        # Alternating +r / -r log returns: sample stdev ≈ r; per minute x sqrt(60/5).
        r = 0.001
        mids = [100.0 * math.exp(r if i % 2 else 0.0) for i in range(41)]
        sigma = sigma_per_minute(mids, 5.0)
        stdev = r * math.sqrt(40 / 39)  # sample (n-1) stdev of 40 returns of ±r
        self.assertAlmostEqual(sigma, stdev * math.sqrt(12), places=9)


if __name__ == "__main__":
    unittest.main()
