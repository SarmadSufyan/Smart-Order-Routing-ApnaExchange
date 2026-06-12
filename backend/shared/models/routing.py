from enum import Enum

from pydantic import BaseModel

from backend.shared.models.order import ChildOrder


class RoutingStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class RoutingResult(BaseModel):
    status: RoutingStatus
    child_orders: list[ChildOrder] = []
    remaining_quantity: float = 0.0
    rejection_reason: str | None = None
    routing_time_ms: float = 0.0


class RoutingStatusResponse(BaseModel):
    active_strategy: str = "best_price"
    available_strategies: list[str] = ["best_price"]
    routable_venues: list[str] = []
    excluded_venues: dict[str, str] = {}
    stats: dict = {}
