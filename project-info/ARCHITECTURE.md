# ARCHITECTURE.md — System Architecture Deep Dive

---

## 1. Design Philosophy

Four principles govern every architectural decision in this system. They are ordered by priority — when two principles conflict, the higher-priority one wins.

**Principle 1 — Separation of concerns at module boundaries.** Market data handling does not know about routing logic. Routing logic does not know about exchange protocols. Risk checks do not know about order representation details. Each subsystem has a defined interface contract. This makes the system testable, replaceable, and understandable.

**Principle 2 — Fail-safe state management.** Every order that enters the system must have a recoverable, auditable trail. State corruption is more dangerous than latency. The order state machine is deterministic — every transition is explicitly allowed or rejected.

**Principle 3 — Determinism over cleverness.** Complex heuristics that behave unpredictably under edge cases are a liability. Clear, predictable rules with well-defined failure modes are preferred. The routing algorithm is deterministic for the same inputs.

**Principle 4 — Latency awareness on the critical path.** The path from receiving an order to sending a child order to a venue should be as short as possible within our Python implementation. We don't achieve microsecond latency (that requires C++), but we minimize unnecessary blocking, use async I/O everywhere, and cache aggressively.

---

## 2. System Layers

The system is organized into four distinct layers, each with clear responsibilities and communication patterns.

### Layer 1 — Frontend (Presentation)

The React + TypeScript frontend provides the operator/trader interface. It communicates with the backend exclusively through the API Gateway via REST (for commands/queries) and a single multiplexed WebSocket (for real-time streaming).

The frontend NEVER talks directly to venue servers, database, or internal services. Everything goes through the gateway.

### Layer 2 — API Gateway

A single FastAPI application that serves as the entry point for all external communication. Responsibilities: authentication, rate limiting, request validation, routing to internal services, WebSocket connection management, and response serialization.

The gateway is the ONLY service with a public-facing port. Everything behind it is internal.

### Layer 3 — Platform Services

The core business logic layer. Contains seven services that work together:

```
┌─────────────────────────────────────────────────────────┐
│                   PLATFORM SERVICES                      │
│                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │ Market Data  │───▶│  Routing    │───▶│ Venue       │  │
│  │ Aggregator   │    │  Engine     │    │ Adapters    │  │
│  └──────┬──────┘    └──────┬──────┘    └─────────────┘  │
│         │                  │                             │
│         ▼                  ▼                             │
│  ┌─────────────┐    ┌─────────────┐                     │
│  │ Venue Health │    │ Order State │                     │
│  │ Monitor      │    │ Manager     │                     │
│  └──────┬──────┘    └─────────────┘                     │
│         │                                                │
│         ▼                                                │
│  ┌─────────────┐    ┌─────────────┐                     │
│  │ Risk Engine  │◀──▶│ RAFT        │                     │
│  │ + Policy     │    │ Consensus   │                     │
│  └─────────────┘    └─────────────┘                     │
└─────────────────────────────────────────────────────────┘
```

### Layer 4 — Venue Simulators

Five independent FastAPI servers, each simulating a trading exchange with its own order book, matching engine, price simulation (GBM), and configurable personality profile.

---

## 3. Subsystem Specifications

### 3.1 Market Data Aggregator

**Purpose:** Collect real-time price quotes from all 5 venues and maintain a consolidated view (NBBO — National Best Bid and Offer).

**How it works:**
1. A background task polls each venue's `/quote` endpoint every 100ms (configurable)
2. Each venue returns: `bid_price`, `ask_price`, `bid_size`, `ask_size`, `last_price`, `volume`, `timestamp`
3. The aggregator maintains an in-memory snapshot of all venues' quotes
4. NBBO is computed: best bid = max(all venue bids), best ask = min(all venue asks)
5. Stale data detection: if a venue's quote timestamp is older than a configurable threshold (e.g., 2 seconds), that venue is marked stale and excluded from NBBO

**Data flow:**
```
Venue 1 ──/quote──▶ ┌─────────────────┐
Venue 2 ──/quote──▶ │  Market Data     │ ──NBBO──▶ Routing Engine
Venue 3 ──/quote──▶ │  Aggregator      │ ──quotes─▶ WebSocket (frontend)
Venue 4 ──/quote──▶ │                  │ ──health─▶ Venue Health Monitor
Venue 5 ──/quote──▶ └─────────────────┘
```

