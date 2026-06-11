# API_SPECIFICATION.md — REST + WebSocket API Contracts

---

## 1. Base Configuration

| Property | Value |
|---|---|
| Base URL | `http://localhost:8000` |
| WebSocket | `ws://localhost:8000/ws` |
| Content-Type | `application/json` |
| Authentication | Bearer token (JWT) in `Authorization` header |
| Versioning | URL-based: `/api/v1/...` (future), currently `/api/...` |

---

## 2. Authentication

### POST /api/auth/login

Authenticate and receive JWT tokens.

**Request:**
```json
{
  "username": "admin",
  "password": "password"
}
```

**Response (200):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "user_001",
    "username": "admin",
    "role": "ADMIN",
    "name": "System Administrator"
  }
}
```

**Roles:** `TRADER`, `RISK_MANAGER`, `ADMIN`

### POST /api/auth/refresh

**Request:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

**Response (200):** Same as login response with new tokens.

---

## 3. Orders

### POST /api/orders

Submit a new order for routing and execution.

**Request:**
```json
{
  "symbol": "AAPL",
  "side": "BUY",
  "quantity": 500,
  "order_type": "MARKET",
  "limit_price": null,
  "strategy": "best_price"
}
```

**Response (201):**
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "symbol": "AAPL",
  "side": "BUY",
  "quantity": 500,
  "order_type": "MARKET",
  "status": "ROUTING",
  "strategy": "best_price",
  "child_orders": [
    {
      "id": "child-001",
      "venue_id": "V2",
      "quantity": 300,
      "price": 150.25,
      "status": "WORKING"
    },
    {
      "id": "child-002",
      "venue_id": "V1",
      "quantity": 200,
      "price": 150.27,
      "status": "WORKING"
    }
  ],
  "created_at": "2026-06-11T10:30:45.123Z"
}
```

**Error (400) — Risk check failed:**
```json
{
  "error": "RISK_CHECK_FAILED",
  "message": "Risk check failed: Order size 500 exceeds maximum 100 for symbol AAPL"
}
```

**Error (503) — Kill switch active:**
```json
{
  "error": "KILL_SWITCH_ACTIVE",
  "message": "Kill switch is active — all orders rejected"
}
```

### GET /api/orders

List orders with filtering and pagination.

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| status | string | all | Filter by status (comma-separated) |
| symbol | string | all | Filter by symbol |
| side | string | all | BUY or SELL |
| from_date | ISO datetime | -24h | Start of time range |
| to_date | ISO datetime | now | End of time range |
| page | int | 1 | Page number |
| page_size | int | 50 | Results per page (max 200) |
| sort_by | string | created_at | Sort field |
| sort_order | string | desc | asc or desc |

**Response (200):**
```json
{
  "orders": [ /* array of Order objects */ ],
  "total": 1247,
  "page": 1,
  "page_size": 50,
  "total_pages": 25
}
```

### GET /api/orders/{order_id}

Get a single order with full details including child orders and execution history.

### POST /api/orders/{order_id}/cancel

Cancel an order. Only works for orders in WORKING or PARTIALLY_FILLED status.

**Response (200):**
```json
{
  "id": "a1b2c3d4-...",
  "status": "CANCELLED",
  "cancelled_at": "2026-06-11T10:35:00.000Z",
  "child_orders": [
    { "id": "child-001", "venue_id": "V2", "status": "CANCELLED" },
    { "id": "child-002", "venue_id": "V1", "status": "FILLED", "fill_price": 150.27 }
  ]
}
```

---

## 4. Venues

### GET /api/venues

List all venues with current health status.

**Response (200):**
```json
{
  "venues": [
    {
      "venue_id": "V1",
      "name": "AlphaExchange",
      "cloud": "AWS",
      "status": "HEALTHY",
      "health_score": 0.95,
      "latency_ms": 12.3,
      "fill_rate": 0.94,
      "reject_rate": 0.02,
      "uptime": 0.999,
      "last_checked": "2026-06-11T10:30:45.123Z",
      "error_count": 0
    },
    {
      "venue_id": "V3",
      "name": "GammaMarkets",
      "cloud": "Azure",
      "status": "BLACKLISTED",
      "health_score": 0.15,
      "latency_ms": 156.7,
      "fill_rate": 0.42,
      "reject_rate": 0.38,
      "uptime": 0.87,
      "last_checked": "2026-06-11T10:30:44.789Z",
      "error_count": 12
    }
  ]
}
```

