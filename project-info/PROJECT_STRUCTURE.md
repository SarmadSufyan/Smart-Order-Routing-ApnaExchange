# PROJECT_STRUCTURE.md — Complete Repository Layout

> **Historical document (June 2026).** Written for the first prototype, which routed orders across five simulated stock venues. The project moved to real crypto exchanges in July 2026; this page is kept as the record of milestone M1 and does not describe the current system. Current design: the root [README](../README.md) and the guides listed in [project-info/README.md](README.md).

> Every folder and file in the monorepo, what it does, and why it's there.
> This is the map Claude Code should consult before creating any new file.

---

## Root Directory

```
apna-exchange/
│
├── docs/                          # Project documentation (you are here)
│   ├── CLAUDE.md                  # Master Claude Code reference — READ FIRST
│   ├── README.md                  # Project overview, quick start
│   ├── ARCHITECTURE.md            # System design, subsystems, data flows
│   ├── MILESTONES.md              # FYP phases, timeline, feature tracking
│   ├── BACKEND_GUIDE.md           # Backend services, patterns, models
│   ├── FRONTEND_GUIDE.md          # Frontend architecture, screen map, state
│   ├── API_SPECIFICATION.md       # REST + WebSocket API contracts
│   ├── DATABASE_SCHEMA.md         # PostgreSQL tables + Redis key design
│   ├── ALGORITHMS.md              # Core algorithms with pseudocode
│   ├── DEPLOYMENT.md              # Docker, infrastructure, multi-cloud
│   ├── DEVELOPMENT_SETUP.md       # Local environment setup guide
│   └── PROJECT_STRUCTURE.md       # This file — repo layout reference
│
├── backend/                       # Python FastAPI backend (gateway + platform services)
├── venues/                        # Venue simulator servers (5 exchanges)
├── frontend/                      # React + TypeScript frontend (Figma Make output + custom)
├── infra/                         # Docker, deployment configs, scripts
├── tests/                         # Integration & end-to-end tests
├── scripts/                       # Utility & automation scripts
│
├── .env.example                   # Environment variable template
├── .gitignore                     # Git ignore rules
├── docker-compose.yml             # Full-stack local orchestration
├── Makefile                       # Common dev commands (optional)
└── README.md                      # Root readme (symlinks to docs/README.md or is brief)
```

---

## `/backend` — Platform Services

The backend is a **modular monolith** — all services run inside one FastAPI process but are organized as separate modules with clean interfaces. Any module can be extracted into a standalone microservice later without restructuring.

