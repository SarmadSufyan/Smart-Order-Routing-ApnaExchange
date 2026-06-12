"""
server/engine/fill_simulator.py

Decides the execution outcome for an incoming order:
  FILL        — full quantity at a single price
  PARTIAL     — fraction of quantity filled
  REJECT      — venue rejects the order entirely

Uses the venue profile's reject_rate, partial_fill_prob, and default_fill_rate.
Accounts for degraded state (multiplied reject rate).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from server.engine.orderbook_engine import OrderBookEngine
from server.engine.price_engine import PriceEngine
from server.profiles.base import VenueProfile


class ExecType(str, Enum):
    FILL = "FILL"
    PARTIAL = "PARTIAL"
    REJECT = "REJECT"


@dataclass
class FillResult:
    exec_type: ExecType
    filled_qty: int
    fill_price: float
    reject_reason: str | None = None


# Domain exceptions (converted to HTTP at the router boundary)

class InsufficientLiquidityError(Exception):
    """Raised when the book cannot fill even 1 share."""
    pass


class VenueCapacityError(Exception):
    """Raised when venue is overloaded."""
    pass


_REJECT_REASONS = [
    "Insufficient liquidity",
    "Order rate limit exceeded",
    "Matching engine busy",
    "Price too stale",
    "Circuit breaker triggered",
]


class FillSimulator:
    """Stateless fill decision engine. Call .execute() per order."""

    def __init__(
        self,
        profile: VenueProfile,
        price_engine: PriceEngine,
        orderbook: OrderBookEngine,
    ) -> None:
        self._profile = profile
        self._price_engine = price_engine
        self._orderbook = orderbook
        self._exec_counter: int = 0
        self._is_degraded: bool = False

    @property
    def is_degraded(self) -> bool:
        return self._is_degraded

    def set_degraded(self, degraded: bool) -> None:
        self._is_degraded = degraded

    def next_exec_id(self) -> str:
        self._exec_counter += 1
        return f"{self._profile.venue_id}-exec-{self._exec_counter:06d}"

    def execute(self, side: str, quantity: int, price: float) -> FillResult:
        """
        Simulate execution of an order.

        Args:
            side:     "BUY" or "SELL"
            quantity: Number of shares
            price:    Limit price (or market price estimate)

        Returns:
            FillResult with exec_type, filled_qty, fill_price, and
            optionally reject_reason.
        """
        # ── Step 1: check for rejection ──────────────────────────────────
        reject_rate = self._profile.reject_rate
        if self._is_degraded:
            reject_rate = min(0.95, reject_rate * self._profile.degraded_reject_multiplier)

        if random.random() < reject_rate:
            return FillResult(
                exec_type=ExecType.REJECT,
                filled_qty=0,
                fill_price=0.0,
                reject_reason=random.choice(_REJECT_REASONS),
            )

        # ── Step 2: determine fill price with slippage ───────────────────
        mid = self._price_engine.current_price
        slippage_bps = random.uniform(0, 0.5)
        if side == "BUY":
            _, ask_price, _, _ = self._orderbook.top_of_book(mid)
            fill_price = round(ask_price * (1 + slippage_bps / 10_000), 2)
        else:
            bid_price, _, _, _ = self._orderbook.top_of_book(mid)
            fill_price = round(bid_price * (1 - slippage_bps / 10_000), 2)

        # ── Step 3: determine fill quantity ──────────────────────────────
        available = self._orderbook.available_size(mid, side)

        if random.random() < self._profile.partial_fill_prob:
            # Partial fill: fill a fraction
            frac = random.betavariate(
                self._profile.default_fill_rate * 5,
                (1 - self._profile.default_fill_rate) * 5,
            )
            filled_qty = max(1, int(min(quantity, available) * frac))
        else:
            # Full fill (capped by available liquidity)
            filled_qty = min(quantity, available)

        if filled_qty <= 0:
            return FillResult(
                exec_type=ExecType.REJECT,
                filled_qty=0,
                fill_price=0.0,
                reject_reason="Insufficient liquidity",
            )

        # Record trade in price engine (updates last_price + volume)
        self._price_engine.record_trade(filled_qty, fill_price)

        exec_type = ExecType.FILL if filled_qty >= quantity else ExecType.PARTIAL
        return FillResult(
            exec_type=exec_type,
            filled_qty=filled_qty,
            fill_price=fill_price,
        )