### GET /api/venues/{venue_id}

Detailed venue information including historical metrics.

**Response (200):**
```json
{
  "venue_id": "V1",
  "name": "AlphaExchange",
  "cloud": "AWS",
  "host": "localhost",
  "port": 8001,
  "status": "HEALTHY",
  "health_score": 0.95,
  "current_metrics": {
    "latency_p50_ms": 8.2,
    "latency_p95_ms": 12.3,
    "latency_p99_ms": 18.7,
    "fill_rate": 0.94,
    "reject_rate": 0.02,
    "orders_last_5min": 342,
    "fills_last_5min": 321,
    "rejects_last_5min": 7
  },
  "latency_history": [
    { "timestamp": "2026-06-11T10:25:00Z", "p95_ms": 11.8 },
    { "timestamp": "2026-06-11T10:26:00Z", "p95_ms": 12.1 }
  ]
}
```

### POST /api/venues/{venue_id}/blacklist

Manually blacklist a venue (requires RISK_MANAGER or ADMIN role).

### POST /api/venues/{venue_id}/unblacklist

Remove a venue from blacklist.

---

## 5. Market Data

### GET /api/market-data/nbbo

Get the National Best Bid and Offer.

**Query Parameters:** `symbol` (required)

**Response (200):**
```json
{
  "symbol": "AAPL",
  "best_bid": 150.25,
  "best_bid_venue": "V2",
  "best_bid_size": 500,
  "best_ask": 150.28,
  "best_ask_venue": "V4",
  "best_ask_size": 200,
  "spread": 0.03,
  "timestamp": "2026-06-11T10:30:45.123Z",
  "venue_quotes": {
    "V1": { "bid": 150.22, "ask": 150.30, "bid_size": 300, "ask_size": 250 },
    "V2": { "bid": 150.25, "ask": 150.29, "bid_size": 500, "ask_size": 350 },
    "V4": { "bid": 150.23, "ask": 150.28, "bid_size": 150, "ask_size": 200 },
    "V5": { "bid": 150.20, "ask": 150.32, "bid_size": 800, "ask_size": 600 }
  }
}
```

Note: V3 is excluded (BLACKLISTED).

### GET /api/market-data/quotes

Get all venue quotes for a symbol.

### GET /api/market-data/orderbook

Get order book depth for a specific venue and symbol.

**Query Parameters:** `venue_id`, `symbol`, `depth` (default 10)

---

## 6. Risk Management

### GET /api/risk/status

Get current risk status overview.

**Response (200):**
```json
{
  "kill_switch": {
    "active": false,
    "last_activated": "2026-06-10T15:30:00Z",
    "last_reason": "Manual test activation"
  },
  "positions": {
    "AAPL": { "net_position": 1500, "notional": 225375.00 },
    "MSFT": { "net_position": -200, "notional": -84600.00 }
  },
  "total_notional_exposure": 309975.00,
  "risk_limits": {
    "max_order_size": 10000,
    "max_position_per_symbol": 50000,
    "max_notional_exposure": 1000000,
    "max_orders_per_second": 50
  },
  "recent_checks": [
    {
      "order_id": "abc-123",
      "result": "APPROVED",
      "checks_passed": ["kill_switch", "size_limit", "rate_limit", "position_limit"],
      "timestamp": "2026-06-11T10:30:45Z",
      "latency_us": 45
    }
  ]
}
```

### POST /api/risk/kill-switch/activate

**Request:**
```json
{
  "reason": "Anomalous rejection rate across multiple venues"
}
```

**Response (200):**
```json
{
  "active": true,
  "activated_at": "2026-06-11T10:31:00.000Z",
  "activated_by": "admin",
  "reason": "Anomalous rejection rate across multiple venues",
  "orders_cancelled": 47
}
```

### POST /api/risk/kill-switch/deactivate

Requires two-step confirmation in frontend (modal with typed confirmation).

### PUT /api/risk/limits

