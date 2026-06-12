# Development Journal

> Plain-language explanation of every meaningful change to this project — what we built, **why**, and the design choices behind it. This is for learning and team handoff, not for git commit messages.
>
> Order is chronological (newest at bottom). Each entry has a date, a title, and walks the reader through the reasoning.

---

## 2026-06-11 — Day 0: Repo scaffolding

### What we did

Started from a clean folder (`D:\Sor-Fyp\`) that contained only 12 planning markdown files. By the end of this session:

- All planning docs moved into a new `project-info/` directory (except `CLAUDE.md`, which has to stay at the project root so the AI assistant auto-loads it).
- The full canonical folder tree from `project-info/PROJECT_STRUCTURE.md` exists on disk, even for modules we won't write code for until M3 (consensus, policy, migrations).
- Python packages have `__init__.py` so imports resolve cleanly once we start writing code.
- Top-level `README.md`, `PROGRESS.md`, this journal, `.env.example`, `.gitignore`, `requirements.txt` for backend + venues, and `pyproject.toml` are all in place.
- Frontend folder is an empty placeholder with a README explaining that the Figma Make output gets dropped in there as one step (no piecemeal scaffolding).

### Why we set it up this way

**Folder structure first, code later.** The user explicitly asked us to "finalize the structure" before any code lands. Three reasons this matters:

1. The team will be pulling from GitHub. A clear, canonical layout means Fizza (frontend), Mahnoor (algorithms), and Maham (testing) can each find their workspace without asking. Code goes in the same place no matter who writes it.
2. The POC is **Phase 1 of the full system, not a throwaway demo.** Every folder that exists for M3 has to also exist now — empty if needed — so that M3 contributors don't shuffle files around and break imports across the codebase.
3. AI coding assistants (us) work much better against a known layout. `PROJECT_STRUCTURE.md` is the spec, and the folder tree on disk matches it. No ambiguity.

**Why `project-info/` instead of `docs/` (which `PROJECT_STRUCTURE.md` originally said).** User direct instruction. The new name reads better — `docs/` could be confused for runtime user documentation; `project-info/` is unambiguously the planning workspace.

**Why three top-level "narrative" files (`README`, `PROGRESS`, this journal)?** Each serves a different reader:

- `README.md` — someone landing on the GitHub page for the first time. Answers "what is this and how do I run it."
- `PROGRESS.md` — a teammate (or future AI session) jumping back in mid-build. Answers "where are we and what's next." Updated continuously.
- `DEVELOPMENT_JOURNAL.md` (this file) — someone trying to **learn how the system was built** or audit design choices. Answers "what did we change and why." Append-only.

Without all three, that information collapses into either git history (too terse) or chat transcripts (lost between sessions).

### Decisions locked in this session

The user answered three blockers before scaffolding started:

| Decision | Choice | Why |
|---|---|---|
| Existing venue simulator code | **Rebuild fresh** from `project-info/ALGORITHMS.md` §3 GBM spec | Old code wasn't in this repo and the spec is precise enough to rebuild cleanly |
| POC persistence | **In-memory only** (no DB) | Fastest demo, zero infra. M3 swaps in Postgres behind unchanged repository interfaces |
| POC auth | **Real JWT, hardcoded users** | Backend role logic actually works for the demo. M3 just replaces the user lookup with a DB query |

Additional decision (carried in from prior conversation, captured in AI memory):

| Decision | Choice | Why |
|---|---|---|
| Cloud deployment | **AWS only**, separate VMs per environment | A 4-person student team realistically managing one provider is much smaller scope than three. Doesn't change the FYP narrative |

### What we did NOT do (and why)

- **No service implementation code.** The user said structure first; we kept that promise. Empty Python packages only.
- **No `docker-compose.yml` content.** POC is in-memory, so Postgres + Redis containers add nothing right now. We'll write the compose file when M3 needs it.
- **No Alembic init.** No DB means no migrations needed.
- **No per-subfolder READMEs inside `backend/services/*/`.** They'd be empty noise. `project-info/PROJECT_STRUCTURE.md` already documents what each subfolder is for.
- **No `gcp/` or `azure/` folders under `infra/cloud/`.** AWS-only decision is final — don't carry forward the original multi-cloud aspiration.

### Files changed

- **Created:** `README.md`, `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md`, `.env.example`, `.gitignore`, `backend/{requirements.txt,requirements-dev.txt,pyproject.toml,README.md}`, `venues/{requirements.txt,README.md}`, `frontend/README.md`, `infra/README.md`, `tests/README.md`, `scripts/README.md`, plus `__init__.py` in every Python package.
- **Moved:** All 11 planning docs from project root → `project-info/`.
- **Edited:** `CLAUDE.md` — updated path references from `docs/…` → `project-info/…` and noted AWS-only + POC-is-in-memory caveats.

### What the next session should expect

`PROGRESS.md` "What's next" is the authoritative next-step list. Most likely first move: write the shared Pydantic models in `backend/shared/models/` (order, venue, market_data, risk, execution), because every service depends on them. After that, venue simulator implementation is the natural #1 because it has no upstream dependencies and unblocks the aggregator.

---

<!-- Append new dated entries below this line. Newest at bottom. -->

---

## 2026-06-12 — Day 1: Workflow finalized + POC scope locked

### What we did

1. **Simplified the team workflow.** The original CONTRIBUTING.md had feature-by-feature branching, review matrices, and daily loops. Too complex for a 4-person FYP team. Replaced with: 3 branches (one per workstream), work freely, open one PR when done, Sarmad merges. That's it.

2. **Locked remaining POC scope decisions:**
   - **Multi-symbol basket** — not single-ticker. The SOR routes orders across multiple symbols (AAPL, GOOGL, MSFT, AMZN, TSLA etc.), each venue maintains order books for all symbols. This is closer to real SOR behavior.
   - **Multi-symbol order books per venue** — confirmed. Each venue simulator serves quotes/books for all symbols in the basket.
   - **M1 diagrams** — user will drop them into `diagrams/` folder for reference.
   - **First coding step = shared Pydantic models** — confirmed.

3. **Created `diagrams/` folder** for M1 visual docs.

### Why multi-symbol matters

A single-ticker demo would show SOR routing one order to the cheapest venue. That's a price comparator, not a Smart Order Router. With a basket:
- Different symbols can have different best venues at the same time (AAPL best at V2, GOOGL best at V4).
- The risk engine tracks exposure across all positions, not just one.
- Kill switch affects all symbols — a realistic scenario.
- The dashboard shows a richer, more convincing trading view.

### Files changed

- **Rewritten:** `CONTRIBUTING.md` — from 350 lines to ~100. Three branches, simple loop, done.
- **Updated:** `PROGRESS.md` — new decisions (multi-symbol, simplified workflow), updated timestamp.
- **Created:** `diagrams/README.md` — placeholder for M1 diagrams.

### What's next

**Start coding.** First step: shared Pydantic models in `backend/shared/models/`.

---

## 2026-06-12 — Day 1 (continued): Full backend implementation

### What we did

Built the entire backend in a single session. Every service from the BACKEND_GUIDE is now implemented, wired, and tested end-to-end.

**Services built (bottom-up order):**

1. **Config** (`gateway/config.py`) — Pydantic Settings, venue profiles, all env vars with POC defaults.
2. **Logger** (`shared/utils/logger.py`) — structlog with console renderer for dev, JSON for prod.
3. **Event bus** (`shared/events/event_bus.py`) — In-memory async pub/sub. Replaces Redis for POC. Same interface so M3 swap is a drop-in.
4. **Auth** (`gateway/middleware/auth.py`) — Real JWT with 3 hardcoded users (admin/trader1/risk_mgr). Role-based access via `require_role()` decorator.
5. **Venue health monitor** (`services/venue_monitor/health_checker.py`) — Polls `/health` on all 5 venues every 1s. Computes health score (weighted: latency 25%, fill rate 25%, reject rate 20%, uptime 15%, freshness 15%). Transitions venues between HEALTHY/DEGRADED/CRITICAL/BLACKLISTED/DISCONNECTED.
6. **Market data collector** (`services/market_data/collector.py`) — Polls `/quote` on all venues for all symbols every 100ms. Marks stale quotes after 2s.
7. **Market data aggregator** (`services/market_data/aggregator.py`) — Computes NBBO per symbol from eligible (non-blacklisted, non-stale) venue quotes.
8. **Order manager** (`services/order_state/order_manager.py`) — In-memory order store. Enforces deterministic state machine transitions. Tracks child orders and fills.
9. **Position tracker** (`services/risk_engine/position_tracker.py`) — Tracks net position and notional per symbol.
10. **Kill switch** (`services/risk_engine/kill_switch.py`) — Activate/deactivate with reason tracking. Cancels all working orders on activation.
11. **Pre-trade risk engine** (`services/risk_engine/pre_trade.py`) — 7 checks in sequence: kill switch → size limit → rate limit → position limit → notional limit → symbol restriction → venue restriction. Microsecond-level latency (~23-43μs per check).
12. **Best-price routing strategy** (`services/routing_engine/strategies/best_price.py`) — Sorts venues by best ask (buy) or best bid (sell), greedily allocates quantity.
13. **Routing engine** (`services/routing_engine/engine.py`) — Orchestrates: validate → risk check → NBBO lookup → route → send to venues → handle fills. Updates positions on fill.
14. **WebSocket manager** (`gateway/websocket/ws_manager.py`) — Single multiplexed connection. Subscribe/unsubscribe per channel. All events auto-broadcast.
15. **8 API routers** — auth, orders, venues, market-data, risk, routing, admin, execution-reports. All per API_SPECIFICATION.md.
16. **Mock venue simulator** (`services/market_data/mock_venue.py`) — Runs all 5 venues locally for testing. GBM price engine per symbol, configurable latency/reject rates per venue profile.

### Architecture decisions in this session

- **Event bus wires WebSocket broadcasts automatically.** Every event published (venue_health, market_data, order_update, etc.) is broadcast to subscribed WebSocket clients. No manual broadcast calls in services.
- **Execution reports stored in-memory list.** M3 moves to Postgres. Same query interface.
- **Mock venues are realistic.** V3 (GammaMarkets) has 35% reject rate and 50-200ms latency. This exercises the health scoring, blacklisting, and error handling paths naturally.
- **Position tracker updates on fill, not on order creation.** Only confirmed fills change exposure. This matches real trading systems.

### Verified behavior

Full end-to-end test confirmed:
- Login → JWT → authenticated requests (3 roles)
- 5 venues reachable, health-scored, status tracked
- NBBO computed from live quotes across venues
- Orders routed to best-priced venue, filled, positions updated
- Kill switch blocks orders (503), cancels working orders
- Venue blacklist excludes from routing
- Role-based access (trader can't activate kill switch)
- Execution reports tracked with venue latency
- All 5 symbols (AAPL, GOOGL, MSFT, AMZN, TSLA) working

### What's next

- **Wait for teammates:** Frontend (Fizza's Figma drop-in) and venues (real simulator from spec).
- **Connect:** When teammates push, swap mock venues for real ones, wire frontend to live API.
- **Test integration:** End-to-end with real frontend + real venues + backend.
- **Deploy venues to AWS EC2** for POC demo with real network latency.

---

## 2026-06-11 — Day 0 (continued): Team guides + AWS-for-POC clarification

### What we did

Two changes after the initial scaffold:

1. Wrote `frontend/TEAM_GUIDE.md` and `venues/TEAM_GUIDE.md` — focused, opinionated working docs for the team members who own each folder.
2. Corrected the deployment scope for POC: the **venues (market simulators) DO get deployed to AWS** during POC (one EC2 VM per venue), so the panel can see real network latency between the platform and the exchanges. Earlier I had captured "local-first POC" which understated this — fixed in `PROGRESS.md` and the AI's memory.

### Why team guides instead of just READMEs

The READMEs we wrote in the scaffolding step describe what's in each folder. The TEAM_GUIDEs describe *how to work*: build order, conventions, gotchas, coordination protocol with other workstreams. Two different jobs, two different files.

The frontend guide assumes two devs working in parallel and splits the POC's 5 screens explicitly. It also walks Fizza through the one-time Figma Make drop-in step (separate branch, no folder reshaping, no code review of the auto-generated code — just "is the file list sane"). This avoids the common failure where the import PR is so large nobody reviews it properly and bad imports slip in.

The venues guide is structured as a phase-by-phase build order (one venue locally → replicate to five → tests → AWS deploy). One person is doing it solo so the guide focuses on sequencing — get one working before parallelizing, because a broken venue + four copies of it is much worse than one working venue.

### Why the AWS-for-POC change matters

If venues all ran on `localhost`, the latency differences between V2 (2–8ms) and V3 (50–200ms) would be entirely simulated via `asyncio.sleep()` calls. That works, but it's invisible to a panelist watching the demo — there's no "ah, V3 really is slower" moment.

With venues on separate EC2 VMs, the simulated latency stacks on top of real network round-trip. The Venue Health Monitor's P95 latency chart will show a genuine bimodal distribution between healthy and degraded venues. The blacklist demonstration becomes concrete: V3 *actually* gets unreachable when AWS marks the EC2 unhealthy, not just when we click a button.

This also means the venue dev's Phase 4 (Dockerfile + EC2 setup script + security groups + public DNS) is **mandatory POC work**, not deferred. Captured that in the venues TEAM_GUIDE explicitly.

### Files changed

- **Created:** `frontend/TEAM_GUIDE.md`, `venues/TEAM_GUIDE.md`.
- **Edited:** `PROGRESS.md` — restructured "What's next" into three parallel workstreams (A: backend, B: venues, C: frontend) since work can now happen concurrently. Updated decisions list with the AWS-for-POC clarification and the team split.

### What the next session should expect

Two viable starting points:

1. **Write the shared Pydantic models** in `backend/shared/models/`. Smallest scope, unblocks both the venue dev (response shapes) and the frontend devs (TypeScript types they can generate from the OpenAPI spec). Recommended next step.
2. **Init git, push the scaffold to GitHub.** Lets the team start pulling and working in parallel even before any code lands. User wanted GitHub sharing; this is when it happens.

Either is fine. Asking the user which they want first.