**Key interfaces:**
```python
class MarketDataAggregator:
    async def get_nbbo(self, symbol: str) -> NBBO
    async def get_venue_quote(self, venue_id: str, symbol: str) -> VenueQuote
    async def get_all_quotes(self, symbol: str) -> dict[str, VenueQuote]
    async def is_data_fresh(self, venue_id: str) -> bool
```

**Storage:** Redis — quotes are cached with TTL matching the staleness threshold. This allows the routing engine to read without blocking on venue polling.

---

### 3.2 Routing Engine

**Purpose:** Given an order, determine which venues to route to, in what quantity, at what price, and in what sequence.

**Core algorithm: Best Price Strategy**

```
INPUT:  Order(symbol, side, quantity, order_type)
OUTPUT: List[ChildOrder(venue_id, quantity, price)]

1. Fetch NBBO snapshot from Market Data Aggregator
2. Filter out:
   - Venues with stale data (data_age > staleness_threshold)
   - Venues that are BLACKLISTED (from Risk Engine)
   - Venues that are DISCONNECTED (from Venue Health Monitor)
   - Venues that fail pre-trade risk check for this order
3. For BUY orders: sort remaining venues by ask_price ASC (cheapest first)
   For SELL orders: sort remaining venues by bid_price DESC (highest first)
4. Walk the sorted list, allocating quantity:
   For each venue:
     allocatable = min(remaining_qty, venue_available_size)
     create ChildOrder(venue, allocatable, venue_price)
     remaining_qty -= allocatable
     if remaining_qty == 0: break
5. If remaining_qty > 0: either reject order or queue remainder
6. Return list of ChildOrders
```

**Routing strategies (interface-based, pluggable):**

| Strategy | Description | When Used |
|---|---|---|
| BestPrice | Route to venue with best price, fill greedily | Default for all orders |
| LiquiditySweep | Hit all venues simultaneously for large orders | Orders > sweep_threshold |
| VWAP | Slice order over time to match volume profile | Time-sensitive large orders |

**Key interfaces:**
```python
class IRoutingStrategy(ABC):
    @abstractmethod
    async def compute_plan(
        self,
        order: Order,
        market_snapshot: dict[str, VenueQuote],
        venue_health: dict[str, VenueHealthStatus],
        risk_limits: RiskProfile
    ) -> RoutingPlan

class RoutingEngine:
    async def route_order(self, order: Order) -> RoutingResult
    async def reroute_on_reject(self, child_order: ChildOrder, reject_reason: str) -> RoutingResult
```

---

### 3.3 Risk Engine

**Purpose:** Enforce pre-trade checks, maintain position/exposure tracking, manage kill switch, and apply policy rules.

**Pre-trade risk checks (executed in order, fail-fast):**

| Check | Description | Cost |
|---|---|---|
| Kill Switch | Is the system in emergency halt? | 1 atomic read |
| Symbol Restriction | Is this symbol allowed for trading? | Hash lookup |
| Order Size Limit | Does qty exceed max single-order size? | Comparison |
| Rate Limit | Too many orders in the last N seconds? | Counter check |
| Position Limit | Would this order exceed max position in this symbol? | Position lookup |
| Notional Exposure | Would this order exceed max dollar exposure? | Multiplication + comparison |
| Venue Restriction | Is the target venue allowed for this order? | Set membership |

**Kill switch behavior:**
1. When activated: all new orders are immediately rejected
2. Cancel requests are sent to every venue for every non-terminal order
3. Activation timestamp, reason, and operator are written to audit log
4. Kill switch state is replicated via RAFT to all platform nodes
5. Deactivation requires explicit operator command (two-step confirmation)

**Key interfaces:**
```python
class RiskEngine:
    async def pre_trade_check(self, order: Order, target_venue: str) -> RiskCheckResult
    async def on_fill(self, order_id: str, filled_qty: float, fill_price: float, side: str) -> None
    async def on_cancel(self, order_id: str) -> None
    async def activate_kill_switch(self, reason: str, operator: str) -> None
    async def deactivate_kill_switch(self, operator: str) -> None
    def kill_switch_active(self) -> bool
    async def update_limits(self, new_limits: RiskProfile) -> None
```

---

### 3.4 Order State Manager

**Purpose:** Track every order through its deterministic lifecycle.

**Order state machine:**

