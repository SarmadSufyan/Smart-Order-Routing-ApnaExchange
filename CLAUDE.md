# CLAUDE.md — Master Reference for Claude Code

> **This file is the single source of truth for any AI assistant working on this codebase.**
> Read this first. Read the linked docs for depth. Never guess when a doc exists.

---

## Project Identity

| Field | Value |
|---|---|
| **Project Name** | DEIRCP — Distributed Execution Intelligence & Risk Control Platform |
| **Brand Name** | Apna Exchange |
| **Group** | CS14 — Usman Institute of Technology (UITU) |
| **Domain** | Finance / FinTech (Algorithmic Trading & Risk Management) |
| **Type** | Final Year Project (FYP) — 4-member team |
| **Supervisors** | Sir Usman Javed, Miss Shiza |

### Team

| Name | Roll No | Primary Responsibility |
|---|---|---|
| Sarmad Sufyan Ahmed | 23SP-078-CS | Backend architecture, system design |
| Fizza Mubeen (Xange) | 23SP-070-CS | Frontend development, UI/UX integration |
| Mahnoor Nadeem | 23SP-065-CS | Risk analytics, algorithms |
| Maham Ikram | 23SP-101-CS | Testing, documentation, deployment |

---

## What This Project Is (One Paragraph)

A distributed, real-time platform that sits between an Order Management System (OMS) and multiple simulated trading venues. It ingests FIX protocol order-flow, continuously monitors venue health (latency, fill rates, rejection rates), predicts execution risk before orders are placed, and enforces automated control actions (venue blacklisting, order throttling, kill switch). A RAFT consensus cluster ensures all platform nodes agree on risk state, making control decisions consistent and fault-tolerant. Five venue servers are deployed across AWS, GCP, and Azure to simulate a realistically fragmented market.

---

## Architecture at a Glance