```
backend/
│
├── gateway/                       # FastAPI application entry point
│   ├── __init__.py
│   ├── main.py                    # App factory, lifespan, CORS, middleware
│   ├── config.py                  # Pydantic Settings — all env vars
│   ├── dependencies.py            # Dependency injection (get_db, get_redis, get_service)
│   └── middleware/
│       ├── __init__.py
│       ├── auth.py                # JWT verification middleware
│       ├── logging.py             # Request/response structured logging
│       └── error_handler.py       # Global exception → JSON response mapping
│
├── routers/                       # API route handlers (thin layer — delegates to services)
│   ├── __init__.py
│   ├── auth.py                    # POST /api/auth/login, /api/auth/refresh
│   ├── orders.py                  # POST /api/orders, GET /api/orders, GET /api/orders/{id}
│   ├── venues.py                  # GET /api/venues, GET /api/venues/{id}, POST blacklist
│   ├── market_data.py             # GET /api/market-data/nbbo, /quotes, /orderbook
│   ├── risk.py                    # GET /api/risk/status, POST kill-switch, PUT limits
│   ├── routing.py                 # GET /api/routing/status
│   ├── admin.py                   # Admin endpoints — degrade, recover, metrics
│   └── websocket.py               # WebSocket /ws — single multiplexed connection
│
├── services/                      # Business logic (the brain)
│   ├── __init__.py
│   ├── market_data/
│   │   ├── __init__.py
│   │   ├── aggregator.py          # Polls venues, computes NBBO, detects staleness
│   │   ├── nbbo.py                # National Best Bid/Offer calculation
│   │   └── venue_poller.py        # HTTP client polling each venue's /quote endpoint
│   │
│   ├── routing/
│   │   ├── __init__.py
│   │   ├── engine.py              # Core routing logic — best price algorithm
│   │   ├── strategies.py          # Strategy interface + implementations (best_price, weighted, vwap)
│   │   └── order_splitter.py      # Splits large orders across venues
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── engine.py              # Pre-trade risk checks pipeline
│   │   ├── checks.py              # Individual check functions (position, notional, rate, etc.)
│   │   ├── kill_switch.py         # Global and per-venue kill switch logic
│   │   └── limits.py              # Risk limit CRUD and evaluation
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── executor.py            # Sends child orders to venues via adapters
│   │   ├── venue_adapter.py       # HTTP client for venue /execute-order endpoints
│   │   └── fill_processor.py      # Processes fills, updates parent order state
│   │
│   ├── venue_health/
│   │   ├── __init__.py
│   │   ├── monitor.py             # Periodic health polling, scoring, status transitions
│   │   ├── scorer.py              # Weighted health score calculation
│   │   └── blacklist.py           # Blacklist management (manual + automatic)
│   │
│   ├── consensus/
│   │   ├── __init__.py
│   │   ├── raft_node.py           # RAFT consensus node implementation
│   │   ├── state_machine.py       # Replicated state machine (risk state, kill switch)
│   │   └── log.py                 # RAFT log entries and persistence
│   │
│   ├── policy/
│   │   ├── __init__.py
│   │   ├── engine.py              # Policy evaluation engine
│   │   └── rules.py               # Built-in policy rules (max order size, symbol restrict, etc.)
│   │
│   └── order_state/
│       ├── __init__.py
│       ├── manager.py             # Order state machine (NEW → ROUTED → FILLED/REJECTED)
│       └── transitions.py         # Valid state transitions and guards
│
├── shared/                        # Shared code across all services
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── order.py               # Order, ChildOrder, OrderStatus enums
│   │   ├── venue.py               # VenueProfile, VenueHealth, VenueQuote
│   │   ├── market_data.py         # NBBO, OrderBook, Trade
│   │   ├── risk.py                # RiskCheckResult, RiskProfile, KillSwitchStatus
│   │   └── execution.py           # ExecutionReport, Fill
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py          # AsyncEngine, async_session_factory
│   │   ├── tables.py              # SQLAlchemy ORM models (all tables)
│   │   ├── seed.py                # Initial data — venues, users, risk limits, policies
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── order_repo.py      # Order CRUD operations
│   │       ├── venue_repo.py      # Venue config and health history CRUD
│   │       ├── risk_repo.py       # Risk events and limits CRUD
│   │       └── audit_repo.py      # Audit log writes
│   │
│   ├── redis/
│   │   ├── __init__.py
│   │   ├── client.py              # Redis connection pool, helper methods
│   │   ├── cache.py               # Market data cache (NBBO, quotes)
│   │   └── pubsub.py              # Pub/Sub for real-time events → WebSocket
│   │
│   ├── websocket/
│   │   ├── __init__.py
│   │   ├── manager.py             # WebSocketManager — connection pool, broadcast
│   │   └── messages.py            # Message type definitions (server→client, client→server)
│   │
│   ├── events/
│   │   ├── __init__.py
│   │   ├── bus.py                 # In-process event bus (publish/subscribe)
│   │   └── types.py               # Event type enums and payloads
│   │
│   ├── exceptions.py              # Domain exception hierarchy
│   ├── logging.py                 # Structured logging setup (structlog)
│   └── utils.py                   # Shared utility functions (timestamps, ID generation)
│
├── tasks/                         # Background tasks
│   ├── __init__.py
│   ├── scheduler.py               # Task scheduler (registers all periodic tasks)
│   ├── market_data_poll.py        # Poll venues every 500ms
│   ├── health_check.py            # Check venue health every 2s
│   ├── stale_order_cleanup.py     # Cancel stuck orders every 30s
│   ├── metrics_aggregation.py     # Aggregate metrics every 10s
│   └── raft_heartbeat.py          # RAFT leader heartbeat every 150ms
│
├── migrations/                    # Alembic database migrations
│   ├── env.py                     # Migration environment config
│   ├── script.py.mako             # Migration template
│   └── versions/
│       ├── 001_initial_schema.py  # Tables: orders, child_orders, venues, etc.
│       ├── 002_add_risk_tables.py # Tables: risk_events, risk_limits, positions
│       └── ...                    # Future migrations
│
├── tests/                         # Backend unit tests
│   ├── __init__.py
│   ├── conftest.py                # Shared fixtures (test DB, test client, mock redis)
│   ├── test_routing_engine.py
│   ├── test_risk_engine.py
│   ├── test_market_data.py
│   ├── test_order_state.py
│   ├── test_venue_health.py
│   └── test_api/
│       ├── test_orders_api.py
│       ├── test_venues_api.py
│       └── test_risk_api.py
│
├── alembic.ini                    # Alembic configuration
├── requirements.txt               # Python dependencies
├── requirements-dev.txt           # Dev-only dependencies (pytest, ruff, etc.)
└── pyproject.toml                 # Python project metadata, ruff config
```

