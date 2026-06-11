# DATABASE_SCHEMA.md — PostgreSQL + Redis Data Design

---

## 1. PostgreSQL Schema

### Design Principles

- UUIDs for all primary keys (collision-free across distributed nodes)
- Timestamps in UTC with timezone (`timestamptz`)
- Enum types stored as VARCHAR (for flexibility + readability in queries)
- Indexes on every foreign key and every column used in WHERE/ORDER BY
- Soft deletes (where applicable) via `deleted_at` column
- Audit columns (`created_at`, `updated_at`) on every table

---

### Table: orders

The parent order submitted by the user/OMS.

```sql
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity         DECIMAL(18, 8) NOT NULL CHECK (quantity > 0),
    order_type      VARCHAR(10) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT')),
    limit_price     DECIMAL(18, 8),
    strategy        VARCHAR(30) NOT NULL DEFAULT 'best_price',
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    filled_quantity DECIMAL(18, 8) NOT NULL DEFAULT 0,
    avg_fill_price  DECIMAL(18, 8) NOT NULL DEFAULT 0,
    rejection_reason TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(50)  -- user who submitted the order
);

CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);
CREATE INDEX idx_orders_status_created ON orders(status, created_at DESC);
```

### Table: child_orders

Individual order slices sent to specific venues.

```sql
CREATE TABLE child_orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_order_id UUID NOT NULL REFERENCES orders(id),
    venue_id        VARCHAR(10) NOT NULL,
    quantity        DECIMAL(18, 8) NOT NULL CHECK (quantity > 0),
    price           DECIMAL(18, 8) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    filled_quantity DECIMAL(18, 8) NOT NULL DEFAULT 0,
    fill_price      DECIMAL(18, 8) NOT NULL DEFAULT 0,
    sent_at         TIMESTAMPTZ,
    filled_at       TIMESTAMPTZ,
    rejected_at     TIMESTAMPTZ,
    rejection_reason TEXT,
    venue_order_id  VARCHAR(100), -- venue's internal order ID
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_child_orders_parent ON child_orders(parent_order_id);
CREATE INDEX idx_child_orders_venue ON child_orders(venue_id);
CREATE INDEX idx_child_orders_status ON child_orders(status);
```

### Table: execution_reports

Fill and reject reports from venues.

```sql
CREATE TABLE execution_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    child_order_id  UUID REFERENCES child_orders(id),
    venue_id        VARCHAR(10) NOT NULL,
    exec_type       VARCHAR(20) NOT NULL CHECK (exec_type IN ('NEW_ACK', 'FILL', 'PARTIAL_FILL', 'REJECT', 'CANCEL_ACK')),
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(4) NOT NULL,
    quantity        DECIMAL(18, 8) NOT NULL,
    price           DECIMAL(18, 8),
    venue_latency_ms DECIMAL(10, 3),
    venue_exec_id   VARCHAR(100),
    raw_fix_message TEXT,         -- original FIX message for audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_exec_reports_order ON execution_reports(order_id);
CREATE INDEX idx_exec_reports_venue ON execution_reports(venue_id);
CREATE INDEX idx_exec_reports_type ON execution_reports(exec_type);
CREATE INDEX idx_exec_reports_created ON execution_reports(created_at DESC);
```

### Table: venue_configs

Static venue configuration.

```sql
CREATE TABLE venue_configs (
    id          VARCHAR(10) PRIMARY KEY, -- V1, V2, etc.
    name        VARCHAR(50) NOT NULL,
    cloud       VARCHAR(10) NOT NULL,
    host        VARCHAR(100) NOT NULL,
    port        INTEGER NOT NULL,
    base_latency_ms DECIMAL(10, 3),
    personality VARCHAR(30),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Table: venue_health_history

Historical venue health snapshots for trending.

```sql
CREATE TABLE venue_health_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id    VARCHAR(10) NOT NULL REFERENCES venue_configs(id),
    status      VARCHAR(20) NOT NULL,
    health_score DECIMAL(5, 4) NOT NULL,
    latency_p50_ms DECIMAL(10, 3),
    latency_p95_ms DECIMAL(10, 3),
    latency_p99_ms DECIMAL(10, 3),
    fill_rate   DECIMAL(5, 4),
    reject_rate DECIMAL(5, 4),
    error_count INTEGER DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_venue_health_venue ON venue_health_history(venue_id, recorded_at DESC);