```
┌──────────────────────────────────────────────────────────────────────┐
│                     FRONTEND (React + TypeScript)                     │
│         Dashboard │ Order Blotter │ Risk Manager │ Kill Switch        │
│              Venue Connectivity │ Market Data │ Execution Reports     │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ REST + WebSocket
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     API GATEWAY (FastAPI)                             │
│         Authentication │ Rate Limiting │ Request Routing              │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ Internal gRPC / async events
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     PLATFORM SERVICES LAYER                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ Market Data   │  │ Routing      │  │ Risk Engine               │   │
│  │ Aggregator    │  │ Engine       │  │ (Pre-trade + Kill Switch) │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘   │
│         │                 │                      │                    │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────────┴───────────────┐   │
│  │ Venue Health  │  │ Order State  │  │ RAFT Consensus            │   │
│  │ Monitor       │  │ Manager      │  │ Cluster (3-node)          │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │              Policy Engine (Rule-based decisions)              │    │
│  └──────────────────────────────────────────────────────────────┘    │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ FIX / REST adapters
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     VENUE SIMULATORS (5 servers)                      │
│  V1: AlphaExchange   V2: BetaLiquidity   V3: GammaMarkets           │
│  (AWS, :8001)        (GCP, :8002)        (Azure, :8003, degraded)    │
│                                                                      │
│  V4: DeltaPrime      V5: EpsilonPool                                 │
│  (AWS, :8004)        (GCP, :8005)                                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
apna-exchange/
│
├── CLAUDE.md                         # ← YOU ARE HERE
├── README.md                         # Project overview, quick start
│
├── project-info/                     # All project planning documentation (READ BEFORE CODING)
│   ├── README.md                     # Project overview, quick start
│   ├── ARCHITECTURE.md               # System architecture deep dive
│   ├── PROJECT_STRUCTURE.md          # Directory layout explanation (CANONICAL)
│   ├── MILESTONES.md                 # FYP milestones & development phases
│   ├── BACKEND_GUIDE.md              # Backend services, patterns, conventions
│   ├── FRONTEND_GUIDE.md             # Frontend architecture & screen map
│   ├── API_SPECIFICATION.md          # REST + WebSocket API contracts
│   ├── DATABASE_SCHEMA.md            # PostgreSQL + Redis data models (M3+; POC is in-memory)
│   ├── ALGORITHMS.md                 # Core algorithms with pseudocode
│   ├── DEPLOYMENT.md                 # Docker + AWS deployment (originally multi-cloud — AWS only now)
│   └── DEVELOPMENT_SETUP.md          # Local environment setup guide
│
├── frontend/                         # React + TypeScript + Vite
│   ├── src/
│   │   ├── app/
│   │   │   ├── routes.tsx
│   │   │   └── App.tsx
│   │   ├── components/               # Shared UI components
│   │   │   ├── ui/                   # shadcn/ui primitives
│   │   │   └── layout/               # Layout shells, sidebar, topbar
│   │   ├── features/                 # Feature-based modules
│   │   │   ├── dashboard/
│   │   │   ├── order-blotter/
│   │   │   ├── market-data/
│   │   │   ├── venue-connectivity/
│   │   │   ├── risk-manager/
│   │   │   ├── kill-switch/
│   │   │   ├── routing-engine/
│   │   │   ├── execution-reports/
│   │   │   └── auth/
│   │   ├── hooks/                    # Custom React hooks
│   │   ├── stores/                   # Zustand stores (WebSocket state)
│   │   ├── services/                 # API client, WebSocket client
│   │   ├── types/                    # TypeScript type definitions
│   │   └── lib/                      # Utilities, constants, helpers
│   ├── public/
│   ├── tailwind.config.ts
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── package.json
│
├── backend/
│   ├── gateway/                      # FastAPI API Gateway
│   │   ├── main.py                   # App entry point
│   │   ├── config.py                 # Configuration management
│   │   ├── middleware/               # Auth, CORS, rate limiting
│   │   ├── routers/                  # API route handlers
│   │   │   ├── orders.py
│   │   │   ├── venues.py
│   │   │   ├── market_data.py
│   │   │   ├── risk.py
│   │   │   ├── routing.py
│   │   │   └── admin.py
│   │   ├── websocket/                # WebSocket manager
│   │   │   └── ws_manager.py
│   │   └── dependencies.py           # Dependency injection
│   │
│   ├── services/                     # Microservices
│   │   ├── market_data/              # Market data collection & aggregation
│   │   │   ├── collector.py          # Per-venue data collection
│   │   │   ├── aggregator.py         # NBBO aggregation across venues
│   │   │   ├── price_engine.py       # GBM + regime-switching simulator
│   │   │   └── models.py
│   │   │
│   │   ├── routing_engine/           # Smart order routing
│   │   │   ├── engine.py             # Core routing logic
│   │   │   ├── strategies/
│   │   │   │   ├── best_price.py     # Best price strategy
│   │   │   │   ├── liquidity_sweep.py
│   │   │   │   └── base.py           # Strategy interface
│   │   │   ├── venue_scorer.py       # Venue scoring / ranking
│   │   │   └── models.py
│   │   │
│   │   ├── risk_engine/              # Risk management
│   │   │   ├── pre_trade.py          # Pre-trade risk checks
│   │   │   ├── kill_switch.py        # Kill switch logic
│   │   │   ├── position_tracker.py   # Position / exposure tracking
│   │   │   ├── policy_engine.py      # Rule-based policy enforcement
│   │   │   └── models.py
│   │   │
│   │   ├── order_state/              # Order lifecycle management
│   │   │   ├── state_machine.py      # Order state transitions
│   │   │   ├── order_manager.py      # CRUD + lifecycle orchestration
│   │   │   └── models.py
│   │   │
│   │   ├── venue_monitor/            # Venue health monitoring
│   │   │   ├── health_checker.py     # Latency, uptime, error tracking
│   │   │   ├── anomaly_detector.py   # Statistical anomaly detection
│   │   │   └── models.py
│   │   │
│   │   └── consensus/                # RAFT consensus
│   │       ├── raft_node.py          # RAFT node implementation
│   │       ├── raft_log.py           # Replicated log
│   │       ├── state_replicator.py   # Risk state replication
│   │       └── models.py
│   │
│   ├── shared/                       # Shared across all services
│   │   ├── models/                   # Pydantic models (Order, Venue, etc.)
│   │   │   ├── order.py
│   │   │   ├── venue.py
│   │   │   ├── market_data.py
│   │   │   ├── risk.py
│   │   │   └── execution_report.py
│   │   ├── database/                 # DB connection, migrations
│   │   │   ├── postgres.py
│   │   │   ├── redis_client.py
│   │   │   └── migrations/
│   │   ├── events/                   # Event bus (pub/sub)
│   │   │   └── event_bus.py
│   │   ├── fix/                      # FIX protocol utilities
│   │   │   ├── parser.py
│   │   │   └── builder.py
│   │   └── utils/
│   │       ├── logger.py
│   │       ├── metrics.py
│   │       └── time_utils.py
│   │
│   ├── requirements.txt
│   └── pyproject.toml
│
├── venues/                           # 5 venue simulator servers
│   ├── venue_server.py               # Configurable venue simulator
│   ├── order_book.py                 # In-memory order book engine
│   ├── matching_engine.py            # Price-time priority matching
│   ├── liquidity_model.py            # Liquidity simulation
│   ├── impact_model.py               # Square-root market impact
│   ├── profiles/                     # Per-venue personality configs
│   │   ├── alpha_exchange.yaml       # V1: AWS, stable, medium latency
│   │   ├── beta_liquidity.yaml       # V2: GCP, best prices, fast
│   │   ├── gamma_markets.yaml        # V3: Azure, degraded, blacklisted
│   │   ├── delta_prime.yaml          # V4: AWS, premium, low latency
│   │   └── epsilon_pool.yaml         # V5: GCP, dark pool, balanced
│   └── requirements.txt
│
├── infra/                            # Infrastructure
│   ├── docker/
│   │   ├── docker-compose.yml        # Full stack orchestration
│   │   ├── docker-compose.dev.yml    # Development overrides
│   │   ├── Dockerfile.gateway        # API gateway image
│   │   ├── Dockerfile.venue          # Venue simulator image
│   │   └── Dockerfile.frontend       # Frontend build image
│   ├── nginx/
│   │   └── nginx.conf                # Reverse proxy config
│   └── scripts/
│       ├── start_all.sh              # Start everything locally
│       ├── start_venues.sh           # Start venue simulators only
│       └── seed_data.sh              # Seed test data
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── load/
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── .gitignore
├── .env.example
└── docker-compose.yml                # Symlink to infra/docker/
```

