"""Binance local-book sync: the snapshot + diff-stream procedure.

A silent bug here means routing on a WRONG book, so every branch of the
documented algorithm is exercised with synthetic events.
"""

import unittest

from backend.exchanges.binance.book_sync import BinanceBookSynchronizer, DiffResult
from backend.exchanges.orderbook import LocalOrderBook


def diff(first: int, last: int, bids=(), asks=()) -> dict:
    return {
        "U": first,
        "u": last,
        "b": [[str(p), str(q)] for p, q in bids],
        "a": [[str(p), str(q)] for p, q in asks],
    }


def snapshot(last_id: int, bids=((100.0, 1.0),), asks=((101.0, 1.0),)) -> dict:
    return {
        "lastUpdateId": last_id,
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
    }


class BinanceBookSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = LocalOrderBook("BTC/USDT")
        self.sync = BinanceBookSynchronizer(self.book)

    def test_events_are_buffered_until_snapshot(self) -> None:
        self.assertIs(self.sync.on_diff(diff(11, 12, bids=[(100.5, 2)])), DiffResult.BUFFERED)
        self.assertFalse(self.book.synced)
        self.assertIsNone(self.book.best_bid())

    def test_snapshot_drops_old_events_and_applies_newer_ones(self) -> None:
        self.sync.on_diff(diff(5, 8, bids=[(99.0, 9)]))  # u <= 10: inside snapshot
        self.sync.on_diff(diff(9, 12, bids=[(100.5, 2)]))  # straddles L=10: apply
        self.sync.on_diff(diff(13, 13, asks=[(101.0, 0)]))  # removes a level
        self.assertIs(self.sync.load_snapshot(snapshot(10)), DiffResult.APPLIED)
        self.assertTrue(self.book.synced)
        self.assertEqual(self.book.last_update_id, 13)
        self.assertEqual(self.book.best_bid(), (100.5, 2.0))
        self.assertIsNone(self.book.best_ask())  # 101 removed by qty 0
        self.assertNotIn(99.0, dict(self.book.top_bids(10)))  # old event not applied

    def test_snapshot_older_than_stream_is_rejected(self) -> None:
        self.sync.on_diff(diff(50, 55))
        self.assertIs(self.sync.load_snapshot(snapshot(40)), DiffResult.GAP)
        self.assertFalse(self.book.synced)

    def test_snapshot_exactly_one_before_stream_is_accepted(self) -> None:
        self.sync.on_diff(diff(11, 12, bids=[(100.2, 3)]))
        self.assertIs(self.sync.load_snapshot(snapshot(10)), DiffResult.APPLIED)
        self.assertEqual(self.book.best_bid(), (100.2, 3.0))

    def test_continuous_chain_applies(self) -> None:
        self.sync.load_snapshot(snapshot(10))
        self.assertIs(self.sync.on_diff(diff(11, 11, bids=[(100.1, 1)])), DiffResult.APPLIED)
        self.assertIs(self.sync.on_diff(diff(12, 15, bids=[(100.0, 0)])), DiffResult.APPLIED)
        self.assertEqual(self.book.last_update_id, 15)
        self.assertEqual(self.book.best_bid(), (100.1, 1.0))

    def test_stale_event_is_ignored(self) -> None:
        self.sync.load_snapshot(snapshot(10))
        self.assertIs(self.sync.on_diff(diff(8, 10, bids=[(1.0, 1)])), DiffResult.STALE)
        self.assertEqual(self.book.best_bid(), (100.0, 1.0))

    def test_gap_is_detected(self) -> None:
        self.sync.load_snapshot(snapshot(10))
        self.assertIs(self.sync.on_diff(diff(13, 14)), DiffResult.GAP)  # 11-12 missing

    def test_gap_inside_buffer_forces_resync(self) -> None:
        self.sync.on_diff(diff(11, 12))
        self.sync.on_diff(diff(20, 21))  # hole 13-19
        self.assertIs(self.sync.load_snapshot(snapshot(10)), DiffResult.GAP)
        self.assertFalse(self.book.synced)
        self.assertEqual(self.sync.resyncs, 1)

    def test_reset_then_resync_recovers(self) -> None:
        self.sync.load_snapshot(snapshot(10))
        self.sync.reset()
        self.assertTrue(self.sync.needs_snapshot)
        self.sync.on_diff(diff(31, 31, asks=[(100.9, 4)]))
        self.assertIs(self.sync.load_snapshot(snapshot(30)), DiffResult.APPLIED)
        self.assertEqual(self.book.best_ask(), (100.9, 4.0))


if __name__ == "__main__":
    unittest.main()
