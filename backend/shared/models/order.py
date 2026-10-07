from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ROUTING = "ROUTING"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.VALIDATED, OrderStatus.REJECTED},
    OrderStatus.VALIDATED: {OrderStatus.APPROVED, OrderStatus.REJECTED},
    OrderStatus.APPROVED: {OrderStatus.ROUTING, OrderStatus.REJECTED},
    OrderStatus.ROUTING: {OrderStatus.WORKING, OrderStatus.REJECTED},
    OrderStatus.WORKING: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.CANCELLED: set(),
}


class RoutingStrategy(str, Enum):
    """How ONE order is split across venues right now (the "where").

    Time-based execution (TWAP, VWAP, POV, ...) is the algo layer's job:
    ``backend/services/algos``, ``POST /api/algos``.
    """

    BEST_PRICE = "best_price"  # cheapest all-in (price + taker fee) levels first
    LIQUIDITY_SWEEP = "liquidity_sweep"  # every venue at once, in proportion to its depth within a price cap
    SMART = "smart"  # best price + each venue's measured fill reliability


def _routing_strategy(value: object) -> object:
    # "vwap" was listed here before 2026-10-07 but never implemented (the
    # router always used best price). VWAP is a schedule over time, so it now
    # lives with the other execution algorithms.
    if isinstance(value, str) and value.strip().lower() == "vwap":
        raise ValueError("VWAP is a time-based execution algorithm, not a routing strategy: "
                         "start it as an algo order (POST /api/algos/ with kind VWAP)")
    return value


class OrderCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20, examples=["BTC/USDT"])
    side: OrderSide
    quantity: float = Field(..., gt=0, examples=[0.001])
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(None, gt=0)
    strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE

    _strategy_check = field_validator("strategy", mode="before")(_routing_strategy)


class ChildOrder(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    parent_order_id: UUID
    venue_id: str
    quantity: float
    price: float  # expected VWAP at this venue (from the routing decision)
    limit_price: float | None = None  # IOC limit actually sent (price protection)
    # True when the strategy already set the worst acceptable price (a sweep's
    # price cap): the engine sends it as is instead of widening it.
    limit_final: bool = False
    client_order_id: str | None = None  # id sent to the venue (idempotency / reconciliation)
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    fill_price: float = 0.0
    sent_at: datetime | None = None
    filled_at: datetime | None = None
    exchange_order_id: str | None = None
    fee: float = 0.0
    fee_asset: str | None = None
    reject_reason: str | None = None


class Order(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    limit_price: float | None = None
    strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rejection_reason: str | None = None
    child_orders: list[ChildOrder] = []
    routing_decision: dict | None = None  # RoutingDecision snapshot — stored as dict to avoid circular import
    submitted_by: str | None = None  # the user whose authority placed it — for the audit trail
    parent_id: str | None = None  # algo order (e.g. a TWAP) this is a slice of

    @field_validator("strategy", mode="before")
    @classmethod
    def _stored_strategy(cls, value: object) -> object:
        # A stored order from before 2026-10-07 may say "vwap" (accepted then,
        # routed as best price): restore it as what actually happened.
        return RoutingStrategy.BEST_PRICE if isinstance(value, str) and value.lower() == "vwap" else value

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        return new_status in VALID_TRANSITIONS.get(self.status, set())


class OrderResponse(BaseModel):
    id: UUID
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    status: OrderStatus
    strategy: RoutingStrategy
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    rejection_reason: str | None = None
    child_orders: list[ChildOrder] = []
    routing_decision: dict | None = None
    submitted_by: str | None = None
    parent_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @classmethod
    def from_order(cls, order: Order) -> "OrderResponse":
        return cls(
            submitted_by=order.submitted_by,
            parent_id=order.parent_id,
            updated_at=order.updated_at,
            id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            status=order.status,
            strategy=order.strategy,
            filled_quantity=order.filled_quantity,
            avg_fill_price=order.avg_fill_price,
            rejection_reason=order.rejection_reason,
            child_orders=order.child_orders,
            routing_decision=order.routing_decision,
            created_at=order.created_at,
        )


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