### Key Design Decisions for `/backend`

**Why modular monolith?** For an FYP/POC, running 7+ separate microservices is operational overhead with no benefit. A monolith with clean module boundaries gives us the same separation of concerns, testability, and clarity — and any module can be extracted later by replacing in-process calls with HTTP/gRPC.

**Why `routers/` separate from `services/`?** Routers are thin HTTP handlers that validate input and delegate to services. Services contain all business logic and are framework-agnostic (testable without FastAPI). This means we can swap FastAPI for another framework without touching business logic.

**Why `shared/models/` uses Pydantic, not SQLAlchemy?** Pydantic models are the API contract — what goes in and out. SQLAlchemy models in `shared/database/tables.py` are the persistence layer. Keeping them separate prevents ORM details from leaking into API responses.

---

## `/venues` — Simulated Exchange Servers

Five independent FastAPI servers simulating different trading exchanges. Each has its own personality (latency, spread, fill rates) defined by profiles.

```
venues/
│
├── server/
│   ├── __init__.py
│   ├── venue_app.py               # FastAPI app factory (shared by all 5 venues)
│   ├── config.py                  # Venue-specific settings (loaded from env/profile)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── quote.py               # GET /quote — current bid/ask for a symbol
│   │   ├── orderbook.py           # GET /orderbook — full depth of book
│   │   ├── execute.py             # POST /execute-order — fill simulation
│   │   ├── health.py              # GET /health — venue status
│   │   └── admin.py               # POST /admin/degrade, /admin/recover
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── price_engine.py        # GBM price simulator with regime switching
│   │   ├── orderbook_engine.py    # Synthetic order book generation
│   │   ├── fill_simulator.py      # Simulated fill logic (partial fills, rejects)
│   │   └── latency_model.py       # Artificial latency injection per venue profile
│   │
│   └── profiles/
│       ├── __init__.py
│       ├── base.py                # Base profile dataclass
│       ├── alpha_exchange.py      # V1 — AWS, reliable, moderate spread
│       ├── beta_liquidity.py      # V2 — GCP, best prices, tight spread
│       ├── gamma_markets.py       # V3 — Azure, degraded, high latency (for blacklist demo)
│       ├── delta_prime.py         # V4 — AWS, premium, low latency
│       └── epsilon_pool.py        # V5 — GCP, balanced, moderate everything
│
├── start_all_venues.py            # Launches all 5 venues in subprocesses
├── requirements.txt               # Venue-specific dependencies (subset of backend)
└── tests/
    ├── test_price_engine.py
    ├── test_fill_simulator.py
    └── test_venue_api.py
```

