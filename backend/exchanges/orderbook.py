"""Exchange-agnostic local L2 order book.

Holds ``price -> quantity`` for each side and nothing else. How the book is
kept in sync (Binance update IDs, Coinbase sequence numbers, Kraken
checksums) is exchange-specific and lives in each adapter.

This is also the component the C++ hot path (Phase 5) replaces, so the
public surface is deliberately small.
"""

import heapq
import time
from collections.abc import Iterable

Level = tuple[float, float]  # (price, quantity)


class LocalOrderBook:
    __slots__ = ("symbol", "_bids", "_asks", "last_update_id", "updated_at", "synced")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self.last_update_id: int = 0
        self.updated_at: float = 0.0  # time.monotonic() of the last change
        self.synced: bool = False

    # ── mutation ────────────────────────────────────────────────────
    def load_snapshot(
        self, bids: Iterable[Level], asks: Iterable[Level], update_id: int
    ) -> None:
        """Replace the whole book with a REST snapshot and mark it synced."""
        self._bids = {p: q for p, q in bids if q > 0}
        self._asks = {p: q for p, q in asks if q > 0}
        self.last_update_id = update_id
        self.updated_at = time.monotonic()
        self.synced = True

    def apply_levels(
        self, bids: Iterable[Level], asks: Iterable[Level], update_id: int
    ) -> None:
        """Apply absolute level updates. Quantity 0 removes the level."""
        for p, q in bids:
            if q == 0:
                self._bids.pop(p, None)
            else:
                self._bids[p] = q
        for p, q in asks:
            if q == 0:
                self._asks.pop(p, None)
            else:
                self._asks[p] = q
        self.last_update_id = update_id
        self.updated_at = time.monotonic()

    def invalidate(self) -> None:
        """Drop all state; the book must be re-seeded from a snapshot."""
        self._bids.clear()
        self._asks.clear()
        self.last_update_id = 0
        self.synced = False

    # ── reads ───────────────────────────────────────────────────────
    def best_bid(self) -> Level | None:
        if not self._bids:
            return None
        p = max(self._bids)
        return p, self._bids[p]

    def best_ask(self) -> Level | None:
        if not self._asks:
            return None
        p = min(self._asks)
        return p, self._asks[p]

    def top_bids(self, depth: int) -> list[Level]:
        """Best ``depth`` bid levels, highest price first. O(n log depth)."""
        return heapq.nlargest(depth, self._bids.items())

    def top_asks(self, depth: int) -> list[Level]:
        """Best ``depth`` ask levels, lowest price first. O(n log depth)."""
        return heapq.nsmallest(depth, self._asks.items())

    @property
    def is_crossed(self) -> bool:
        bb, ba = self.best_bid(), self.best_ask()
        return bool(bb and ba and bb[0] >= ba[0])

    @property
    def level_count(self) -> tuple[int, int]:
        return len(self._bids), len(self._asks)
