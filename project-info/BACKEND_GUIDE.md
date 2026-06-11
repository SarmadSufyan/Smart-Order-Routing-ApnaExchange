# BACKEND_GUIDE.md — Backend Services Deep Dive

---

## 1. Overview

The backend is a Python FastAPI application structured as a modular monolith. All services run in the same process but are organized as independent modules with clean interfaces. This allows us to extract them into separate microservices later without refactoring business logic.

**Runtime:** Python 3.11+ (async/await everywhere)
**Framework:** FastAPI (async, auto-OpenAPI, Pydantic integration)
**Database:** PostgreSQL 16 (persistent state) + Redis 7 (cache, pub/sub, real-time)
**Process model:** Single process, multi-coroutine (asyncio event loop)

---

## 2. Gateway Layer

The gateway is the only externally-accessible service. It handles HTTP and WebSocket connections, validates inputs, and delegates to internal services.

### File Layout

```
backend/gateway/
├── main.py                    # FastAPI app creation, lifespan events
├── config.py                  # Settings from environment / .env
├── dependencies.py            # Dependency injection (services, DB)
├── middleware/
│   ├── auth.py                # JWT token verification
│   ├── cors.py                # CORS configuration
│   ├── rate_limit.py          # Per-user rate limiting (Redis-backed)
│   └── error_handler.py       # Global exception → HTTP response mapping
├── routers/
│   ├── orders.py              # POST /api/orders, GET /api/orders, etc.
│   ├── venues.py              # GET /api/venues, GET /api/venues/{id}
│   ├── market_data.py         # GET /api/market-data/nbbo, quotes
│   ├── risk.py                # GET /api/risk/status, POST /api/risk/kill-switch
│   ├── routing.py             # GET /api/routing/status, strategies
│   └── admin.py               # POST /api/admin/degrade, recover
└── websocket/
    └── ws_manager.py          # WebSocket connection manager + message routing
```

### App Lifecycle (main.py)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    await init_database()                    # PostgreSQL connection pool
    await init_redis()                       # Redis connection
    await start_market_data_collector()      # Background task: poll venues
    await start_venue_health_monitor()       # Background task: health checks
    await start_raft_cluster()               # RAFT node initialization
    yield
    # SHUTDOWN
    await stop_all_background_tasks()
    await close_database()
    await close_redis()

app = FastAPI(
    title="Apna Exchange — DEIRCP API",
    version="1.0.0",
    lifespan=lifespan
)
```

### Router Pattern

Every router follows the same pattern: thin handler → service call → response model.

```python
# routers/orders.py
from fastapi import APIRouter, Depends, HTTPException
from backend.services.routing_engine.engine import RoutingEngine
from backend.services.order_state.order_manager import OrderManager
from backend.shared.models.order import OrderCreate, OrderResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])

@router.post("/", response_model=OrderResponse, status_code=201)
async def submit_order(
    order_in: OrderCreate,
    order_manager: OrderManager = Depends(get_order_manager),
    routing_engine: RoutingEngine = Depends(get_routing_engine),
):
    """Submit a new order for routing and execution."""
    # 1. Create order record
    order = await order_manager.create_order(order_in)

    # 2. Route order (includes risk check internally)
    result = await routing_engine.route_order(order)

    # 3. Return response
    if result.status == "REJECTED":
        raise HTTPException(status_code=400, detail=result.rejection_reason)
    return OrderResponse.from_order(order, result)
```

---

## 3. Shared Models

All data models live in `backend/shared/models/` and are used by every service. They are Pydantic v2 models with strict validation.

### Core Models

```python
# shared/models/order.py
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from uuid import UUID, uuid4

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

class OrderCreate(BaseModel):
    """Input model for creating a new order."""
    symbol: str = Field(..., min_length=1, max_length=20, examples=["AAPL"])
    side: OrderSide
    quantity: float = Field(..., gt=0, examples=[100.0])
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(None, gt=0)
    strategy: str = Field("best_price", examples=["best_price", "liquidity_sweep"])

class Order(BaseModel):
    """Full order model with all lifecycle fields."""
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    limit_price: float | None = None
    strategy: str = "best_price"
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    rejection_reason: str | None = None
    child_orders: list["ChildOrder"] = []

class ChildOrder(BaseModel):
    """Order slice sent to a specific venue."""
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
```

```python
# shared/models/venue.py
from pydantic import BaseModel
from enum import Enum

class VenueStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    BLACKLISTED = "BLACKLISTED"
    DISCONNECTED = "DISCONNECTED"

class VenueProfile(BaseModel):
    """Static venue configuration."""
    id: str                          # "V1", "V2", etc.
    name: str                        # "AlphaExchange"
    cloud: str                       # "AWS", "GCP", "Azure"
    host: str                        # "localhost"
    port: int                        # 8001
    base_latency_ms: float           # Expected latency
    personality: str                 # "stable", "fast", "degraded", etc.