### Venue Identity Table

| ID | Name | Cloud | Port | Personality |
|----|------|-------|------|-------------|
| V1 | AlphaExchange | AWS | 8001 | Reliable all-rounder, moderate spread |
| V2 | BetaLiquidity | GCP | 8002 | Best prices, tightest spreads |
| V3 | GammaMarkets | Azure | 8003 | Degraded/blacklisted (demo scenario) |
| V4 | DeltaPrime | AWS | 8004 | Premium venue, lowest latency |
| V5 | EpsilonPool | GCP | 8005 | Balanced, mid-range everything |

---

## `/frontend` — React + TypeScript UI

The frontend folder contains the Figma Make output plus any custom components. It is treated as a **separate, independent application** that communicates with the backend exclusively through REST API and WebSocket.

```
frontend/
│
├── public/
│   ├── favicon.ico
│   └── logo.svg
│
├── src/
│   ├── main.tsx                   # React entry point
│   ├── App.tsx                    # Root component, providers, router
│   ├── routes.tsx                 # Route definitions (all 18+ screens)
│   ├── vite-env.d.ts              # Vite type declarations
│   │
│   ├── api/                       # API communication layer
│   │   ├── client.ts              # Axios/fetch instance, base URL, auth interceptor
│   │   ├── endpoints.ts           # Typed API functions (getVenues, submitOrder, etc.)
│   │   └── types.ts               # API request/response TypeScript interfaces
│   │
│   ├── stores/                    # Zustand stores (real-time state)
│   │   ├── useVenueStore.ts       # Venue health, status, connectivity
│   │   ├── useMarketDataStore.ts  # NBBO, quotes, prices
│   │   ├── useRiskStore.ts        # Risk status, kill switch, alerts
│   │   ├── useOrderStore.ts       # Active orders, recent fills
│   │   └── useWebSocket.ts        # WebSocket connection manager, message router
│   │
│   ├── hooks/                     # Shared React hooks
│   │   ├── useAuth.ts             # Authentication state and actions
│   │   └── useTheme.ts            # Dark/light theme toggle
│   │
│   ├── components/                # Shared UI components
│   │   ├── ui/                    # shadcn/ui components (Button, Card, Dialog, etc.)
│   │   ├── layout/
│   │   │   ├── AppShell.tsx       # Main layout (sidebar + header + content)
│   │   │   ├── Sidebar.tsx        # Navigation sidebar
│   │   │   └── Header.tsx         # Top bar (user info, notifications, kill switch)
│   │   └── common/
│   │       ├── StatusBadge.tsx    # Colored status indicator
│   │       ├── LoadingSpinner.tsx
│   │       └── ErrorBoundary.tsx
│   │
│   ├── features/                  # Feature-based page modules
│   │   ├── auth/
│   │   │   ├── LoginPage.tsx
│   │   │   └── components/
│   │   │       └── LoginForm.tsx
│   │   │
│   │   ├── dashboard/
│   │   │   ├── DashboardPage.tsx  # Main overview — venue cards, NBBO, order flow
│   │   │   └── components/
│   │   │       ├── VenueCard.tsx
│   │   │       ├── NbboTicker.tsx
│   │   │       ├── OrderFlowChart.tsx
│   │   │       └── SystemHealthBar.tsx
│   │   │
│   │   ├── orders/
│   │   │   ├── OrderBlotterPage.tsx    # AG Grid table of all orders
│   │   │   ├── OrderEntryPage.tsx      # New order form
│   │   │   └── components/
│   │   │       ├── OrderTable.tsx
│   │   │       ├── OrderForm.tsx
│   │   │       └── OrderDetail.tsx
│   │   │
│   │   ├── market-data/
│   │   │   ├── MarketDataPage.tsx      # Live prices, order book depth
│   │   │   └── components/
│   │   │       ├── PriceChart.tsx       # TradingView Lightweight Charts
│   │   │       ├── OrderBookView.tsx
│   │   │       └── NbboDisplay.tsx
│   │   │
│   │   ├── venues/
│   │   │   ├── VenueConnectivityPage.tsx   # All venue cards with health scores
│   │   │   ├── VenueDetailPage.tsx         # Single venue deep dive
│   │   │   └── components/
│   │   │       ├── VenueGrid.tsx
│   │   │       ├── VenueHealthChart.tsx
│   │   │       └── VenueLatencyGraph.tsx
│   │   │
│   │   ├── risk/
│   │   │   ├── RiskManagerPage.tsx     # Risk dashboard, limits, positions
│   │   │   └── components/
│   │   │       ├── RiskLimitsTable.tsx
│   │   │       ├── PositionExposure.tsx
│   │   │       └── RiskEventLog.tsx
│   │   │
│   │   ├── kill-switch/
│   │   │   ├── KillSwitchPage.tsx      # Kill switch controls + alert history
│   │   │   └── components/
│   │   │       ├── KillSwitchToggle.tsx
│   │   │       └── AlertTimeline.tsx
│   │   │
│   │   ├── routing/
│   │   │   ├── RoutingEnginePage.tsx    # Routing decisions visualization
│   │   │   └── components/
│   │   │       ├── RoutingFlowDiagram.tsx
│   │   │       └── StrategySelector.tsx
│   │   │
│   │   └── execution/
│   │       ├── ExecutionReportsPage.tsx # Execution reports with fills
│   │       └── components/
│   │           ├── ExecutionTable.tsx
│   │           └── FillChart.tsx
│   │
│   ├── lib/                       # Utility functions
│   │   ├── utils.ts               # cn(), formatCurrency(), formatTimestamp()
│   │   ├── constants.ts           # App-wide constants (colors, status labels)
│   │   └── validators.ts          # Form validation schemas (zod)
│   │
│   └── types/                     # Global TypeScript types
│       ├── index.ts               # Shared type exports
│       ├── order.ts               # Order-related types
│       ├── venue.ts               # Venue-related types
│       └── websocket.ts           # WebSocket message types
│
├── index.html                     # Vite HTML entry point
├── vite.config.ts                 # Vite configuration
├── tailwind.config.ts             # Tailwind CSS configuration
├── postcss.config.js              # PostCSS config
├── tsconfig.json                  # TypeScript configuration
├── tsconfig.node.json             # TS config for Vite/Node
├── package.json                   # Dependencies and scripts
├── pnpm-lock.yaml                 # Lock file
└── .eslintrc.cjs                  # ESLint configuration
```