---

## Technology Stack

| Layer | Technology | Why |
|---|---|---|
| **Frontend** | React 18 + TypeScript + Vite | Figma Make generated the initial code; fast HMR with Vite |
| **UI Library** | Tailwind CSS + shadcn/ui | Consistent dark-theme components; trading-dashboard aesthetic |
| **Frontend State** | Zustand (real-time) + TanStack Query (server) | Zustand for WebSocket streams; TanStack for REST cache/sync |
| **Charts** | TradingView Lightweight Charts + Recharts | Industry-standard candlestick/line charts |
| **Tables** | AG Grid (Community) | High-performance grid for order blotter, market data |
| **Backend Gateway** | Python FastAPI | Async, auto-OpenAPI docs, fast prototyping |
| **Database** | PostgreSQL 16 | Orders, execution reports, audit logs, config |
| **Cache / Pub-Sub** | Redis 7 | Real-time market data cache, WebSocket pub/sub, rate limiting |
| **Venue Simulators** | Python FastAPI | Self-contained per-venue with GBM price engine |
| **FIX Protocol** | QuickFIX-Python | Session management, message parsing, execution reports |
| **Consensus** | Custom RAFT (Python) | Risk state replication across 3-node cluster |
| **Containerization** | Docker + docker-compose | Local dev + multi-cloud deployment |
| **Cloud** | AWS + GCP + Azure | Venues distributed across 3 clouds for realism |
| **CI/CD** | GitHub Actions | Automated testing, linting, build verification |

---

## Coding Conventions

### Python (Backend)

- Python 3.11+
- Use `async`/`await` everywhere in FastAPI — no sync blocking in request handlers
- Pydantic v2 for all data models — strict validation, `model_dump()` not `.dict()`
- Type hints on every function signature — no `Any` unless unavoidable
- Import order: stdlib → third-party → local (enforced by `ruff`)
- Naming: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants
- Docstrings: Google style on all public functions
- Error handling: raise domain-specific exceptions (`OrderNotFoundError`, `VenueUnavailableError`), caught in middleware
- Logging: structured JSON via `structlog` — every log includes `order_id`, `venue_id`, `timestamp`
- Tests: `pytest` + `pytest-asyncio` — minimum 80% coverage on services

### TypeScript (Frontend)