Update risk limits (ADMIN only).

**Request:**
```json
{
  "max_order_size": 15000,
  "max_position_per_symbol": 75000,
  "max_notional_exposure": 2000000,
  "max_orders_per_second": 100
}
```

---

## 7. Routing

### GET /api/routing/status

Get routing engine status and strategy information.

**Response (200):**
```json
{
  "active_strategy": "best_price",
  "available_strategies": ["best_price", "liquidity_sweep", "vwap"],
  "routable_venues": ["V1", "V2", "V4", "V5"],
  "excluded_venues": {
    "V3": "BLACKLISTED"
  },
  "stats": {
    "orders_routed_today": 1247,
    "avg_routing_time_ms": 2.3,
    "venue_allocation": {
      "V1": 0.22,
      "V2": 0.35,
      "V4": 0.18,
      "V5": 0.25
    }
  }
}
```

---

## 8. Admin

### POST /api/admin/venues/{venue_id}/degrade

Force a venue into degraded mode (for testing/demo).

### POST /api/admin/venues/{venue_id}/recover

Recover a degraded venue.

### GET /api/admin/metrics

System metrics (Prometheus-compatible format).

### GET /api/health

Health check endpoint (no auth required).

**Response (200):**
```json
{
  "status": "healthy",
  "services": {
    "gateway": "up",
    "market_data": "up",
    "routing_engine": "up",
    "risk_engine": "up",
    "order_state": "up",
    "venue_monitor": "up",
    "raft_cluster": "up",
    "postgres": "up",
    "redis": "up"
  },
  "venues": {
    "V1": "reachable",
    "V2": "reachable",
    "V3": "reachable",
    "V4": "reachable",
    "V5": "reachable"
  },
  "uptime_seconds": 86400
}
```

---

## 9. Execution Reports

### GET /api/execution-reports

List execution reports with filtering.

**Query Parameters:** `order_id`, `venue_id`, `exec_type` (FILL, PARTIAL, REJECT), `from_date`, `to_date`, `page`, `page_size`

**Response (200):**
```json
{
  "reports": [
    {
      "id": "exec-001",
      "order_id": "a1b2c3d4-...",
      "child_order_id": "child-001",
      "venue_id": "V2",
      "exec_type": "FILL",
      "symbol": "AAPL",
      "side": "BUY",
      "quantity": 300,
      "price": 150.25,
      "timestamp": "2026-06-11T10:30:45.567Z",
      "venue_latency_ms": 8.2
    }
  ],
  "total": 2347,
  "page": 1,
  "page_size": 50
}
```

---

## 10. WebSocket Protocol

### Connection

```
ws://localhost:8000/ws?token=<JWT_TOKEN>
```

### Subscribe to Channels

**Client → Server:**
```json
{
  "type": "subscribe",
  "channels": ["venue_health", "market_data", "order_update", "risk_alert", "kill_switch"]
}
```

### Server → Client Messages

See `BACKEND_GUIDE.md` Section 5 for complete message type documentation.

### Heartbeat

**Client → Server** (every 30s):
```json
{ "type": "ping" }
```

**Server → Client:**
```json
{ "type": "pong", "timestamp": "2026-06-11T10:30:45.123Z" }
```

---

## 11. Error Response Format

All errors follow this consistent format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable error description",
  "details": {}
}
```

| HTTP Code | Error Code | Meaning |
|---|---|---|
| 400 | VALIDATION_ERROR | Request body failed Pydantic validation |
| 400 | RISK_CHECK_FAILED | Pre-trade risk check rejected the order |
| 401 | UNAUTHORIZED | Missing or invalid JWT token |
| 403 | FORBIDDEN | Valid token but insufficient role |
| 404 | ORDER_NOT_FOUND | Referenced order does not exist |
| 404 | VENUE_NOT_FOUND | Referenced venue does not exist |
| 409 | INVALID_STATE_TRANSITION | Cannot transition order to requested state |
| 429 | RATE_LIMITED | Too many requests |
| 503 | KILL_SWITCH_ACTIVE | System in emergency halt |
| 503 | VENUE_UNAVAILABLE | Target venue is unreachable |
| 500 | INTERNAL_ERROR | Unexpected server error |
