"""
server/engine/orderbook_engine.py

Generates synthetic order book depth around the current mid-price.
Not a full matching engine — synthesizes levels on every read.
Geometric size decay at deeper levels (realistic).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from server.profiles.base import VenueProfile


@dataclass
class BookLevel:
    price: float
    size: int


@dataclass
class OrderBookSnapshot:
    bids: list[BookLevel]
    asks: list[BookLevel]


class OrderBookEngine:
    """Stateless depth generator. Call .snapshot() to get current book."""

    def __init__(self, profile: VenueProfile) -> None:
        self._profile = profile

    def snapshot(self, mid_price: float, depth: int = 5) -> OrderBookSnapshot:
        """
        Build a synthetic order book around the given mid-price.

        Args:
            mid_price: Current GBM mid-price from the price engine.
            depth:     Number of levels to generate on each side.

        Returns:
            OrderBookSnapshot with bid and ask levels.
        """
        half_spread = mid_price * (self._profile.spread_bps / 10_000) / 2
        base_size = self._profile.liquidity_depth
        variance = self._profile.depth_variance

        bids: list[BookLevel] = []
        asks: list[BookLevel] = []

        for level in range(depth):
            # Each deeper level is ~0.3–0.6 bps wider
            level_offset = half_spread * (1 + level * 0.5)

            # Prices
            ask_price = round(mid_price + level_offset, 2)
            bid_price = round(mid_price - level_offset, 2)

            # Sizes: geometric decay deeper in book (1.0, 0.7, 0.5, 0.35, ...)
            # with random variance
            decay = 0.7 ** level
            size_ask = max(1, int(base_size * decay * random.uniform(1 - variance, 1 + variance)))
            size_bid = max(1, int(base_size * decay * random.uniform(1 - variance, 1 + variance)))

            asks.append(BookLevel(price=ask_price, size=size_ask))
            bids.append(BookLevel(price=bid_price, size=size_bid))

        return OrderBookSnapshot(bids=bids, asks=asks)

    def top_of_book(self, mid_price: float) -> tuple[float, float, int, int]:
        """
        Returns (bid_price, ask_price, bid_size, ask_size) for top-of-book.
        Used by the /quote endpoint.
        """
        snap = self.snapshot(mid_price, depth=1)
        bid = snap.bids[0]
        ask = snap.asks[0]
        return bid.price, ask.price, bid.size, ask.size

    def available_size(self, mid_price: float, side: str) -> int:
        """Total size across top 3 levels for a given side."""
        snap = self.snapshot(mid_price, depth=3)
        levels = snap.asks if side == "BUY" else snap.bids
        return sum(lvl.size for lvl in levels)
