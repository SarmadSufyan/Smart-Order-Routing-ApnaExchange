import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from backend.shared.database.store import AuditStore
from backend.shared.models.order import Order, OrderSide, OrderStatus, OrderType
from backend.shared.models.portfolio import FillRecord, VenueBaseline

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class AuditStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = AuditStore(":memory:")
        await self.store.open()

    async def asyncTearDown(self) -> None:
        await self.store.close()

    async def test_events_keep_the_order_they_happened_in(self) -> None:
        for i in range(5):
            self.store.append_event("order_status", f"step {i}", order_id="o1")
        self.store.append_event("kill_switch", "engaged", actor="sarmad")
        await self.store.flush()

        timeline = await self.store.query_events(order_id="o1", ascending=True)
        self.assertEqual([e.summary for e in timeline], [f"step {i}" for i in range(5)])
        newest = await self.store.query_events(limit=1)
        self.assertEqual((newest[0].event_type, newest[0].actor), ("kill_switch", "sarmad"))
        by_type = await self.store.query_events(event_type="kill_switch,risk_limits_changed")
        self.assertEqual(len(by_type), 1)

    async def test_latest_order_snapshot_wins(self) -> None:
        order = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET,
                      submitted_by="sarmad")
        self.store.upsert_order(order)
        order.status = OrderStatus.FILLED
        order.filled_quantity = 1.0
        self.store.upsert_order(order)
        await self.store.flush()

        [loaded] = await self.store.load_orders()
        self.assertEqual(loaded.status, OrderStatus.FILLED)
        self.assertEqual(loaded.submitted_by, "sarmad")

    async def test_fills_baselines_and_kv_round_trip(self) -> None:
        self.store.record_fill(FillRecord(id="c1", ts=T0, order_id="o1", venue_id="V1", symbol="BTC/USDT",
                                          side=OrderSide.SELL, quantity=0.5, price=100.0, fee=0.1,
                                          fee_asset="USDT", fee_usdt=0.1))
        old = VenueBaseline(venue_id="V1", ts=T0, balances={"BTC": 1.0}, marks={"BTC": 100.0})
        new = VenueBaseline(venue_id="V1", ts=T0 + timedelta(hours=1), set_by="sarmad",
                            balances={"BTC": 2.0}, marks={"BTC": 110.0})
        self.store.save_baseline(old)
        self.store.save_baseline(new)
        self.store.kv_set("kill_switch", {"active": True, "reason": "test"})
        await self.store.flush()

        [f] = await self.store.load_fills()
        self.assertEqual((f.side, f.quantity, f.fee_asset), (OrderSide.SELL, 0.5, "USDT"))
        self.assertEqual(await self.store.load_baselines(), {"V1": new})  # latest is active
        self.assertEqual((await self.store.kv_get("kill_switch"))["reason"], "test")
        self.assertIsNone(await self.store.kv_get("missing"))

    async def test_survives_reopen_and_continues_sequence(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            first = AuditStore(path)
            await first.open()
            first.append_event("gateway_started", "one")
            await first.close()

            second = AuditStore(path)
            await second.open()
            event = second.append_event("gateway_started", "two")
            await second.close()
            self.assertEqual(event.seq, 2)
        finally:
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.unlink(path + suffix)


if __name__ == "__main__":
    unittest.main()
