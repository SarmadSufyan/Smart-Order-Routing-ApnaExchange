import unittest
from datetime import UTC, datetime, timedelta

from backend.services.wallet.ledger import Lot, build_ledger, fill_base_change, value_ledger
from backend.shared.models.order import OrderSide
from backend.shared.models.portfolio import FillRecord, VenueBaseline

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def fill(n: int, side: OrderSide, qty: float, price: float, *, venue: str = "V1", symbol: str = "BTC/USDT",
         fee: float = 0.0, fee_asset: str | None = None, fee_usdt: float | None = None,
         at: datetime | None = None) -> FillRecord:
    return FillRecord(
        id=f"c{n}", order_id=f"o{n}", ts=at or T0 + timedelta(minutes=n), venue_id=venue, symbol=symbol,
        side=side, quantity=qty, price=price, fee=fee, fee_asset=fee_asset, fee_usdt=fee_usdt,
    )


class LotTest(unittest.TestCase):
    def test_average_cost_and_realized(self) -> None:
        lot = Lot()
        lot.trade(1.0, 100.0)
        lot.trade(1.0, 200.0)
        self.assertAlmostEqual(lot.avg, 150.0)
        lot.trade(-0.5, 300.0)  # sell half a coin 150 above cost
        self.assertAlmostEqual(lot.realized, 75.0)
        self.assertAlmostEqual(lot.avg, 150.0)  # reducing keeps the cost
        self.assertAlmostEqual(lot.qty, 1.5)

    def test_flat_then_flip(self) -> None:
        lot = Lot(qty=1.0, avg=100.0)
        lot.trade(-1.0, 110.0)
        self.assertEqual((lot.qty, lot.avg), (0.0, 0.0))
        self.assertAlmostEqual(lot.realized, 10.0)
        lot = Lot(qty=1.0, avg=100.0)
        lot.trade(-1.5, 90.0)  # sells more than held: the extra is a new (short) position
        self.assertAlmostEqual(lot.qty, -0.5)
        self.assertAlmostEqual(lot.avg, 90.0)
        self.assertAlmostEqual(lot.realized, -10.0)


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baselines = {
            "V1": VenueBaseline(venue_id="V1", ts=T0, balances={"BTC": 1.0, "USDT": 1_000.0, "BNB": 2.0},
                                marks={"BTC": 100.0, "BNB": 10.0}),
        }
        self.marks = {("V1", "BTC"): 120.0, ("V1", "BNB"): 12.0}

    def mark(self, venue: str, asset: str) -> float | None:
        return 1.0 if asset == "USDT" else self.marks.get((venue, asset))

    def assert_identity(self, val) -> None:
        # value now - opening value == realized + unrealized - fees
        self.assertAlmostEqual(val.total, val.realized + val.unrealized - val.fees, places=9)

    def test_no_fills_is_pure_hold_pnl(self) -> None:
        val = value_ledger(build_ledger(self.baselines, []), self.baselines, self.mark)
        self.assertTrue(val.complete)
        self.assertAlmostEqual(val.total, 1.0 * 20 + 2.0 * 2)  # BTC +20, BNB +2
        self.assertAlmostEqual(val.hold, val.total)
        self.assertAlmostEqual(val.trading, 0.0)
        self.assert_identity(val)

    def test_buy_then_price_up_is_trading_profit(self) -> None:
        # Bought 1 BTC at 110 after the baseline; the mark is now 120.
        fills = [fill(1, OrderSide.BUY, 1.0, 110.0, fee=1.1, fee_asset="USDT")]
        ledger = build_ledger(self.baselines, fills)
        self.assertAlmostEqual(ledger.quantity("V1", "BTC"), 2.0)
        self.assertAlmostEqual(ledger.quantity("V1", "USDT"), 1_000 - 110 - 1.1)
        val = value_ledger(ledger, self.baselines, self.mark)
        self.assertAlmostEqual(val.fees, 1.1)
        self.assertAlmostEqual(val.trading, (120 - 110) - 1.1)  # +10 on the new coin, minus the fee
        self.assert_identity(val)

    def test_fee_in_base_asset_reduces_coins(self) -> None:
        # Bybit-style: buying 1 BTC delivers 0.999 (fee 0.001 BTC).
        fills = [fill(1, OrderSide.BUY, 1.0, 110.0, fee=0.001, fee_asset="BTC")]
        ledger = build_ledger(self.baselines, fills)
        self.assertAlmostEqual(ledger.quantity("V1", "BTC"), 1.999)
        self.assertAlmostEqual(fill_base_change(fills[0]), 0.999)
        val = value_ledger(ledger, self.baselines, self.mark)
        self.assertAlmostEqual(val.fees, 0.001 * 110)
        self.assert_identity(val)

    def test_fee_in_third_asset(self) -> None:
        # Binance-style BNB fee: 0.01 BNB worth $0.12 at fill time.
        fills = [fill(1, OrderSide.SELL, 0.5, 130.0, fee=0.01, fee_asset="BNB", fee_usdt=0.12)]
        ledger = build_ledger(self.baselines, fills)
        self.assertAlmostEqual(ledger.quantity("V1", "BNB"), 1.99)
        self.assertAlmostEqual(ledger.quantity("V1", "BTC"), 0.5)
        val = value_ledger(ledger, self.baselines, self.mark)
        self.assertAlmostEqual(val.fees, 0.12)
        self.assertAlmostEqual(val.realized, 0.5 * (130 - 100) + 0.01 * (12 - 10))
        self.assert_identity(val)

    def test_selling_opening_coins_is_not_a_short(self) -> None:
        # Selling coins held since the baseline reduces holdings; no negative position.
        fills = [fill(1, OrderSide.SELL, 1.0, 130.0)]
        val = value_ledger(build_ledger(self.baselines, fills), self.baselines, self.mark)
        btc = next(a for a in val.assets if a.asset == "BTC")
        self.assertAlmostEqual(btc.quantity, 0.0)
        self.assertAlmostEqual(btc.realized_pnl, 30.0)
        # Sold at 130 but the coin is now worth 120: selling beat holding by $10.
        self.assertAlmostEqual(val.trading, 10.0)
        self.assert_identity(val)

    def test_fills_before_the_baseline_are_already_in_it(self) -> None:
        fills = [fill(1, OrderSide.BUY, 5.0, 90.0, at=T0 - timedelta(seconds=1))]
        ledger = build_ledger(self.baselines, fills)
        self.assertEqual((ledger.applied, ledger.before_baseline), (0, 1))
        self.assertAlmostEqual(ledger.quantity("V1", "BTC"), 1.0)

    def test_fills_on_a_venue_without_baseline_are_ignored(self) -> None:
        ledger = build_ledger(self.baselines, [fill(1, OrderSide.BUY, 1.0, 110.0, venue="V4")])
        self.assertEqual(ledger.before_baseline, 1)

    def test_missing_mark_makes_totals_unknown_not_wrong(self) -> None:
        del self.marks[("V1", "BNB")]
        val = value_ledger(build_ledger(self.baselines, []), self.baselines, self.mark)
        self.assertFalse(val.complete)
        self.assertEqual(val.unpriced, ["BNB"])
        self.assertIsNone(val.total)
        self.assertIsNone(val.trading)

    def test_two_venues_aggregate_per_asset(self) -> None:
        self.baselines["V4"] = VenueBaseline(venue_id="V4", ts=T0, balances={"BTC": 0.5, "USDT": 500.0},
                                             marks={"BTC": 100.0})
        self.marks[("V4", "BTC")] = 121.0
        fills = [fill(1, OrderSide.BUY, 0.5, 110.0, venue="V4")]
        val = value_ledger(build_ledger(self.baselines, fills), self.baselines, self.mark)
        btc = next(a for a in val.assets if a.asset == "BTC")
        self.assertAlmostEqual(btc.quantity, 2.0)
        self.assertEqual(btc.by_venue, {"V1": 1.0, "V4": 1.0})
        self.assertAlmostEqual(btc.avg_cost, (1 * 100 + 0.5 * 100 + 0.5 * 110) / 2)
        self.assert_identity(val)


if __name__ == "__main__":
    unittest.main()
