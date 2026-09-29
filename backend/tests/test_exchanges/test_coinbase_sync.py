"""Coinbase level2 sync: in-stream snapshots + per-connection sequence numbers."""

import unittest

from backend.exchanges.coinbase.book_sync import CoinbaseBookSynchronizer, SeqResult
from backend.exchanges.orderbook import LocalOrderBook


def event(kind: str, product: str, updates: list[tuple[str, float, float]]) -> dict:
    return {
        "type": kind,
        "product_id": product,
        "updates": [{"side": s, "price_level": str(p), "new_quantity": str(q)} for s, p, q in updates],
    }


class CoinbaseBookSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.btc = LocalOrderBook("BTC/USDT")
        self.eth = LocalOrderBook("ETH/USDT")
        self.sync = CoinbaseBookSynchronizer({"BTC-USDT": self.btc, "ETH-USDT": self.eth})

    def test_snapshot_seeds_book_with_offer_as_ask(self) -> None:
        self.sync.on_l2_event(event("snapshot", "BTC-USDT", [("bid", 100, 1), ("offer", 101, 2)]), 0)
        self.assertTrue(self.btc.synced)
        self.assertEqual(self.btc.best_bid(), (100.0, 1.0))
        self.assertEqual(self.btc.best_ask(), (101.0, 2.0))
        self.assertFalse(self.eth.synced)  # other products wait for their own snapshot

    def test_updates_apply_and_zero_removes(self) -> None:
        self.sync.on_l2_event(event("snapshot", "BTC-USDT", [("bid", 100, 1), ("offer", 101, 2)]), 0)
        self.sync.on_l2_event(event("update", "BTC-USDT", [("bid", 100, 0), ("bid", 100.5, 3)]), 1)
        self.assertEqual(self.btc.best_bid(), (100.5, 3.0))

    def test_update_before_snapshot_is_ignored(self) -> None:
        self.sync.on_l2_event(event("update", "ETH-USDT", [("bid", 10, 1)]), 0)
        self.assertFalse(self.eth.synced)
        self.assertIsNone(self.eth.best_bid())

    def test_contiguous_sequence_is_ok(self) -> None:
        for seq in range(5):
            self.assertIs(self.sync.check_sequence(seq), SeqResult.OK)

    def test_sequence_gap_is_detected(self) -> None:
        self.sync.check_sequence(0)
        self.sync.check_sequence(1)
        self.assertIs(self.sync.check_sequence(3), SeqResult.GAP)
        self.assertEqual(self.sync.gaps, 1)

    def test_reset_invalidates_all_books_and_restarts_sequence(self) -> None:
        self.sync.on_l2_event(event("snapshot", "BTC-USDT", [("bid", 100, 1), ("offer", 101, 2)]), 0)
        self.sync.check_sequence(41)
        self.sync.reset()
        self.assertFalse(self.btc.synced)
        self.assertIs(self.sync.check_sequence(0), SeqResult.OK)  # new connection starts over


if __name__ == "__main__":
    unittest.main()
