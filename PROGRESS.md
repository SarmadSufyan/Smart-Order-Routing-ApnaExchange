# Progress

> Live snapshot of the project's state. Updated at the end of every working session, and automatically when the AI assistant's context window approaches 90% full.
>
> **Read this first** when starting a new session — it tells you exactly where to pick up.

---

## Last updated

**2026-06-12** — by Claude (Opus 4.6). Full backend implemented and tested end-to-end.

---

## Current milestone

**M2 — POC** (delayed from original Week 14 schedule; now in active build).

POC scope is the five panel-assigned use cases. Full list in [`project-info/MILESTONES.md`](project-info/MILESTONES.md) §"POC Requirements Assigned by Panel".

---

## What's done

### Repo scaffolding (this session)

- Created canonical folder structure per `project-info/PROJECT_STRUCTURE.md`.
- Moved all 11 original planning docs into `project-info/` (CLAUDE.md kept at root for AI auto-load).
- Updated `CLAUDE.md` path references from `docs/` to `project-info/`.
- Created root README, PROGRESS, DEVELOPMENT_JOURNAL, .env.example, .gitignore.
- Created backend + venues `requirements.txt`, `pyproject.toml`.
- Per-major-folder README in `backend/`, `venues/`, `frontend/`, `infra/`, `tests/`, `scripts/`.
- AWS-only `infra/cloud/aws/` created (no `gcp/` or `azure/` — that override is final).
- **Team guides** added: `frontend/TEAM_GUIDE.md` (for the 2 frontend devs) and `venues/TEAM_GUIDE.md` (for the 1 market-simulator dev). Each is self-contained and tells the teammate exactly what to build, in what order, with what conventions.
- **Deployment scope clarified:** venues (market simulators) DO get deployed to AWS for the POC demo, on separate EC2 VMs, so health monitoring and venue blacklisting are visibly meaningful over real network latency. Platform services (gateway, routing, risk, frontend) still run locally during POC.

### From prior work (pre-this-repo, per docs)

These were claimed "complete" in `project-info/MILESTONES.md` §M2 status but **the code is not yet in this repo** — to be rebuilt fresh from the spec:

- Venue simulators (5 servers, GBM price engine, profiles, FastAPI endpoints)
- Market Data Aggregator (NBBO across venues, V3 blacklisted)
- Frontend prototype (Figma Make, 18+ screens, dark theme) — to be dropped into `frontend/` by Fizza

---

## What's next (immediate)

Work is now parallel across three team workstreams. Dependencies are noted per task.

### Workstream A — Backend / SOR (user) — DONE

All backend services implemented and tested end-to-end:

1. ✅ **Shared Pydantic models** — `backend/shared/models/` (6 model files + exceptions)
2. ✅ **Venue health monitor** — `backend/services/venue_monitor/health_checker.py` — health scoring, latency tracking, blacklist
3. ✅ **Market data collector + aggregator** — `backend/services/market_data/` — NBBO computation, quote polling
4. ✅ **Risk engine** — `backend/services/risk_engine/` — pre-trade checks (7 checks), kill switch, position tracker
5. ✅ **Routing engine (best-price)** — `backend/services/routing_engine/` — order splitting, venue selection, fill handling
6. ✅ **Order state manager** — `backend/services/order_state/` — deterministic state machine, fill tracking
7. ✅ **Gateway + all routers** — `backend/gateway/` — auth, orders, venues, market-data, risk, routing, admin, execution-reports
8. ✅ **WebSocket manager** — `backend/gateway/websocket/` — subscribe/unsubscribe, broadcast, per-channel routing
9. ✅ **Mock venue simulator** — `backend/services/market_data/mock_venue.py` — 5 venues with GBM prices for local testing
10. ✅ **JWT auth** — hardcoded users (admin/trader1/risk_mgr), role-based access control

### Workstream B — Market simulator (1 person)

See `venues/TEAM_GUIDE.md` for the full build order. High level:

1. One venue end-to-end locally (V1 / AlphaExchange).
2. Replicate to V2–V5 by swapping profiles.
3. Tests.
4. AWS deployment — one EC2 per venue, Docker image, public DNS, security groups. Coordinate with user before swapping the platform's `.env` URLs.

### Workstream C — Frontend (2 people)

See `frontend/TEAM_GUIDE.md` for the full plan. High level:

1. Fizza drops Figma Make output into `frontend/` on a `feature/figma-frontend-import` branch.
2. Build the single `ApiClient` (REST) and `WebSocketClient` (real-time).
3. Wire the 4 Zustand stores (venue, market data, order, risk).
4. Replace mock data on the 5 POC screens (Login, Dashboard, Venue Connectivity, Risk Manager, Kill Switch) one at a time.

---

## Open questions / blockers

None right now. Decisions locked:

- Persistence: **in-memory only** for POC (repository interfaces clean enough that M3 swaps in Postgres without service-layer changes).
- Auth: **real JWT, hardcoded users** (admin / trader1 / risk_mgr) for POC.
- Deployment: **AWS only**. For POC: venues go to AWS EC2 (one per venue) so health monitoring is meaningful; platform services + frontend run locally during the demo.
- Venue simulator code: **rebuild fresh** from `project-info/ALGORITHMS.md` §3 spec, not migrated from elsewhere.
- Team split: 2 frontend, 1 market simulator, user owns the rest. See `frontend/TEAM_GUIDE.md` and `venues/TEAM_GUIDE.md`.
- Symbols: **multi-symbol basket** (e.g., AAPL, GOOGL, MSFT, AMZN, TSLA) — not single-ticker. Each venue maintains multi-symbol order books.
- Team workflow: **simplified** — one branch per workstream, one PR when done, Sarmad reviews and merges. See `CONTRIBUTING.md`.
- M1 diagrams: will be added to `diagrams/` folder by the user.

---

## How to run locally

```powershell
# Terminal 1: Start mock venues (all 5 on ports 8001-8005)
cd D:\Sor-Fyp
$env:PYTHONPATH = "."
python -m backend.services.market_data.mock_venue

# Terminal 2: Start the gateway (port 8000)
cd D:\Sor-Fyp
$env:PYTHONPATH = "."
python -m uvicorn backend.gateway.main:app --host 127.0.0.1 --port 8000

# Open Swagger UI: http://127.0.0.1:8000/docs
# Login: POST /api/auth/login with {"username":"admin","password":"admin"}
# Copy the access_token, click "Authorize" in Swagger, paste "Bearer <token>"
```

---

## Not-yet-committed work in progress

Backend implementation (not yet committed).

---

## How to update this file

- **End of every working session:** update "Last updated", refresh "What's done" and "What's next."
- **When AI context hits ~90% used:** the AI assistant updates this automatically so the next session can pick up cold.
- **When a decision flips:** edit the "Open questions" or top of "What's done" with the new decision and a date.

Keep it factual. No marketing, no aspirational language. A teammate pulling this branch should know exactly what state the codebase is in after reading this file alone.