```
                    ┌──────────┐
                    │  PENDING  │ ← Order received, not yet validated
                    └─────┬────┘
                          │ validate()
                          ▼
                    ┌──────────┐
              ┌─────│ VALIDATED │
              │     └─────┬────┘
              │           │ risk_check()
     reject() │           ▼
              │     ┌──────────┐
              │     │ APPROVED  │ ← Passed all risk checks
              │     └─────┬────┘
              │           │ route()
              ▼           ▼
        ┌──────────┐ ┌──────────┐
        │ REJECTED  │ │ ROUTING  │ ← Routing engine computing plan
        └──────────┘ └─────┬────┘
                          │ send_to_venues()
                          ▼
                    ┌──────────┐
              ┌─────│ WORKING  │ ← Child orders live at venues
              │     └─────┬────┘
              │           │
     cancel() │     ┌─────┴─────┐
              │     │           │
              │     ▼           ▼
              │ ┌──────────┐ ┌──────────────┐
              │ │  FILLED  │ │ PARTIALLY    │──── cancel() ───▶ CANCELLED
              │ └──────────┘ │ FILLED       │
              │              └──────────────┘
              ▼
        ┌──────────┐
        │CANCELLED │
        └──────────┘
```

**Transition rules (enforced, not suggested):**

| From | Allowed Transitions |
|---|---|
| PENDING | VALIDATED, REJECTED |
| VALIDATED | APPROVED, REJECTED |
| APPROVED | ROUTING, REJECTED |
| ROUTING | WORKING, REJECTED |
| WORKING | FILLED, PARTIALLY_FILLED, CANCELLED |
| PARTIALLY_FILLED | FILLED, CANCELLED |
| FILLED | (terminal) |
| REJECTED | (terminal) |
| CANCELLED | (terminal) |

Any transition not listed is **illegal** and will be rejected with an error log.

---

### 3.5 Venue Health Monitor

**Purpose:** Continuously assess the health of each venue and provide health status to the routing engine and risk engine.

**Metrics tracked per venue:**

| Metric | How Measured | Healthy Threshold |
|---|---|---|
| Latency (ms) | Round-trip time of health check + quote requests | < 50ms (P95) |
| Fill Rate (%) | Fills / orders sent in last 5 minutes | > 80% |
| Reject Rate (%) | Rejects / orders sent in last 5 minutes | < 10% |
| Uptime (%) | Successful health checks / total checks | > 99% |
| Error Rate | HTTP errors + timeout in last 5 minutes | < 5% |
| Data Freshness | Age of latest quote | < 2 seconds |

**Health status levels:**

| Status | Color | Meaning | Routing Impact |
|---|---|---|---|
| HEALTHY | Green | All metrics within thresholds | Full routing allowed |
| DEGRADED | Yellow | 1-2 metrics outside thresholds | Routing with latency penalty |
| CRITICAL | Red | 3+ metrics outside or any catastrophic failure | Routing suspended |
| BLACKLISTED | Black | Manually or policy-engine blacklisted | No routing at all |
| DISCONNECTED | Gray | Cannot reach venue | No routing at all |

**Health scoring formula:**
```
health_score = (
    w_latency    * normalize(latency, 0, max_latency) +
    w_fill_rate  * fill_rate +
    w_reject     * (1 - reject_rate) +
    w_uptime     * uptime +
    w_freshness  * normalize(data_freshness, 0, max_staleness)
) / sum_of_weights

# Score range: 0.0 (worst) to 1.0 (best)
# HEALTHY:     score >= 0.8
# DEGRADED:    0.5 <= score < 0.8
# CRITICAL:    score < 0.5
```

---

### 3.6 RAFT Consensus Cluster

**Purpose:** Ensure all platform nodes agree on critical risk state: venue blacklist decisions, kill switch status, and risk parameter updates.

**What is replicated via RAFT:**
- Venue blacklist/unblacklist decisions
- Kill switch activation/deactivation
- Risk limit parameter changes
- Policy engine rule updates

**What is NOT replicated:**
- Market data (too high frequency, each node collects independently)
- Individual order state (managed by the Order State Manager, persisted in PostgreSQL)
- Metrics/logs (each node writes its own)

**Cluster configuration:**
- 3 nodes (tolerates 1 failure)
- Leader election timeout: 150-300ms (randomized)
- Heartbeat interval: 50ms
- Log entries are JSON-serialized commands

**RAFT node states:**
```
FOLLOWER ──(election timeout)──▶ CANDIDATE ──(majority votes)──▶ LEADER
    ▲                                │                              │
    │                                │ (loses election)             │
    │                                ▼                              │
    └────────────────────────────FOLLOWER◀──(discovers new leader)──┘
```

