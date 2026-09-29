"""Phase 3 building blocks: in-stream sync (Bybit/OKX), coherence, client ids."""

import re
import unittest
from uuid import uuid4

from backend.exchanges.ccxt_venue import venue_client_id
from backend.exchanges.orderbook import LocalOrderBook
from backend.exchanges.stream_sync import ChainResult, InStreamSnapshotSync
from backend.services.venue_monitor.coherence import evaluate
from backend.shared.models.venue import CoherenceStatus


class InStreamSnapshotSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = LocalOrderBook("BTC/USDT")
        self.sync = InStreamSnapshotSync(self.book)

    def test_delta_before_snapshot_is_ignored(self) -> None:
        self.assertIs(self.sync.on_delta([(1.0, 1.0)], [], 5, chained=True), ChainResult.IGNORED)
        self.assertFalse(self.book.synced)

    def test_snapshot_then_chained_deltas(self) -> None:
        self.sync.on_snapshot([(100.0, 1.0)], [(101.0, 1.0)], 10)
        self.assertIs(self.sync.on_delta([(100.5, 2.0)], [], 11, chained=True), ChainResult.APPLIED)
        self.assertEqual(self.book.best_bid(), (100.5, 2.0))
        self.assertEqual(self.sync.last_id, 11)

    def test_broken_chain_invalidates_book(self) -> None:
        self.sync.on_snapshot([(100.0, 1.0)], [(101.0, 1.0)], 10)
        self.assertIs(self.sync.on_delta([], [], 13, chained=False), ChainResult.GAP)
        self.assertFalse(self.book.synced)
        self.assertEqual(self.sync.gaps, 1)

    def test_new_snapshot_resets_book(self) -> None:
        self.sync.on_snapshot([(100.0, 1.0)], [(101.0, 1.0)], 10)
        self.sync.on_snapshot([(90.0, 1.0)], [(91.0, 1.0)], 1)  # e.g. Bybit service restart (u=1)
        self.assertEqual(self.book.best_bid(), (90.0, 1.0))
        self.assertIsNone(dict(self.book.top_bids(5)).get(100.0))


class CoherenceTest(unittest.TestCase):
    def test_tracking_book_is_ok(self) -> None:
        st = evaluate(100.02, 100.04, 100.0, 50)
        self.assertIs(st.status, CoherenceStatus.OK)
        self.assertAlmostEqual(st.divergence_bps, 3.0, places=4)

    def test_divergent_book_is_blocked_both_directions(self) -> None:
        self.assertIs(evaluate(101.6, 101.7, 100.0, 50).status, CoherenceStatus.DIVERGED)  # like Bybit BTC
        self.assertIs(evaluate(98.0, 98.1, 100.0, 50).status, CoherenceStatus.DIVERGED)

    def test_empty_side_is_blocked(self) -> None:
        self.assertIs(evaluate(413.0, None, 762.0, 50).status, CoherenceStatus.EMPTY)  # like Bybit BNB

    def test_unknown_without_reference(self) -> None:
        self.assertIs(evaluate(100.0, 100.1, None, 50).status, CoherenceStatus.UNKNOWN)


class ClientIdTest(unittest.TestCase):
    def test_fits_every_venue(self) -> None:
        cid = venue_client_id(str(uuid4()))
        self.assertEqual(len(cid), 27)  # Gate: <=28 before its t- prefix
        self.assertRegex(cid, r"^[a-z0-9]+$")  # OKX: alphanumeric only
        self.assertNotEqual(cid[0], "t")  # so ccxt adds Gate's t- prefix

    def test_is_deterministic_per_child(self) -> None:
        child = str(uuid4())
        self.assertEqual(venue_client_id(child), venue_client_id(child))
        self.assertTrue(re.fullmatch(r"sor[0-9a-f]{24}", venue_client_id(child)))


if __name__ == "__main__":
    unittest.main()