### Frontend Architecture Rules

1. **Feature isolation** — Each feature folder is self-contained. A feature never imports from another feature's `components/` folder. Shared components go in `/src/components/`.

2. **API layer is the only exit** — Components never call `fetch()` or `axios` directly. All API calls go through `/src/api/endpoints.ts`, which returns typed data.

3. **Stores are for real-time only** — Zustand stores hold WebSocket-pushed data (venue health, live prices, alerts). REST data is managed by TanStack Query hooks inside feature components.

4. **No business logic in components** — Components render UI. Logic lives in hooks, stores, or the API layer.

---

## `/infra` — Infrastructure & Deployment

```
infra/
│
├── docker/
│   ├── Dockerfile.gateway         # Backend gateway container
│   ├── Dockerfile.venue           # Venue simulator container
│   ├── Dockerfile.frontend        # Frontend build + nginx container
│   ├── nginx.conf                 # Nginx config for frontend serving + API proxy
│   └── init-db.sql                # PostgreSQL initialization script
│
├── compose/
│   ├── docker-compose.yml         # Full stack (all services)
│   ├── docker-compose.dev.yml     # Dev overrides (hot reload, volumes)
│   └── docker-compose.infra.yml   # Infrastructure only (postgres + redis)
│
├── cloud/
│   ├── aws/
│   │   ├── ec2-venue-setup.sh     # V1 + V4 deployment on AWS
│   │   └── security-group.json    # Firewall rules
│   ├── gcp/
│   │   ├── gce-venue-setup.sh     # V2 + V5 deployment on GCP
│   │   └── firewall-rules.sh
│   └── azure/
│       ├── vm-venue-setup.sh      # V3 deployment on Azure
│       └── nsg-rules.json         # Network security group
│
└── monitoring/
    ├── prometheus.yml             # Metrics scraping config
    └── grafana/
        └── dashboards/
            └── sor-overview.json  # Pre-built Grafana dashboard
```