---

### 3.7 Policy Engine

**Purpose:** Evaluate configurable rules to determine automated actions (blacklist, throttle, alert) based on venue health and risk metrics.

**Rule structure:**
```python
class PolicyRule:
    name: str                          # "auto_blacklist_high_reject"
    condition: str                     # "venue.reject_rate > 0.3 AND venue.reject_rate_trend == 'increasing'"
    action: PolicyAction               # BLACKLIST, THROTTLE, ALERT, SLOWDOWN
    cooldown_seconds: int              # Don't re-trigger for N seconds after firing
    requires_consensus: bool           # Must this action go through RAFT?
    severity: Severity                 # INFO, WARNING, CRITICAL
```

**Built-in policies:**

| Rule | Condition | Action |
|---|---|---|
| Auto-blacklist high reject | reject_rate > 30% for 2+ minutes | BLACKLIST (via RAFT) |
| Auto-blacklist high latency | P95 latency > 200ms for 1+ minute | BLACKLIST (via RAFT) |
| Throttle degraded venue | health_score < 0.5 | THROTTLE (50% rate reduction) |
| Alert on anomaly | anomaly_score > 3σ | ALERT (dashboard notification) |
| Kill switch on cascade | 3+ venues CRITICAL simultaneously | KILL_SWITCH (via RAFT) |

---

## 4. Data Flow — Complete Order Lifecycle

```
1. Order submitted via REST POST /api/orders
   │
2. Gateway validates request schema (Pydantic)
   │
3. Order State Manager: PENDING → VALIDATED
   │
4. Risk Engine: pre_trade_check()
   │── FAIL → Order State: REJECTED, return error
   │── PASS ↓
   │
5. Order State Manager: VALIDATED → APPROVED → ROUTING
   │
6. Routing Engine: compute routing plan
   │  a. Fetch NBBO from Market Data Aggregator
   │  b. Filter venues (blacklist, health, staleness)
   │  c. Apply strategy (BestPrice/LiquiditySweep)
   │  d. Generate child orders
   │
7. Send child orders to venue(s) via REST POST /execute-order
   │
8. Order State Manager: ROUTING → WORKING
   │
9. Venue responds with execution report (fill/partial/reject)
   │
10. Execution Report Handler processes response:
    │── FILL → update position, Order State: FILLED
    │── PARTIAL → update position, Order State: PARTIALLY_FILLED
    │── REJECT → attempt reroute or Order State: REJECTED
    │
11. WebSocket pushes state update to frontend
    │
12. Audit log records entire lifecycle
```

---

## 5. Communication Patterns

### Frontend ↔ Gateway

| Pattern | Protocol | Use Case |
|---|---|---|
| Request-Response | REST (HTTP) | Submit order, fetch data, admin actions |
| Real-time stream | WebSocket | Live quotes, order updates, alerts, venue health |

### Gateway ↔ Services

| Pattern | Protocol | Use Case |
|---|---|---|
| Direct call | In-process async Python | All service-to-service within the gateway process |
| Pub/Sub | Redis pub/sub | Async events (order filled, venue status changed) |

For the POC, all platform services run in the same process as the FastAPI gateway. They are structured as separate Python modules with clean interfaces so they can be extracted into separate services later if needed.

### Gateway ↔ Venues

| Pattern | Protocol | Use Case |
|---|---|---|
| Request-Response | REST (HTTP) | Health check, quote, order execution |
| Polling | HTTP (periodic) | Market data collection (100ms interval) |

---

## 6. Security Model (POC Scope)

| Concern | Implementation |
|---|---|
| Authentication | JWT tokens — login returns access + refresh token |
| Authorization | Role-based (TRADER, RISK_MANAGER, ADMIN) |
| CORS | Configured for frontend origin only |
| Rate Limiting | Per-user, per-endpoint, configurable |
| Input Validation | Pydantic v2 on every endpoint |
| Audit Trail | Every state change logged with timestamp, user, reason |

---

## 7. Observability

| Component | Tool | Purpose |
|---|---|---|
| Structured Logs | structlog (JSON) | Every order event, risk check, routing decision |
| Metrics | Prometheus-compatible counters/gauges | Latency histograms, order counts, fill rates |
| Dashboard | Frontend Risk Manager screen | Real-time visualization |
| Health Checks | `/health` endpoint per service | Liveness + readiness probes |
| Audit Log | PostgreSQL `audit_logs` table | Tamper-evident event record |
