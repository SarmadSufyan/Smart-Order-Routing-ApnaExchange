"""Phase 4: kill switch halts in-flight work, loss limit, P&L service, audit trail."""

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.exchanges.base import ExecutionResult
from backend.services.audit import AuditRecorder
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.risk_engine.risk_monitor import RiskMonitor
from backend.services.routing_engine.engine import RoutingEngine
from backend.services.wallet.pnl import PnLService
from backend.shared.database.store import AuditStore
from backend.shared.exceptions import InvalidStateTransitionError
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import ChildOrder, OrderCreate, OrderSide, OrderStatus
from backend.shared.models.portfolio import FillRecord
from backend.shared.models.risk import RiskLimits
from backend.shared.models.wallet import AssetTotal, VenueWallet, WalletAsset, WalletSnapshot

NOW = datetime.now(UTC)


class StubAdapter:
    def __init__(self, venue_id: str, name: str) -> None:
        self.venue_id, self.name = venue_id, name
        self.sent = 0

    def rules(self, symbol: str):
        return None

    async def place_order(self, **kwargs) -> ExecutionResult:
        self.sent += 1
        return ExecutionResult(exec_type=ExecType.FILL, requested_qty=kwargs["quantity"],
                               filled_qty=kwargs["quantity"], avg_price=100.0)


class StubRegistry:
    def __init__(self, *adapters: StubAdapter) -> None:
        self._adapters = {a.venue_id: a for a in adapters}

    def all(self):
        return list(self._adapters.values())

    def get(self, venue_id: str):
        return self._adapters.get(venue_id)

    async def cancel_all_open_orders(self) -> int:
        return 0


def wallet_snapshot(balances: dict[str, float], prices: dict[str, float], updated_at: datetime,
                    venue: str = "V1", error: str | None = None) -> WalletSnapshot:
    assets = [WalletAsset(asset=a, total=q, free=q, price_usdt=prices.get(a, 1.0 if a == "USDT" else None),
                          value_usdt=q * prices.get(a, 1.0)) for a, q in balances.items()]
    total = sum(a.value_usdt or 0 for a in assets)
    return WalletSnapshot(
        as_of=updated_at, mode="testnet", total_value_usdt=total,
        venues=[VenueWallet(venue_id=venue, name="Binance", mode="testnet", assets=assets,
                            total_value_usdt=total, updated_at=updated_at, error=error)],
        assets=[AssetTotal(asset=a.asset, total=a.total, free=a.free, value_usdt=a.value_usdt,
                           by_venue={venue: a.total}) for a in assets],
    )


class StubWallet:
    def __init__(self, snapshot: WalletSnapshot, prices: dict[str, float]) -> None:
        self.snapshot, self.prices = snapshot, prices

    def get_snapshot(self) -> WalletSnapshot:
        return self.snapshot

    def price_usdt(self, asset: str, venue_id: str) -> float | None:
        return 1.0 if asset == "USDT" else self.prices.get(asset)

    async def refresh(self) -> WalletSnapshot:
        return self.snapshot


async def working_order(om: OrderManager, qty: float = 1.0):
    order = await om.create_order(OrderCreate(symbol="BTC/USDT", side=OrderSide.BUY, quantity=qty),
                                  submitted_by="sarmad")
    for status in (OrderStatus.VALIDATED, OrderStatus.APPROVED, OrderStatus.ROUTING):
        await om.transition(order.id, status)
    child = ChildOrder(parent_order_id=order.id, venue_id="V1", quantity=qty, price=100.0)
    await om.add_child_order(order.id, child)
    await om.transition(order.id, OrderStatus.WORKING)
    return order, child