---

## `/tests` — Integration & E2E Tests

```
tests/
│
├── integration/
│   ├── conftest.py                # Fixtures: start venues + gateway, seed DB
│   ├── test_order_flow.py         # Submit order → route → fill → report
│   ├── test_venue_blacklist.py    # Degrade V3 → verify routing skips it
│   ├── test_kill_switch.py        # Activate kill switch → verify all orders halt
│   ├── test_risk_limits.py        # Exceed limits → verify rejection
│   └── test_market_data.py        # Verify NBBO computation from 5 venues
│
├── e2e/                           # End-to-end (if using Playwright/Cypress later)
│   └── README.md                  # Placeholder — setup when frontend stabilizes
│
└── load/
    ├── locustfile.py              # Load testing with Locust
    └── README.md                  # How to run load tests
```

---

## `/scripts` — Utility Scripts

```
scripts/
│
├── seed_database.py               # One-shot DB seeding (venues, users, limits)
├── generate_test_orders.py        # Generate N random test orders
├── run_all_venues.ps1             # PowerShell script to start all 5 venues
├── run_all_venues.sh              # Bash equivalent for Linux/Mac
├── export_metrics.py              # Export performance metrics to CSV
└── reset_local.ps1                # Nuclear reset — drops DB, clears Redis, fresh start
```

---

## Root Configuration Files

| File | Purpose |
|------|---------|
| `.env.example` | Template for environment variables — copy to `.env` |
| `.gitignore` | Ignore `.venv/`, `node_modules/`, `.env`, `__pycache__/`, `.next/`, `dist/` |
| `docker-compose.yml` | Root compose file (can import from `infra/compose/`) |
| `Makefile` | Optional shortcuts: `make dev`, `make test`, `make seed`, `make reset` |
| `README.md` | Brief pointer to `docs/README.md` or duplicate |

### `.gitignore` Template

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
*.egg
dist/
build/

# Node
node_modules/
dist/
.next/

# Environment
.env
.env.local
.env.*.local

# IDE
.vscode/settings.json
.idea/
*.swp
*.swo

# Docker
docker-compose.override.yml

# OS
.DS_Store
Thumbs.db

# Testing
htmlcov/
.coverage
.pytest_cache/

# Database
*.db
*.sqlite3
```

---

## File Naming Conventions

| Context | Convention | Example |
|---------|-----------|---------|
| Python modules | `snake_case.py` | `price_engine.py`, `risk_engine.py` |
| Python classes | `PascalCase` | `RoutingEngine`, `VenueHealthMonitor` |
| TypeScript/React components | `PascalCase.tsx` | `DashboardPage.tsx`, `VenueCard.tsx` |
| TypeScript hooks | `camelCase.ts` (prefixed `use`) | `useAuth.ts`, `useWebSocket.ts` |
| TypeScript stores | `camelCase.ts` (prefixed `use`) | `useVenueStore.ts` |
| TypeScript utilities | `camelCase.ts` | `utils.ts`, `constants.ts` |
| API endpoints file | `camelCase.ts` | `endpoints.ts` |
| Test files | `test_*.py` / `*.test.ts` | `test_routing_engine.py`, `OrderForm.test.ts` |
| Database migrations | `NNN_description.py` | `001_initial_schema.py` |
| Docker files | `Dockerfile.<service>` | `Dockerfile.gateway` |
| Markdown docs | `UPPER_CASE.md` | `ARCHITECTURE.md` |

---

## Import Rules (Dependency Direction)

```
routers → services → shared
                  ↘ shared/database
                  ↘ shared/redis
                  ↘ shared/models

