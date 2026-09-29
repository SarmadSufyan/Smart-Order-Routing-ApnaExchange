import unittest

from backend.exchanges.orderbook import LocalOrderBook
from backend.exchanges.precision import floor_to_step, is_close


class FloorToStepTest(unittest.TestCase):
    def test_btc_lot(self) -> None:
        self.assertEqual(floor_to_step(0.0012345, 0.00001), 0.00123)

    def test_xrp_lot(self) -> None:
        self.assertEqual(floor_to_step(12.37, 0.1), 12.3)

    def test_float_artifacts_do_not_lose_a_step(self) -> None:
        # 0.1 + 0.2 == 0.30000000000000004 in binary floats
        self.assertEqual(floor_to_step(0.1 + 0.2, 0.1), 0.3)
        self.assertEqual(floor_to_step(0.3, 0.1), 0.3)

    def test_float_sum_below_the_grid_keeps_its_step(self) -> None:
        # Regression (found live): a float sum of on-grid slices can land just
        # BELOW the grid; flooring it lost a whole step (0.08 BNB -> 0.0799).
        self.assertEqual(0.1 + 0.7, 0.7999999999999999)  # the trap itself
        self.assertEqual(floor_to_step(0.1 + 0.7, 0.1), 0.8)
        self.assertEqual(floor_to_step(0.1 + 0.7, 0.0001), 0.8)

    def test_never_rounds_up(self) -> None:
        self.assertEqual(floor_to_step(0.0000099, 0.00001), 0.0)

    def test_no_step_means_no_rounding(self) -> None:
        self.assertEqual(floor_to_step(1.23456, 0), 1.23456)

    def test_is_close(self) -> None:
        self.assertTrue(is_close(0.1 + 0.2, 0.3))
        self.assertFalse(is_close(0.3, 0.30001))


class LocalOrderBookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = LocalOrderBook("BTC/USDT")
        self.book.load_snapshot(
            bids=[(100.0, 1.0), (99.0, 2.0), (98.0, 3.0)],
            asks=[(101.0, 1.5), (102.0, 2.5), (103.0, 0.0)],  # 0 qty must be dropped
            update_id=7,
        )

    def test_snapshot_loaded(self) -> None:
        self.assertTrue(self.book.synced)
        self.assertEqual(self.book.best_bid(), (100.0, 1.0))
        self.assertEqual(self.book.best_ask(), (101.0, 1.5))
        self.assertEqual(self.book.level_count, (3, 2))

    def test_top_levels_are_sorted_best_first(self) -> None:
        self.assertEqual(self.book.top_bids(2), [(100.0, 1.0), (99.0, 2.0)])
        self.assertEqual(self.book.top_asks(5), [(101.0, 1.5), (102.0, 2.5)])

    def test_apply_updates_and_removes(self) -> None:
        self.book.apply_levels(bids=[(100.0, 0.0), (100.5, 4.0)], asks=[(101.0, 0.7)], update_id=8)
        self.assertEqual(self.book.best_bid(), (100.5, 4.0))
        self.assertEqual(self.book.best_ask(), (101.0, 0.7))
        self.assertEqual(self.book.last_update_id, 8)

    def test_crossed_detection(self) -> None:
        self.assertFalse(self.book.is_crossed)
        self.book.apply_levels(bids=[(101.5, 1.0)], asks=[], update_id=9)
        self.assertTrue(self.book.is_crossed)

    def test_invalidate(self) -> None:
        self.book.invalidate()
        self.assertFalse(self.book.synced)
        self.assertIsNone(self.book.best_bid())


if __name__ == "__main__":
    unittest.main()