class InFlightTest(unittest.IsolatedAsyncioTestCase):
    async def test_fill_after_cancel_is_recorded_not_lost(self) -> None:
        om = OrderManager()
        order, child = await working_order(om)
        om.begin_execution(order.id)
        await om.cancel_order(order.id, reason="Kill switch: test")
        # The IOC child was already at the exchange and filled.
        await om.update_fill(order.id, child.id, 1.0, 100.0)  # used to raise CANCELLED -> FILLED
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(order.filled_quantity, 1.0)

    async def test_finished_partial_fill_has_nothing_to_cancel(self) -> None:
        om = OrderManager()
        order, child = await working_order(om, qty=2.0)
        await om.update_fill(order.id, child.id, 1.0, 100.0)
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(om.active_orders(), [])  # execution finished: not active
        with self.assertRaises(InvalidStateTransitionError) as ctx:
            await om.cancel_order(order.id)
        self.assertIn("IOC", ctx.exception.message)

    async def test_kill_switch_stops_children_not_yet_sent(self) -> None:
        om = OrderManager()
        adapter = StubAdapter("V1", "Binance")
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker())
        engine = RoutingEngine(None, None, risk, None, om, StubRegistry(adapter))
        order, child = await working_order(om)
        await risk.kill_switch.activate(reason="halt", activated_by="unit")
        result = await engine._send_child(order, child)
        self.assertEqual(result.exec_type, ExecType.REJECT)
        self.assertIn("kill switch", result.reason)
        self.assertEqual(adapter.sent, 0)  # nothing reached the exchange

    async def test_restart_recovers_interrupted_orders(self) -> None:
        om = OrderManager()
        order, _ = await working_order(om)
        fresh = OrderManager()
        fixed = fresh.restore([order])
        self.assertEqual([o.status for o in fixed], [OrderStatus.CANCELLED])
        self.assertIn("Gateway stopped", fresh.get_order(order.id).rejection_reason)


class LossLimitTest(unittest.IsolatedAsyncioTestCase):
    class Pnl:
        value: float | None = -1_500.0

        def trading_pnl(self) -> float | None:
            return self.value

    async def test_loss_limit_engages_and_holds_the_kill_switch(self) -> None:
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(max_trading_loss_usdt=1_000))
        pnl = self.Pnl()
        wallet = StubWallet(wallet_snapshot({"USDT": 100.0}, {}, NOW), {})
        monitor = RiskMonitor(risk, pnl, wallet, OrderManager(), StubRegistry())

        await monitor.evaluate()
        self.assertTrue(risk.kill_switch.is_active)
        self.assertIn("Automatic", risk.kill_switch.get_state().reason)
        self.assertIsNotNone(monitor.deactivation_blocker())

        pnl.value = -500.0  # recovered (or the limit was raised)
        await monitor.evaluate()
        self.assertIsNone(monitor.deactivation_blocker())

    async def test_automatic_halt_goes_through_the_risk_controls(self) -> None:
        # With RAFT on, an automatic halt must be replicated like a manual one.
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(max_trading_loss_usdt=1_000))

        class RecordingControls:
            calls: list[tuple[str, str]] = []

            async def engage_kill_switch(self, reason: str, operator: str) -> None:
                self.calls.append((reason, operator))
                await risk.kill_switch.activate(reason, operator)

        controls = RecordingControls()
        monitor = RiskMonitor(risk, self.Pnl(), StubWallet(wallet_snapshot({"USDT": 100.0}, {}, NOW), {}),
                              OrderManager(), StubRegistry(), controls=controls)
        await monitor.evaluate()
        self.assertEqual(len(controls.calls), 1)
        self.assertEqual(controls.calls[0][1], "risk-monitor")
        self.assertTrue(risk.kill_switch.is_active)

    async def test_zero_limit_is_off(self) -> None:
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(max_trading_loss_usdt=0))
        monitor = RiskMonitor(risk, self.Pnl(), StubWallet(wallet_snapshot({}, {}, NOW), {}),
                              OrderManager(), StubRegistry())
        await monitor.evaluate()
        self.assertFalse(risk.kill_switch.is_active)

    async def test_concentration_flags_asset_and_exchange(self) -> None:
        risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits())
        snap = wallet_snapshot({"BTC": 7.0, "USDT": 300.0}, {"BTC": 100.0}, NOW)
        monitor = RiskMonitor(risk, self.Pnl(), StubWallet(snap, {}), OrderManager(), StubRegistry())
        risk.limits = RiskLimits(max_trading_loss_usdt=0)
        state = await monitor.evaluate()
        flagged = {(c.kind, c.key) for c in state.concentration if c.breached}
        self.assertEqual(flagged, {("asset", "BTC"), ("venue", "V1")})  # 70% BTC, 100% on one exchange
        self.assertNotIn("USDT", {c.key for c in state.concentration})  # cash isn't concentration


class PnLServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.prices = {"BTC": 100.0}
        self.t0 = NOW - timedelta(minutes=10)
        self.wallet = StubWallet(wallet_snapshot({"BTC": 1.0, "USDT": 1_000.0}, self.prices, self.t0),
                                 self.prices)
        self.orders = OrderManager()
        self.positions = PositionTracker()
        self.pnl = PnLService(StubRegistry(StubAdapter("V1", "Binance")), self.wallet, self.orders,
                              self.positions)

    async def test_baseline_then_fills_then_reconciliation(self) -> None:
        self.assertEqual(await self.pnl.capture_baselines(self.wallet.snapshot), ["V1"])
        self.pnl.record_fill(FillRecord(id="c1", order_id="o1", ts=self.t0 + timedelta(minutes=1),
                                        venue_id="V1", symbol="BTC/USDT", side=OrderSide.BUY,
                                        quantity=0.5, price=100.0))
        self.pnl.rebuild_positions()
        self.assertAlmostEqual(self.positions.get_position("BTC/USDT").net_position, 0.5)

        # Exchange agrees -> OK.
        self.wallet.snapshot = wallet_snapshot({"BTC": 1.5, "USDT": 950.0}, self.prices, NOW)
        [rec] = self.pnl.compute().reconciliation
        self.assertEqual(rec.status, "OK")

        # Coins vanished outside ApnaSOR -> BREAK with the difference.
        self.wallet.snapshot = wallet_snapshot({"BTC": 1.2, "USDT": 950.0}, self.prices, NOW)
        [rec] = self.pnl.compute().reconciliation
        self.assertEqual(rec.status, "BREAK")
        btc = next(i for i in rec.items if i.asset == "BTC")
        self.assertAlmostEqual(btc.difference, -0.3)

    async def test_recent_fill_is_pending_not_a_break(self) -> None:
        await self.pnl.capture_baselines(self.wallet.snapshot)
        filled_at = NOW - timedelta(seconds=1)
        self.pnl.record_fill(FillRecord(id="c1", order_id="o1", ts=filled_at, venue_id="V1",
                                        symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.5, price=100.0))
        # Balance read right after the fill still shows the old amount.
        self.wallet.snapshot = wallet_snapshot({"BTC": 1.0, "USDT": 1_000.0}, self.prices, NOW)
        [rec] = self.pnl.compute().reconciliation
        self.assertEqual(rec.status, "PENDING")

    async def test_no_baseline_while_an_order_is_executing(self) -> None:
        self.orders.begin_execution(UUID(int=1))
        self.assertEqual(await self.pnl.capture_baselines(self.wallet.snapshot), [])

    async def test_unpriced_holdings_wait_for_prices(self) -> None:
        self.wallet.snapshot = wallet_snapshot({"BTC": 1.0}, {}, self.t0)
        self.assertEqual(await self.pnl.capture_baselines(self.wallet.snapshot), [])


class AuditTrailTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = AuditStore(":memory:")
        await self.store.open()
        self.orders = OrderManager()
        self.kill_switch = KillSwitch()
        self.recorder = AuditRecorder(self.store, self.orders, self.kill_switch)
        self.recorder.start()

    async def asyncTearDown(self) -> None:
        self.recorder.stop()
        await self.store.close()

    async def test_order_timeline_and_kill_switch_state_are_persisted(self) -> None:
        order, child = await working_order(self.orders)
        await self.orders.update_fill(order.id, child.id, 1.0, 100.0)
        await self.kill_switch.activate(reason="panel demo", activated_by="sarmad")
        await self.store.flush()

        timeline = await self.store.query_events(order_id=str(order.id), ascending=True)
        kinds = [e.event_type for e in timeline]
        self.assertEqual(kinds[0], "order_submitted")
        self.assertEqual(timeline[0].actor, "sarmad")
        self.assertIn("Status WORKING -> FILLED", [e.summary.split(":")[0] for e in timeline])
        [saved] = await self.store.load_orders()
        self.assertEqual(saved.status, OrderStatus.FILLED)
        self.assertTrue((await self.store.kv_get("kill_switch"))["active"])


if __name__ == "__main__":
    unittest.main()