Never: routers → routers
Never: services → routers
Never: shared → services
Never: shared → routers
```

**In Python (backend):**
```python
# router imports service
from backend.services.routing.engine import RoutingEngine

# service imports shared models
from backend.shared.models.order import Order, OrderStatus

# service imports repository
from backend.shared.database.repositories.order_repo import OrderRepository

# NEVER: service imports router
# NEVER: shared imports service
```

**In TypeScript (frontend):**
```typescript
// Feature component imports from api layer
import { getVenues } from '@/api/endpoints';

// Feature component imports shared component
import { StatusBadge } from '@/components/common/StatusBadge';

// Feature component imports store
import { useVenueStore } from '@/stores/useVenueStore';

// NEVER: feature imports from another feature's components/
// If shared, move to /src/components/
```

---

## How to Add a New Feature (Checklist)

### Adding a Backend Service

1. Create folder: `backend/services/<feature>/`
2. Create `__init__.py`, `engine.py` (or appropriate name)
3. Add Pydantic models to `backend/shared/models/` if new data types needed
4. Add SQLAlchemy table to `backend/shared/database/tables.py` if DB storage needed
5. Create alembic migration: `alembic revision --autogenerate -m "add_<feature>_table"`
6. Create repository in `backend/shared/database/repositories/` if needed
7. Create router in `backend/routers/<feature>.py`
8. Register router in `backend/gateway/main.py`
9. Write tests in `backend/tests/test_<feature>.py`

### Adding a Frontend Page

1. Create folder: `frontend/src/features/<feature>/`
2. Create `<Feature>Page.tsx` as the page component
3. Create `components/` subfolder for page-specific components
4. Add route in `frontend/src/routes.tsx`
5. Add API functions in `frontend/src/api/endpoints.ts` if new endpoints
6. Add types in `frontend/src/types/` if new data shapes
7. Add Zustand store in `frontend/src/stores/` only if real-time WebSocket data needed
8. Add sidebar link in `frontend/src/components/layout/Sidebar.tsx`

---

## POC vs Full Build — What Changes

The folder structure above is the **final structure**. During POC phase, many folders will be empty or have minimal implementations. Here's what exists at each phase:

### POC Phase (Current)

```
✅ Created and functional:
   venues/                     — All 5 simulators running
   backend/gateway/            — App factory, basic config
   backend/routers/            — orders, venues, market_data (basic)
   backend/services/market_data/ — Aggregator, NBBO
   backend/services/routing/   — Best price algorithm (basic)
   backend/services/risk/      — Pre-trade checks (basic, in-memory)
   backend/shared/models/      — Core Pydantic models
   backend/shared/websocket/   — WebSocket manager
   frontend/                   — Figma Make output, connected to mock data
   docs/                       — All documentation

🔄 Stubbed (interface exists, minimal implementation):
   backend/services/execution/ — Direct venue calls, no retry/circuit-breaker
   backend/services/venue_health/ — Basic health check, no scoring formula yet
   backend/services/consensus/ — Stub, not needed for POC
   backend/services/policy/    — Stub, hardcoded rules
   backend/shared/database/    — SQLite or in-memory for POC, no Alembic yet

⏳ Not started:
   infra/cloud/                — Local only for POC
   tests/integration/          — After core services work
   tests/load/                 — After POC demo
```

### Post-POC (Milestones 3-4)

Everything above moves from stubbed → fully implemented. New additions:

```
backend/services/consensus/    — Full RAFT implementation
backend/services/policy/       — Dynamic policy engine
backend/shared/database/       — PostgreSQL + Alembic migrations
infra/docker/                  — Full containerization
infra/cloud/                   — Multi-cloud venue deployment
tests/                         — Full integration + load tests
monitoring/                    — Prometheus + Grafana
```

The key point: **the folder structure never changes**. We just fill in the implementations progressively.