-- Partition by month for performance (if needed)
```

### Table: risk_events

Risk-related events (kill switch, blacklist, limit changes).

```sql
CREATE TABLE risk_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  VARCHAR(30) NOT NULL, -- KILL_SWITCH_ON, KILL_SWITCH_OFF, BLACKLIST, UNBLACKLIST, LIMIT_CHANGE
    venue_id    VARCHAR(10),
    operator    VARCHAR(50),
    reason      TEXT,
    details     JSONB,               -- flexible payload for different event types
    raft_term   INTEGER,             -- RAFT term when this decision was made
    raft_index  INTEGER,             -- RAFT log index
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_risk_events_type ON risk_events(event_type, created_at DESC);
```

### Table: audit_logs

Comprehensive audit trail — every significant system action.

```sql
CREATE TABLE audit_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  VARCHAR(50) NOT NULL,
    entity_type VARCHAR(30) NOT NULL, -- ORDER, VENUE, RISK, SYSTEM
    entity_id   VARCHAR(100),
    before_state JSONB,
    after_state  JSONB,
    operator    VARCHAR(50),
    ip_address  VARCHAR(45),
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_logs_type ON audit_logs(event_type, created_at DESC);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at DESC);
```

### Table: positions

Current position tracking per symbol.

```sql
CREATE TABLE positions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol      VARCHAR(20) NOT NULL UNIQUE,
    net_quantity DECIMAL(18, 8) NOT NULL DEFAULT 0,
    avg_cost    DECIMAL(18, 8) NOT NULL DEFAULT 0,
    notional    DECIMAL(18, 8) NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_positions_symbol ON positions(symbol);
