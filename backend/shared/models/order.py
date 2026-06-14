from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


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
    BEST_PRICE = "best_price"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    VWAP = "vwap"


class OrderCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10, examples=["AAPL"])
    side: OrderSide
    quantity: float = Field(..., gt=0, examples=[100.0])
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(None, gt=0)
    strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE


class ChildOrder(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    parent_order_id: UUID
    venue_id: str
    quantity: float
    price: float
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    fill_price: float = 0.0
    sent_at: datetime | None = None
    filled_at: datetime | None = None


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
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderResponse":
        return cls(
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
