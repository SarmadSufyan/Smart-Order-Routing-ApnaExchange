"""Real money (Phase D): the .env ceilings cap every risk limit, and the settings refuse an unsafe start."""

import unittest

from pydantic import ValidationError

from backend.gateway.config import Settings
from backend.services.risk_engine.ceilings import apply_ceilings, ceiling_breaches
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.shared.exceptions import RiskLimitAboveCeilingError
from backend.shared.models.order import Order, OrderSide, OrderType
from backend.shared.models.risk import RiskCeilings, RiskCheckResult, RiskLimits

CAPS = RiskCeilings(max_order_notional=25, max_algo_notional_usdt=100, max_notional_exposure=150,
                    max_trading_loss_usdt=10)


class CeilingMathTest(unittest.TestCase):
    def test_every_capped_limit_is_lowered_and_the_rest_untouched(self) -> None:
        capped = apply_ceilings(RiskLimits(), CAPS)  # the testnet-sized defaults
        self.assertEqual((capped.max_order_notional, capped.max_algo_notional_usdt, capped.max_notional_exposure,
                          capped.max_trading_loss_usdt), (25, 100, 150, 10))
        self.assertEqual(capped.max_orders_per_second, RiskLimits().max_orders_per_second)
        tighter = RiskLimits(max_order_notional=12, max_trading_loss_usdt=5)
        self.assertEqual(apply_ceilings(tighter, CAPS).max_order_notional, 12)  # tighter stays tighter
        self.assertEqual(apply_ceilings(tighter, CAPS).max_trading_loss_usdt, 5)
        self.assertEqual(apply_ceilings(RiskLimits(), None), RiskLimits())  # testnet: no ceilings

    def test_a_loss_limit_of_zero_means_off_and_is_capped_too(self) -> None:
        self.assertEqual(apply_ceilings(RiskLimits(max_trading_loss_usdt=0), CAPS).max_trading_loss_usdt, 10)
        breaches = ceiling_breaches(RiskLimits(max_trading_loss_usdt=0, max_order_notional=20,
                                               max_algo_notional_usdt=50, max_notional_exposure=100), CAPS)
        self.assertEqual(len(breaches), 1)
        self.assertIn("trading-loss limit off", breaches[0])
        self.assertIn("MAINNET_MAX_TRADING_LOSS_USDT", breaches[0])

    def test_breaches_name_the_setting_to_change(self) -> None:
        breaches = ceiling_breaches(RiskLimits(), CAPS)
        self.assertEqual(len(breaches), 4)
        self.assertTrue(any("MAINNET_MAX_ORDER_USDT" in b and "$10,000.00" in b for b in breaches))
        self.assertEqual(ceiling_breaches(apply_ceilings(RiskLimits(), CAPS), CAPS), [])
        self.assertEqual(ceiling_breaches(RiskLimits(), None), [])


class EngineCeilingTest(unittest.IsolatedAsyncioTestCase):
    def engine(self, caps: RiskCeilings | None = CAPS) -> PreTradeRiskEngine:
        return PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(), ceilings=caps)

    async def test_limits_in_force_never_exceed_the_ceilings(self) -> None:
        risk = self.engine()
        self.assertEqual(risk.limits.max_order_notional, 25)
        risk.limits = RiskLimits(max_order_notional=5_000)  # e.g. limits saved by an earlier testnet-sized run
        self.assertEqual(risk.limits.max_order_notional, 25)
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.0004, order_type=OrderType.MARKET)
        detail = await risk.check_order(order, mid_price=100_000)  # $40 > $25
        self.assertEqual(detail.result, RiskCheckResult.REJECTED_ORDER_NOTIONAL_LIMIT)
        ok = await risk.check_order(order.model_copy(update={"quantity": 0.0002}), mid_price=100_000)
        self.assertEqual(ok.result, RiskCheckResult.APPROVED)

    async def test_a_change_past_a_ceiling_is_refused_and_a_tighter_one_accepted(self) -> None:
        risk = self.engine()
        with self.assertRaises(RiskLimitAboveCeilingError) as refused:
            await risk.update_limits(RiskLimits(max_order_notional=1_000, max_algo_notional_usdt=100,
                                                max_notional_exposure=150, max_trading_loss_usdt=10), "admin")
        self.assertIn("restart", refused.exception.message)
        self.assertEqual(risk.limits.max_order_notional, 25)
        await risk.update_limits(RiskLimits(max_order_notional=15, max_algo_notional_usdt=60,
                                            max_notional_exposure=100, max_trading_loss_usdt=5), "admin")
        self.assertEqual(risk.limits.max_order_notional, 15)

    async def test_testnet_has_no_ceilings(self) -> None:
        risk = self.engine(None)
        self.assertIsNone(risk.ceilings)
        await risk.update_limits(RiskLimits(max_order_notional=50_000), "admin")
        self.assertEqual(risk.limits.max_order_notional, 50_000)


class RealMoneySettingsTest(unittest.TestCase):
    def test_every_exchange_has_mainnet_keys_read_only_on_mainnet(self) -> None:
        keys = {"bybit_mainnet_api_key": "K", "bybit_mainnet_api_secret": "S", "okx_mainnet_api_key": "K",
                "okx_mainnet_api_secret": "S", "okx_mainnet_api_passphrase": "P", "gate_mainnet_api_key": "K",
                "gate_mainnet_api_secret": "S"}
        test = Settings(_env_file=None, **keys)
        self.assertIsNone(test.exchange_credentials("bybit"))
        real = Settings(_env_file=None, exchange_mode="mainnet", allow_mainnet=True, **keys)
        self.assertEqual(real.exchange_credentials("bybit"), {"apiKey": "K", "secret": "S"})
        self.assertEqual(real.exchange_credentials("okx"), {"apiKey": "K", "secret": "S", "password": "P"})
        self.assertEqual(real.exchange_credentials("gate"), {"apiKey": "K", "secret": "S"})
        self.assertTrue(real.is_real_money)
        self.assertFalse(real.mainnet_cycle_bots)  # off until the owner turns them on

    def test_a_ceiling_of_zero_stops_a_real_money_start(self) -> None:
        with self.assertRaises(ValidationError) as refused:
            Settings(_env_file=None, exchange_mode="mainnet", allow_mainnet=True, mainnet_max_trading_loss_usdt=0)
        self.assertIn("MAINNET_MAX_TRADING_LOSS_USDT", str(refused.exception))
        Settings(_env_file=None, mainnet_max_trading_loss_usdt=0)  # testnet doesn't use them


if __name__ == "__main__":
    unittest.main()
