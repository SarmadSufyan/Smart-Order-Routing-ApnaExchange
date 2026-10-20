"""The monthly records export: month bounds, rows as the exchanges reported them, the per-coin summary."""

import json
import unittest

from scripts import export_records as ex


class ExportTest(unittest.TestCase):
    def test_month_bounds_include_december(self) -> None:
        self.assertEqual(ex.month_bounds("2026-11"), ("2026-11-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00"))
        self.assertEqual(ex.month_bounds("2026-12")[1], "2027-01-01T00:00:00+00:00")
        with self.assertRaises(ValueError):
            ex.month_bounds("Nov 2026")

    def test_fills_name_the_exchange_and_keep_the_fee_as_charged(self) -> None:
        [row] = ex.fill_rows([("2026-11-02T10:00:00+00:00", "o1", "V4", "BTC/USDT", "BUY", 0.0002, 80000.0,
                               0.0000002, "BTC", 0.016, "x9")])
        self.assertEqual((row["exchange"], row["value_usdt"], row["fee"], row["fee_asset"], row["fee_usdt"]),
                         ("Bybit", 16.0, 0.0000002, "BTC", 0.016))

    def test_summary_per_coin(self) -> None:
        fills = ex.fill_rows([("t", "o1", "V1", "BTC/USDT", "BUY", 0.0002, 80000.0, 0, "USDT", 0.016, "a"),
                              ("t", "o2", "V4", "BTC/USDT", "SELL", 0.0002, 81000.0, 0, "USDT", 0.0162, "b")])
        s = ex.summarize(fills)["BTC/USDT"]
        self.assertEqual((s["fills"], s["bought_usdt"], s["sold_usdt"]), (2, 16.0, 16.2))
        self.assertAlmostEqual(s["fees_usdt"], 0.0322)

    def test_orders_from_their_snapshot(self) -> None:
        snap = json.dumps({"id": "o1", "symbol": "ETH/USDT", "side": "SELL", "order_type": "MARKET", "quantity": 0.01,
                           "filled_quantity": 0.01, "avg_fill_price": 2400.5, "status": "FILLED",
                           "strategy": "best_price", "submitted_by": "admin"})
        [row] = ex.order_rows([("2026-11-02T10:00:00+00:00", None, snap)])
        self.assertEqual((row["submitted_by"], row["status"], row["avg_fill_price"]), ("admin", "FILLED", 2400.5))


if __name__ == "__main__":
    unittest.main()
