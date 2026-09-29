"""Keeps LocalOrderBooks in sync with Coinbase Advanced Trade ``level2``.

Coinbase's protocol differs from Binance's in two ways, so it gets its own
synchronizer:

  * The snapshot arrives IN the stream (first ``l2_data`` event per product,
    ``type="snapshot"``) — no REST snapshot, no buffering.
  * Integrity is tracked with ``sequence_num``, one counter for the WHOLE
    connection (all channels and products), incremented by exactly 1 per
    message. Any jump means a message was lost somewhere on the connection,
    so every book on it is suspect -> the caller must reconnect.

Sides are ``"bid"`` and ``"offer"``; quantities are absolute (0 = remove).
"""

from enum import Enum
from typing import Any

from backend.exchanges.orderbook import Level, LocalOrderBook


class SeqResult(str, Enum):
    OK = "OK"
    GAP = "GAP"  # a message was lost on this connection -> reconnect


class CoinbaseBookSynchronizer:
    def __init__(self, books_by_product: dict[str, LocalOrderBook]) -> None:
        self._books = books_by_product
        self._last_seq: int | None = None
        self.gaps = 0

    def reset(self) -> None:
        """New connection: sequence restarts and every book waits for a snapshot."""
        self._last_seq = None
        for book in self._books.values():
            book.invalidate()

    def check_sequence(self, seq: int | None) -> SeqResult:
        if seq is None:
            return SeqResult.OK
        if self._last_seq is not None and seq != self._last_seq + 1:
            self.gaps += 1
            return SeqResult.GAP
        self._last_seq = seq
        return SeqResult.OK

    def on_l2_event(self, event: dict[str, Any], seq: int) -> None:
        """Apply one ``l2_data`` event (``snapshot`` or ``update``)."""
        book = self._books.get(event.get("product_id", ""))
        if book is None:
            return
        bids: list[Level] = []
        asks: list[Level] = []
        for u in event.get("updates", []):
            level = (float(u["price_level"]), float(u["new_quantity"]))
            if u["side"] == "bid":
                bids.append(level)
            else:  # "offer" (Coinbase's name for the ask side)
                asks.append(level)

        if event.get("type") == "snapshot":
            book.load_snapshot(bids, asks, seq)
        elif book.synced:
            book.apply_levels(bids, asks, seq)
        # An update before the product's snapshot can't be applied; the
        # snapshot always arrives first on a healthy connection.
