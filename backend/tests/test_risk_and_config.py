import os
import tempfile
import unittest

from pydantic import ValidationError

from backend.gateway.config import Settings
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.shared.models.order import Order, OrderSide, OrderType
from backend.shared.models.risk import RiskCheckResult, RiskLimits


class RaftSecretTest(unittest.TestCase):
    def test_raft_needs_a_long_enough_secret_but_loading_settings_never_fails(self) -> None:
        s = Settings(_env_file=None, raft_enabled=True)  # loads fine...
        self.assertIn("RAFT_SHARED_SECRET", s.raft_secret_problem())  # ...but RAFT won't start
        self.assertIsNotNone(Settings(_env_file=None, raft_shared_secret="short").raft_secret_problem())
        self.assertIsNone(Settings(_env_file=None, raft_shared_secret="x" * 32).raft_secret_problem())
        self.assertNotIn("x" * 32, repr(Settings(_env_file=None, raft_shared_secret="x" * 32)))  # SecretStr


class SettingsTest(unittest.TestCase):
    def test_mainnet_requires_explicit_allow(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, exchange_mode="mainnet")
        Settings(_env_file=None, exchange_mode="mainnet", allow_mainnet=True)  # ok

    def test_unknown_env_keys_do_not_crash(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("VITE_API_URL=http://x\nPOSTGRES_URL=postgres://x\nRAFT_NODE_ID=1\n")
        try:
            Settings(_env_file=f.name)
        finally:
            os.unlink(f.name)

    def test_testnet_never_reads_mainnet_keys(self) -> None:
        s = Settings(_env_file=None, exchange_mode="testnet",
                     binance_mainnet_api_key="MAIN", binance_mainnet_api_secret="MAIN_S")
        self.assertIsNone(s.exchange_credentials("binance"))

    def test_secrets_are_masked(self) -> None:
        s = Settings(_env_file=None, binance_testnet_api_key="k-123", binance_testnet_api_secret="s-456")
        self.assertNotIn("s-456", repr(s))
        self.assertEqual(s.exchange_credentials("binance"), {"apiKey": "k-123", "secret": "s-456"})

    def test_okx_passphrase_becomes_ccxt_password(self) -> None:
        s = Settings(_env_file=None, okx_testnet_api_key="k", okx_testnet_api_secret="s",
                     okx_testnet_api_passphrase="p")
        self.assertEqual(s.exchange_credentials("okx"), {"apiKey": "k", "secret": "s", "password": "p"})

    def test_default_venues_are_the_executable_ones(self) -> None:
        self.assertEqual(Settings(_env_file=None).enabled_exchange_list, ["binance", "bybit", "okx", "gate"])


class PreTradeRiskTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(max_order_notional=1_000))

    def _order(self, qty: float, side: OrderSide = OrderSide.BUY) -> Order:
        return Order(symbol="BTC/USDT", side=side, quantity=qty, order_type=OrderType.MARKET)

    async def test_order_notional_limit(self) -> None:
        ok = await self.risk.check_order(self._order(0.01), mid_price=80_000)  # $800
        self.assertEqual(ok.result, RiskCheckResult.APPROVED)
        bad = await self.risk.check_order(self._order(0.02), mid_price=80_000)  # $1,600
        self.assertEqual(bad.result, RiskCheckResult.REJECTED_ORDER_NOTIONAL_LIMIT)
        self.assertIn("$1,600.00", bad.message)

    async def test_kill_switch_blocks_everything(self) -> None:
        await self.risk.kill_switch.activate(reason="test", activated_by="unit")
        detail = await self.risk.check_order(self._order(0.001), mid_price=80_000)
        self.assertEqual(detail.result, RiskCheckResult.REJECTED_KILL_SWITCH)

    async def test_sell_reduces_projected_position(self) -> None:
        self.risk.limits = RiskLimits(max_position_per_symbol=1.0, max_order_notional=1e12)
        self.risk.position_tracker.update_position("BTC/USDT", OrderSide.BUY, 1.0, 100.0)
        detail = await self.risk.check_order(self._order(0.5, OrderSide.SELL), mid_price=100.0)
        self.assertEqual(detail.result, RiskCheckResult.APPROVED)


class PriceProtectionTest(unittest.TestCase):
    def test_buy_band_is_above_and_sell_band_below(self) -> None:
        from backend.services.routing_engine.engine import protected_limit_price

        self.assertAlmostEqual(protected_limit_price(OrderSide.BUY, 100.0, 50), 100.5)
        self.assertAlmostEqual(protected_limit_price(OrderSide.SELL, 100.0, 50), 99.5)

    def test_rounds_to_tick_toward_planned_price(self) -> None:
        from backend.services.routing_engine.engine import protected_limit_price

        # 2647.09 * 1.005 = 2660.32545 -> buy floors to 2660.32 (never beyond the band)
        self.assertEqual(protected_limit_price(OrderSide.BUY, 2647.09, 50, 0.01), 2660.32)
        # 2647.08 * 0.995 = 2633.8446 -> sell ceils to 2633.85
        self.assertEqual(protected_limit_price(OrderSide.SELL, 2647.08, 50, 0.01), 2633.85)


class NetOfFeeTest(unittest.TestCase):
    def _ex(self, filled: float, fee: float, fee_asset: str):
        from backend.exchanges.base import ExecutionResult
        from backend.shared.models.execution_report import ExecType

        return ExecutionResult(exec_type=ExecType.FILL, requested_qty=filled, filled_qty=filled,
                               avg_price=1.0, fee=fee, fee_asset=fee_asset)

    def test_buy_fee_in_base_reduces_delivered_qty(self) -> None:
        from backend.services.routing_engine.engine import net_base_change

        buy = Order(symbol="XRP/USDT", side=OrderSide.BUY, quantity=731.7, order_type=OrderType.MARKET)
        self.assertAlmostEqual(net_base_change(buy, self._ex(731.7, 0.7317, "XRP")), 730.9683)

    def test_quote_fee_leaves_base_qty_untouched(self) -> None:
        from backend.services.routing_engine.engine import net_base_change

        sell = Order(symbol="XRP/USDT", side=OrderSide.SELL, quantity=100, order_type=OrderType.MARKET)
        self.assertAlmostEqual(net_base_change(sell, self._ex(100, 0.15, "USDT")), 100)


class PositionTrackerTest(unittest.TestCase):
    def test_weighted_average_entry(self) -> None:
        t = PositionTracker()
        t.update_position("BTC/USDT", OrderSide.BUY, 1.0, 100.0)
        t.update_position("BTC/USDT", OrderSide.BUY, 1.0, 110.0)
        self.assertAlmostEqual(t.get_position("BTC/USDT").avg_entry_price, 105.0)
        t.update_position("BTC/USDT", OrderSide.SELL, 0.5, 200.0)  # reduce: entry unchanged
        self.assertAlmostEqual(t.get_position("BTC/USDT").avg_entry_price, 105.0)
        t.update_position("BTC/USDT", OrderSide.SELL, 1.5, 90.0)  # flat
        self.assertEqual(t.get_position("BTC/USDT").net_position, 0.0)
        self.assertEqual(t.get_position("BTC/USDT").avg_entry_price, 0.0)


if __name__ == "__main__":
    unittest.main()