- Strict mode enabled (`"strict": true` in tsconfig)
- Functional components only — no class components
- Custom hooks for any logic used in 2+ components
- Named exports only — no default exports (except route pages)
- Naming: `camelCase` for variables/functions, `PascalCase` for components/types, `UPPER_SNAKE` for constants
- File naming: `kebab-case.tsx` for components, `camelCase.ts` for utilities
- Feature-based folder structure — everything related to a feature lives in its folder
- No `any` — use `unknown` and narrow with type guards
- Prefer `interface` over `type` for object shapes (extensibility)
- CSS: Tailwind utility classes only — no custom CSS files except for global tokens

### Git Conventions

- Branch naming: `feature/xxx`, `fix/xxx`, `refactor/xxx`, `project-info/xxx`
- Commit messages: conventional commits — `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`
- PR-based workflow — no direct pushes to `main`
- Each PR must pass CI (lint + test + build)

---

## Five Venue Profiles

| ID | Name | Cloud | Port | Personality | Status |
|---|---|---|---|---|---|
| V1 | AlphaExchange | AWS | 8001 | Stable, medium latency (5-15ms), reliable fills | HEALTHY |
| V2 | BetaLiquidity | GCP | 8002 | Best prices, fast (2-8ms), deep liquidity | HEALTHY |
| V3 | GammaMarkets | Azure | 8003 | Degraded latency (50-200ms), high reject rate | BLACKLISTED |
| V4 | DeltaPrime | AWS | 8004 | Premium venue, lowest latency (1-5ms), thin books | HEALTHY |
| V5 | EpsilonPool | GCP | 8005 | Dark pool, balanced (10-25ms), variable liquidity | HEALTHY |

---

## Key Architectural Decisions

1. **RAFT consensus lives in the platform layer, NOT in venue servers.** Venues are purely simulated exchanges. The RAFT cluster replicates risk state (venue health scores, blacklist decisions, kill switch status) across platform nodes.

2. **Single multiplexed WebSocket** from frontend to gateway. Messages are routed by `type` field into Zustand slices. No multiple connections.

3. **Feature-based folder structure** over file-type grouping. Everything for "risk-manager" (component, hooks, types, tests) lives in `features/risk-manager/`.

4. **Backend is Python FastAPI** — not the C++ core described in the blueprint. The blueprint is the production-grade reference architecture. Our implementation is a faithful POC in Python that demonstrates the same design principles, interfaces, and data flows.

5. **Venue simulators are in-process** — each venue is a self-contained FastAPI server with its own order book, matching engine, and price simulator. No separate matching engine microservice.

6. **Order state machine is deterministic.** Valid transitions are explicitly defined. Any invalid transition is rejected and logged.

7. **Event-driven internal communication.** Services communicate via an async event bus (Redis pub/sub), not synchronous inter-service calls, except for latency-critical paths (routing decisions query market data synchronously).

---

## What NOT to Do

- **Do NOT modify files in `frontend/`** without explicit instructions — Figma Make generated that code and the structure should be preserved.
- **Do NOT use `print()` for logging** — use `structlog` with structured fields.
- **Do NOT create sync endpoints in FastAPI** — everything is `async def`.
- **Do NOT hardcode venue URLs** — use configuration/environment variables.
- **Do NOT skip Pydantic validation** — every API input/output goes through a model.
- **Do NOT put business logic in route handlers** — handlers call service functions.
- **Do NOT use `localStorage`** in React artifacts — use React state.
- **Do NOT install packages globally** — use project-level `requirements.txt` or `package.json`.

---

## Quick Reference: Which Doc to Read

| I need to... | Read this |
|---|---|
| Understand the full system architecture | `project-info/ARCHITECTURE.md` |
| Know where files go | `project-info/PROJECT_STRUCTURE.md` |
| See milestone timeline & what's done | `project-info/MILESTONES.md` |
| Build or modify backend services | `project-info/BACKEND_GUIDE.md` |
| Build or modify frontend screens | `project-info/FRONTEND_GUIDE.md` |
| Implement or consume an API endpoint | `project-info/API_SPECIFICATION.md` |
| Design or query the database | `project-info/DATABASE_SCHEMA.md` |
| Understand the routing/risk algorithms | `project-info/ALGORITHMS.md` |
| Deploy or run with Docker | `project-info/DEPLOYMENT.md` |
| Set up local dev environment | `project-info/DEVELOPMENT_SETUP.md` |