class VenueHealth(BaseModel):
    """Dynamic venue health status."""
    venue_id: str
    status: VenueStatus
    health_score: float              # 0.0 to 1.0
    latency_ms: float                # Current P95 latency
    fill_rate: float                 # Last 5 min fill rate
    reject_rate: float               # Last 5 min reject rate
    uptime: float                    # Lifetime uptime percentage
    last_checked: str                # ISO timestamp
    error_count: int                 # Errors in last 5 minutes
```

```python
# shared/models/market_data.py
from pydantic import BaseModel

class VenueQuote(BaseModel):
    """Real-time quote from a single venue."""
    venue_id: str
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    last_price: float
    volume: float
    timestamp: str
    is_stale: bool = False

class NBBO(BaseModel):
    """National Best Bid and Offer across all venues."""
    symbol: str
    best_bid: float
    best_bid_venue: str
    best_bid_size: float
    best_ask: float
    best_ask_venue: str
    best_ask_size: float
    spread: float
    timestamp: str
    venue_quotes: dict[str, VenueQuote]
```

```python
# shared/models/risk.py
from pydantic import BaseModel
from enum import Enum

class RiskCheckResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_KILL_SWITCH = "REJECTED_KILL_SWITCH"
    REJECTED_SIZE_LIMIT = "REJECTED_SIZE_LIMIT"
    REJECTED_RATE_LIMIT = "REJECTED_RATE_LIMIT"
    REJECTED_POSITION_LIMIT = "REJECTED_POSITION_LIMIT"
    REJECTED_NOTIONAL_LIMIT = "REJECTED_NOTIONAL_LIMIT"
    REJECTED_VENUE_RESTRICTED = "REJECTED_VENUE_RESTRICTED"
    REJECTED_SYMBOL_RESTRICTED = "REJECTED_SYMBOL_RESTRICTED"

class RiskProfile(BaseModel):
    """Configurable risk limits."""
    max_order_size: float = 10000.0
    max_position_per_symbol: float = 50000.0
    max_notional_exposure: float = 1000000.0
    max_orders_per_second: int = 50
    restricted_symbols: list[str] = []
    restricted_venues: list[str] = []

class KillSwitchStatus(BaseModel):
    active: bool
    activated_at: str | None = None
    activated_by: str | None = None
    reason: str | None = None
    orders_cancelled: int = 0
```

---

## 4. Service Patterns

Every service follows these patterns:

### Constructor Injection

```python
class RoutingEngine:
    def __init__(
        self,
        market_data: MarketDataAggregator,
        risk_engine: RiskEngine,
        venue_monitor: VenueHealthMonitor,
        order_manager: OrderManager,
    ):
        self._market_data = market_data
        self._risk_engine = risk_engine
        self._venue_monitor = venue_monitor
        self._order_manager = order_manager
```

### Result Objects (Not Exceptions for Business Logic)

```python
class RoutingResult(BaseModel):
    status: str                    # "SUCCESS", "PARTIAL", "REJECTED"
    child_orders: list[ChildOrder]
    rejection_reason: str | None = None
    routing_time_ms: float
```

### Event Publishing

```python
# After any significant state change, publish an event
await event_bus.publish("order.filled", {
    "order_id": str(order.id),
    "venue_id": child.venue_id,
    "fill_price": child.fill_price,
    "fill_qty": child.filled_quantity,
    "timestamp": datetime.utcnow().isoformat()
})
```

---

## 5. WebSocket Protocol

A single WebSocket connection at `ws://localhost:8000/ws` carries all real-time data. Messages are JSON objects with a `type` field for routing.

### Message Types (Server → Client)

```json
{ "type": "venue_health",    "data": { "venue_id": "V1", "status": "HEALTHY", "health_score": 0.95, ... } }
{ "type": "market_data",     "data": { "symbol": "AAPL", "venue_id": "V1", "bid": 150.25, "ask": 150.30, ... } }
{ "type": "nbbo_update",     "data": { "symbol": "AAPL", "best_bid": 150.25, "best_ask": 150.28, ... } }
{ "type": "order_update",    "data": { "order_id": "...", "status": "FILLED", "fill_price": 150.27, ... } }
{ "type": "risk_alert",      "data": { "severity": "WARNING", "message": "V3 reject rate exceeds 30%", ... } }
{ "type": "kill_switch",     "data": { "active": true, "reason": "Manual activation", "operator": "admin" } }
{ "type": "execution_report","data": { "order_id": "...", "venue_id": "V2", "exec_type": "FILL", ... } }
```

### Message Types (Client → Server)

```json
{ "type": "subscribe",   "channels": ["venue_health", "market_data", "order_update"] }
{ "type": "unsubscribe", "channels": ["market_data"] }
{ "type": "ping" }
```

### WebSocket Manager

```python
class WebSocketManager:
    """Manages all WebSocket connections and message broadcasting."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}      # connection_id → socket
        self._subscriptions: dict[str, set[str]] = {}     # channel → set of connection_ids

    async def connect(self, websocket: WebSocket) -> str:
        """Accept connection, return connection_id."""

    async def disconnect(self, connection_id: str) -> None:
        """Remove connection and all its subscriptions."""

    async def broadcast(self, channel: str, message: dict) -> None:
        """Send message to all subscribers of a channel."""

    async def send_personal(self, connection_id: str, message: dict) -> None:
        """Send message to a specific connection."""
```