```

### Table: risk_limits

Configurable risk limits (versioned).

```sql
CREATE TABLE risk_limits (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    max_order_size          DECIMAL(18, 8) NOT NULL DEFAULT 10000,
    max_position_per_symbol DECIMAL(18, 8) NOT NULL DEFAULT 50000,
    max_notional_exposure   DECIMAL(18, 8) NOT NULL DEFAULT 1000000,
    max_orders_per_second   INTEGER NOT NULL DEFAULT 50,
    restricted_symbols      TEXT[] DEFAULT '{}',
    restricted_venues       TEXT[] DEFAULT '{}',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_by              VARCHAR(50),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Table: users

Application users for authentication.

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username    VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    name        VARCHAR(100),
    role        VARCHAR(20) NOT NULL DEFAULT 'TRADER' CHECK (role IN ('TRADER', 'RISK_MANAGER', 'ADMIN')),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    last_login  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Table: policy_rules

Configurable policy engine rules.

```sql
CREATE TABLE policy_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    condition_expr  TEXT NOT NULL,        -- Expression to evaluate
    action          VARCHAR(30) NOT NULL, -- BLACKLIST, THROTTLE, ALERT, KILL_SWITCH
    severity        VARCHAR(20) NOT NULL DEFAULT 'WARNING',
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    requires_consensus BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 2. Redis Key Design

Redis is used for real-time state that needs fast read/write access and pub/sub messaging.

### Market Data Cache

```
# Per-venue quote (JSON, TTL = 5 seconds)
market:quote:{venue_id}:{symbol}  →  { bid, ask, bid_size, ask_size, last, volume, ts }

# NBBO (JSON, TTL = 2 seconds)
market:nbbo:{symbol}  →  { best_bid, best_ask, best_bid_venue, best_ask_venue, spread, ts }

# Example:
market:quote:V1:AAPL  →  {"bid":150.22,"ask":150.30,"bid_size":300,"ask_size":250,"last":150.25,"volume":45000,"ts":"..."}
market:nbbo:AAPL      →  {"best_bid":150.25,"best_ask":150.28,"best_bid_venue":"V2","best_ask_venue":"V4","spread":0.03}
```

### Venue Health (Real-Time)

```
# Current health status (JSON, TTL = 10 seconds)
venue:health:{venue_id}  →  { status, health_score, latency_ms, fill_rate, reject_rate, last_checked }

# Latency sliding window (sorted set, score = timestamp)
venue:latency:{venue_id}  →  ZSET { (latency_value, timestamp), ... }

# Order counts for rate calculation (expiring counters)
venue:orders_5min:{venue_id}  →  INT (TTL = 300s)
venue:fills_5min:{venue_id}   →  INT (TTL = 300s)
venue:rejects_5min:{venue_id} →  INT (TTL = 300s)
```

### Risk State

```
# Kill switch status (JSON, no TTL — persistent)
risk:kill_switch  →  { active, activated_at, activated_by, reason }

# Blacklisted venues (SET)
risk:blacklist  →  SET { "V3", ... }

# Rate limiting per user (sliding window counter)
rate_limit:{user_id}:{endpoint}  →  INT (TTL = 1s)

# Position cache (HASH)
risk:positions  →  HASH { "AAPL": "1500", "MSFT": "-200", ... }
```

### Pub/Sub Channels

```
# Real-time event channels
channel:venue_health    →  venue health status changes
channel:market_data     →  new market data quotes
channel:order_update    →  order state transitions
channel:risk_alert      →  risk alerts and warnings
channel:kill_switch     →  kill switch state changes
channel:execution_report →  new execution reports
```

### Session / Auth

```
# Refresh token store (STRING, TTL = 7 days)
auth:refresh:{token_hash}  →  { user_id, role, expires_at }

# Active WebSocket connections (SET)
ws:connections  →  SET { "conn_001", "conn_002", ... }

# Per-connection subscriptions (SET)
ws:subs:{connection_id}  →  SET { "venue_health", "market_data", ... }
```

---

## 3. Migration Strategy

Migrations are managed via `alembic` (SQLAlchemy's migration tool).

```
backend/shared/database/migrations/
├── alembic.ini
├── env.py
└── versions/
    ├── 001_initial_schema.py
    ├── 002_add_venue_health_history.py
    ├── 003_add_policy_rules.py
    └── ...
```

**Commands:**
```bash
# Create new migration
alembic revision --autogenerate -m "add_policy_rules"

# Apply all pending migrations
alembic upgrade head

# Rollback last migration
alembic downgrade -1
```

---

## 4. Seed Data

Initial data loaded on first startup:

```python
# Venue configs
SEED_VENUES = [
    {"id": "V1", "name": "AlphaExchange", "cloud": "AWS", "host": "localhost", "port": 8001, "personality": "stable"},
    {"id": "V2", "name": "BetaLiquidity", "cloud": "GCP", "host": "localhost", "port": 8002, "personality": "fast"},
    {"id": "V3", "name": "GammaMarkets",  "cloud": "Azure", "host": "localhost", "port": 8003, "personality": "degraded"},
    {"id": "V4", "name": "DeltaPrime",    "cloud": "AWS", "host": "localhost", "port": 8004, "personality": "premium"},
    {"id": "V5", "name": "EpsilonPool",   "cloud": "GCP", "host": "localhost", "port": 8005, "personality": "balanced"},
]

# Default users
SEED_USERS = [
    {"username": "admin",    "role": "ADMIN",        "name": "System Administrator"},
    {"username": "trader1",  "role": "TRADER",       "name": "Demo Trader"},
    {"username": "risk_mgr", "role": "RISK_MANAGER", "name": "Risk Manager"},
]

# Default risk limits
SEED_RISK_LIMITS = {
    "max_order_size": 10000,
    "max_position_per_symbol": 50000,
    "max_notional_exposure": 1000000,
    "max_orders_per_second": 50,
}

# Default policy rules
SEED_POLICIES = [
    {"name": "auto_blacklist_high_reject", "condition_expr": "venue.reject_rate > 0.3", "action": "BLACKLIST", "requires_consensus": True},
    {"name": "auto_blacklist_high_latency", "condition_expr": "venue.latency_p95 > 200", "action": "BLACKLIST", "requires_consensus": True},
    {"name": "alert_degraded_venue", "condition_expr": "venue.health_score < 0.5", "action": "ALERT", "requires_consensus": False},
]
```
