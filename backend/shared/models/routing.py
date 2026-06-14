from enum import Enum

from pydantic import BaseModel

from backend.shared.models.order import ChildOrder


class RoutingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class VenueCandidate(BaseModel):
    """One row in the routing decision — what each venue offered."""
    venue_id: str
    price: float                  # ask (BUY) or bid (SELL) at decision time
    size: float                   # top-of-book size on that side
    rank: int                     # 1 = best, 2 = second best, ...
    eligible: bool                # routable (not blacklisted, has size & price)
    excluded_reason: str | None = None  # populated when eligible=False
    allocated_qty: float = 0.0    # how many shares the SOR sent here
    is_winner: bool = False       # True if this venue got any allocation


class RoutingDecision(BaseModel):
    """A snapshot of WHY the SOR routed the way it did. Designed for showing
    the panel that the SOR is genuinely picking best-price and how it splits
    across venues when a single venue can't absorb the full order."""
    side: str                                     # BUY or SELL
    requested_quantity: float
    total_allocated: float
    candidates: list[VenueCandidate]              # all 5 venues, ranked
    winning_venues: list[str]                     # venues that received an allocation, in order
    blended_avg_price: float                      # weighted avg of allocated children
    worst_price: float                            # worst price the order could have gotten (last eligible)
    savings_per_share: float                      # worst_price - blended_avg (BUY) or blended_avg - worst (SELL)
    total_savings: float                          # savings_per_share * total_allocated
    is_split: bool                                # True if order touched 2+ venues
    notes: list[str] = []                         # human-readable explanation strings


class RoutingResult(BaseModel):
    status: RoutingStatus
    child_orders: list[ChildOrder] = []
    remaining_quantity: float = 0.0
    rejection_reason: str | None = None
    routing_time_ms: float = 0.0
    decision: RoutingDecision | None = None


class RoutingStatusResponse(BaseModel):
    active_strategy: str = "best_price"
    available_strategies: list[str] = ["best_price"]
    routable_venues: list[str] = []
    excluded_venues: dict[str, str] = {}
    stats: dict = {}