---

## 6. Error Handling

### Domain Exceptions

```python
# shared/exceptions.py

class DomainError(Exception):
    """Base class for all domain errors."""
    def __init__(self, message: str, code: str):
        self.message = message
        self.code = code

class OrderNotFoundError(DomainError):
    def __init__(self, order_id: str):
        super().__init__(f"Order {order_id} not found", "ORDER_NOT_FOUND")

class VenueUnavailableError(DomainError):
    def __init__(self, venue_id: str):
        super().__init__(f"Venue {venue_id} is unavailable", "VENUE_UNAVAILABLE")

class InvalidStateTransitionError(DomainError):
    def __init__(self, order_id: str, from_state: str, to_state: str):
        super().__init__(
            f"Invalid transition {from_state} → {to_state} for order {order_id}",
            "INVALID_STATE_TRANSITION"
        )

class RiskCheckFailedError(DomainError):
    def __init__(self, reason: str):
        super().__init__(f"Risk check failed: {reason}", "RISK_CHECK_FAILED")

class KillSwitchActiveError(DomainError):
    def __init__(self):
        super().__init__("Kill switch is active — all orders rejected", "KILL_SWITCH_ACTIVE")
```

### Global Error Handler (Middleware)

```python
@app.exception_handler(DomainError)
async def domain_error_handler(request, exc: DomainError):
    status_map = {
        "ORDER_NOT_FOUND": 404,
        "VENUE_UNAVAILABLE": 503,
        "INVALID_STATE_TRANSITION": 409,
        "RISK_CHECK_FAILED": 400,
        "KILL_SWITCH_ACTIVE": 503,
    }
    return JSONResponse(
        status_code=status_map.get(exc.code, 500),
        content={"error": exc.code, "message": exc.message}
    )
```

---

## 7. Background Tasks

These tasks run continuously alongside the FastAPI server:

| Task | Interval | Purpose |
|---|---|---|
| Market Data Collector | 100ms | Poll all 5 venues for quotes |
| Venue Health Checker | 1s | Check venue health endpoints |
| Health Score Computer | 5s | Recompute health scores from metrics |
| RAFT Heartbeat | 50ms | RAFT leader heartbeat (when leader) |
| WebSocket Broadcaster | Event-driven | Push updates to subscribed clients |
| Metrics Aggregator | 10s | Aggregate and store metrics |
| Stale Data Cleanup | 30s | Mark stale quotes, clean expired cache |

```python
# Background task pattern
async def market_data_collector_task():
    """Continuously poll venues for market data."""
    while True:
        for venue in VENUE_CONFIGS:
            try:
                quote = await fetch_venue_quote(venue)
                await aggregator.update_quote(venue.id, quote)
                await ws_manager.broadcast("market_data", quote.model_dump())
            except Exception as e:
                logger.error("venue_poll_failed", venue_id=venue.id, error=str(e))
        await asyncio.sleep(0.1)  # 100ms
```

---

## 8. Configuration

All configuration comes from environment variables, loaded via Pydantic Settings:

```python
# gateway/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Application
    app_name: str = "Apna Exchange"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Database
    postgres_url: str = "postgresql+asyncpg://user:pass@localhost:5432/apna_exchange"
    redis_url: str = "redis://localhost:6379/0"

    # Venues
    venue_v1_url: str = "http://localhost:8001"
    venue_v2_url: str = "http://localhost:8002"
    venue_v3_url: str = "http://localhost:8003"
    venue_v4_url: str = "http://localhost:8004"
    venue_v5_url: str = "http://localhost:8005"

    # Risk Defaults
    default_max_order_size: float = 10000.0
    default_max_position: float = 50000.0
    default_max_notional: float = 1000000.0
    default_max_orders_per_second: int = 50

    # Market Data
    market_data_poll_interval_ms: int = 100
    data_staleness_threshold_ms: int = 2000

    # RAFT
    raft_node_id: int = 1
    raft_cluster_nodes: str = "localhost:9001,localhost:9002,localhost:9003"
    raft_election_timeout_ms: int = 300
    raft_heartbeat_interval_ms: int = 50

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
```

---

## 9. Logging

All logging uses `structlog` for structured JSON output.

```python
# shared/utils/logger.py
import structlog

def get_logger(module_name: str):
    return structlog.get_logger(module=module_name)

# Usage in any service:
logger = get_logger("routing_engine")
logger.info("order_routed",
    order_id=str(order.id),
    strategy="best_price",
    venues_selected=["V1", "V2"],
    routing_time_ms=2.3
)
```

**Output format:**
```json
{
  "timestamp": "2026-06-11T10:30:45.123Z",
  "level": "info",
  "module": "routing_engine",
  "event": "order_routed",
  "order_id": "a1b2c3d4-...",
  "strategy": "best_price",
  "venues_selected": ["V1", "V2"],
  "routing_time_ms": 2.3
}
```
