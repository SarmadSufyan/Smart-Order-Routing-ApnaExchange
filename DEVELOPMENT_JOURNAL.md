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

---

## 2026-09-28 — Crypto pivot, Phase 0 + 1: the first real exchange (Binance testnet)

### What we did

The panel rejected simulated venues (see `notes/decisions/ADR-001-Crypto-Pivot.md`). This session replaced the simulated venue layer with a **real exchange connection** and proved it end to end:

- New `backend/exchanges/` package: an `ExchangeAdapter` contract, an exchange-agnostic `LocalOrderBook`, a registry of the five planned exchanges, and a working **Binance** adapter (testnet or mainnet).
- Market data, venue health, routing, and a new **unified wallet** service now read from adapters instead of HTTP-polling simulators.
- The router now **walks the consolidated order book** instead of taking top-of-book only.
- Frontend: crypto symbols, decimal quantities, adaptive price formatting, a real Wallet page, a permanent TESTNET/MAINNET badge, and several honesty fixes (below).
- Verified live: real testnet orders filled on Binance from both the API and the browser UI.

### How the live order book works (the core engineering)

An exchange's WebSocket doesn't send you the order book — it sends *changes* ("price 84,532.01 now has 2.1 BTC"). To know the book you must combine a REST **snapshot** with the stream of **diffs**, and that is easy to get subtly wrong. We implement Binance's documented procedure (`backend/exchanges/binance/book_sync.py`):

1. Open the stream and **buffer** diffs.
2. Fetch a snapshot; it carries `lastUpdateId = L`.
3. Drop buffered diffs already inside the snapshot (`u <= L`), apply the rest.
4. Every diff has a first/last update id `[U, u]`. Each must continue the chain (`U <= local_id + 1`). If one doesn't, **a message was lost and our book is wrong** — so we throw it away and resync from a fresh snapshot.

That gap check is the difference between "we display a book" and "we can trust the book we route on". It has 9 dedicated unit tests. On the live testnet the books synced in ~3 s with **0 gaps and 0 reconnects**.

### Why the router had to change: consolidated depth walk

The simulator router allocated `min(remaining, top-of-book size)` per venue. Against a real book with a single venue, any order bigger than the best level would come back PARTIAL even though Binance has plenty of liquidity one tick away. The real SOR algorithm (`strategies/best_price.py`):

```
levels = every (price, qty, venue) from every eligible venue's book side
sort levels best-first                       # O(L log L)
walk them, taking min(remaining, qty) floored to the venue's lot size
→ per-venue allocation + expected VWAP + slippage (bps)
```

With one exchange it walks Binance's book; when Coinbase lands in Phase 2 the **same code** splits across venues automatically. "Savings" is now honest: our VWAP vs. sending the whole order to the worst venue *alone* — which is zero when only one venue exists, and the routing notes say so rather than inventing a number.

### Design decisions worth defending to the panel

- **Child orders are MARKET or IOC LIMIT — never resting.** A SOR that leaves orders resting on several venues risks double fills. With IOC, every child finishes immediately (filled / partly filled / expired) and the remainder is reported back.
- **Idempotency via `clientOrderId`.** Each child order's UUID is sent as Binance's `newClientOrderId`. If the request times out we don't know whether it executed, so we *look it up* by that id instead of resending (resending could buy twice).
- **Lot-size rounding in `Decimal`.** Binance rejects 0.0012345 BTC (step is 0.00001). Floats can't represent most decimal steps (`0.1 + 0.2 != 0.3`), so rounding is done in `Decimal`, always DOWN, and the routing notes say "rounded 0.0012345 → 0.00123".
- **Health scoring only blames the venue for the venue's faults.** "Below $5 minimum", "insufficient balance" and "no API keys" are *our* mistakes — they no longer lower Binance's reject rate. A venue with no order history gets a neutral score instead of 0% fill rate (the old formula penalised a brand-new venue by 25%).
- **Quiet ≠ stale.** A market that isn't moving still has a live feed. Staleness is now judged on the connection (no messages for 10 s, or a book re-syncing), not on "the price didn't change".
- **"Degrade" on a real exchange** can't break Binance, so it degrades *our connection* (adds 350–650 ms to every REST call). The health monitor then measures that latency exactly as it would a genuinely slow link — the demo stays honest.
- **Money safety.** Keys are `SecretStr` (never appear in logs, reprs or errors). Mainnet keys are only read when `EXCHANGE_MODE=mainnet`, *and* mainnet also requires `ALLOW_MAINNET=true` or the gateway refuses to start. The UI shows TESTNET / MAINNET in the top bar at all times.
- **The wallet is read-only aggregation**, valued with mid prices from our *own* order books; funds stay custodied by the exchange (we deliberately don't call it "non-custodial"). Only the trading universe is tracked — the testnet account holds ~500 junk tokens, which are counted but hidden.

### Bugs found and fixed along the way

- The gateway **crashed at startup** once Binance keys were added to `.env` (the settings class rejected unknown keys). Settings now ignore unrelated keys; a test locks this in.
- The frontend **never subscribed** to any WebSocket channel, so every "real-time" update was silently dead and the UI only worked through 3-second polling. Fixed in `services/websocket.ts`.
- The frontend **fabricated p99 latency** as `latency × 2.5`. It now shows the measured p50/p99 from the health monitor.
- Kill Switch page showed a 0.90 health score as "1%"; position-limit check ignored order side (a SELL increased projected position); average entry price was just the last fill price. All fixed.
- Removed misleading UI: Time-in-Force and venue selectors that were never sent to the backend.

### Verification evidence (2026-09-28, Binance Spot testnet)

- 36 unit tests pass (book sync, order book, lot-size math, depth routing, risk, config safety).
- API: BUY 0.000123456 BTC → rounded to 0.00012 → FILLED @ $84,532.01 (Binance order id 6990106, 386 ms). SELL → FILLED. Wallet reconciled to the cent (net cost = the spread crossed, $0.00096).
- Marketable IOC limit filled with price improvement; below-minimum, over-limit, unsupported-symbol, blacklist and kill-switch paths all rejected with a clear reason.
- UI: logged in, placed BUY 0.0002 BTC from the New Order modal → filled @ $83,134.01 with routing proof; Wallet, Market Data and Venue pages all showed live data; zero console errors.

### Files changed

- **New:** `backend/exchanges/` (base, orderbook, precision, registry, binance/adapter, binance/book_sync), `backend/services/wallet/`, `backend/shared/models/wallet.py`, `backend/gateway/routers/wallet.py`, `backend/tests/test_exchanges/`, `backend/tests/test_routing/`, `backend/tests/test_risk_and_config.py`, `frontend/src/format.ts`, `scripts/check_binance_testnet.py`.
- **Rewritten:** gateway config, dependencies, main; market-data collector; venue health monitor; routing engine + best-price strategy; pre-trade risk; `start_all.py`; `frontend/src/pages/Wallet.tsx`.
- **Edited:** shared models (additive fields), order manager, position tracker, kill switch, routers, and frontend pages/components for symbols, formatting and honesty fixes.
- **Removed:** `backend/services/market_data/mock_venue.py` (retired simulator; preserved in git history and on `archive/venue-simulators`). The `venues/` folder is untouched but no longer used.

### What the next session should expect

Phase 2 — add **Coinbase** as V2. Because services only talk to `ExchangeAdapter`, that should be one new adapter (Coinbase uses sequence numbers rather than Binance's update ids, so it gets its own synchronizer) plus a line in the registry. One design question to settle first: testnet order books on different exchanges are unrelated to each other, so cross-venue NBBO on testnets may show artificial price gaps — decide whether Phase 2 compares books on mainnet public data while executing on testnet.

---

## 2026-09-28 — Phase 2: Coinbase + real-market data for comparison

### The decision and what it implies

Sarmad chose: **compare venues on real-market (mainnet) data, execute on testnet.** That splits one idea — "a venue" — into two: *where its order book comes from* and *where orders go*. The adapter contract now carries both (`data_mode`, `mode`) plus capability flags (`execution_available`, `can_trade`, `execution_note`).

### Finding: Coinbase has no real testnet

Checked, not assumed:
- The Coinbase **Advanced Trade sandbox** returns "static and pre-defined" mocked responses (Coinbase's own docs) — no matching engine. A live request returned a canned BTC-USD order at $70,000.44.
- The institutional **Exchange sandbox** lists 13 products and **no USDT pairs**, and needs a separate institutional sandbox account.

Routing orders into a mock and calling the reply a "fill" is fake execution — the exact thing the panel rejected. So **Coinbase is a data-only venue** in this phase: its real order book counts in the NBBO and appears in every routing proof, marked *excluded* with the reason. Execution on Coinbase comes with real money in Phase 8, built and tested then. We deliberately did not write untested code that could move real money.

### How Coinbase's feed differs from Binance's (and why it needs its own synchronizer)

| | Binance | Coinbase |
|---|---|---|
| Initial book | REST snapshot, merged with buffered stream | snapshot arrives **inside** the stream |
| Integrity check | per-symbol update-id chain `[U, u]` | one `sequence_num` for the **whole connection** |
| On a gap | resync that one symbol | any symbol's message could be lost → **reconnect** |
| Ask side called | `a` | `"offer"` |

`backend/exchanges/coinbase/book_sync.py` implements that model (6 unit tests). Live: 4 books (BNB isn't listed on Coinbase — skipped cleanly), 0 gaps, 0 reconnects.

### Price protection — the consequence of splitting data and execution

The router now decides on the **mainnet** book but fills happen on the **testnet** book. If those drift apart, a plain MARKET order could fill anywhere. So every MARKET parent now sends **IOC LIMIT children capped at the worst price the router planned to pay ± 50 bps** (`CHILD_PRICE_PROTECTION_BPS`). If the execution book has moved beyond the band, the child *expires* and the proof says "price protection: … had no liquidity within 50 bps". This is standard SOR practice ("marketable limit") and would be right even without the testnet split. The limit is rounded to the venue tick *toward* the planned price so the band is never exceeded.

### Measuring the split honestly: implementation shortfall

The routing proof now shows **expected VWAP (from the real-market book) vs actual fill**, in basis points. Live results: BTC 0.93 bps, ETH 0.49 bps, a SELL −1.35 bps (sold higher than expected). The testnet tracks mainnet closely, and the number proves it instead of us claiming it.

### Things the real data showed us immediately

- **Crossed markets are real:** BTC/USDT and ETH/USDT both showed Coinbase's bid *above* Binance's ask (a few bps). The NBBO spread goes negative and the UI flags "Crossed market". It's far smaller than the round-trip fees (Binance's standard spot taker fee alone is 0.1% = 10 bps, and Coinbase's retail taker fee is higher), which is exactly why PLAN.md frames the arbitrage feature as a *detector that nets fees*, not a profit machine.
- **Missed opportunity is now visible:** when Coinbase quotes a better price the router can't use, the notes say so ("Coinbase quoted a better bid, 0.5 bps better, but was skipped: no real testnet").
- **Coinbase's USDT books are thin** (BTC-USDT ~25 BTC/day vs ~4,500 BTC/day on BTC-USD). Comparing BTC-USD against BTC/USDT would mix in the USD/USDT basis, so we compare like with like.

### Smaller honesty fixes

- A venue with no order history showed "Fill Rate 0%" in red (reads as "everything failed"). It now shows "—" (new `orders_total` field).
- Routing-proof header now says excluded venues are listed after executable ones (a better-priced excluded venue sitting at rank 2 looked contradictory).

### A tooling lesson (Windows)

Vite's file watcher on Windows sometimes missed the *second* of two quick edits to the same file, so the browser ran a stale module even after a reload (the DATA ONLY tag was on disk but not served). We caught it by fetching the served module and diffing, not by trusting the screenshot. If the UI doesn't match the code, restart `npm run dev`.

### Files changed

- **New:** `backend/exchanges/coinbase/` (adapter, book_sync), `backend/tests/test_exchanges/test_coinbase_sync.py`.
- **Edited:** exchange base (capabilities, `data_mode`), Binance adapter (separate data/execution clients), registry, config (`market_data_mode`, `child_price_protection_bps`, both exchanges enabled), precision (`ceil_to_step`), routing engine (executable venues, exclusion reasons, price protection), best-price strategy (`limit_price`, missed-opportunity notes), health monitor, wallet, routing router, models (venue capability fields, `ChildOrder.limit_price`); frontend venue badges, order modal, market data, routing proof (shortfall), rate display.
- **Tests:** 46 passing.

### What the next session should expect

Phase 3 — Kraken, Bybit, OKX. Check each one's testnet reality the same way before building (Bybit has a real testnet; Kraken's spot "demo" and OKX's "demo trading" need verifying). Any venue without real execution joins as data-only, which the architecture now supports with no service changes.

---

## 2026-09-28 — Phase 3: every venue where orders really execute

### The goal

Sarmad asked for Bybit and, more broadly, "all real venues where we can execute orders". So the question was no longer "which exchanges exist" but **"on which testnets does an order really execute, at prices that mean something?"**

### Surveying the testnets — measured, not assumed

We probed every mainstream exchange that advertises a sandbox, comparing each testnet's order book with the real market for all five pairs (three rounds, 20 s apart):

| Testnet | Result |
|---|---|
| **OKX demo trading** | tracks the real market within ~1–3 bps on all 5 pairs, deep books |
| **Gate testnet** | tracks within ~0–10 bps on all 5 pairs, spot trading endpoints present |
| **Binance testnet** | tracks within ~1–5 bps (already in use) |
| **Bybit testnet** | real matching engine, but **its own market**: BTC ~+155–165 bps, ETH ~+85–92, XRP ~+430 with $89 of depth, BNB with no asks; only SOL tracks |
| Phemex | BTC at $49,000 on its testnet |
| HitBTC demo | BTC at $21,000, BTC is the only pair |
| Poloniex sandbox | down |
| Kraken | no spot testnet at all (futures demo only) |
| Coinbase | no real testnet (mocked sandbox) — see Phase 2 |

So the executable venues are **Binance, OKX, Gate and Bybit (SOL only)**. Coinbase stays in the code as an optional data-only venue but is off by default, as requested.

### The new mechanism: execution coherence

We decide on real-market books but execute on testnets. That's only meaningful where the testnet book tracks the real one. `services/venue_monitor/coherence.py` checks every 5 s, per venue and symbol:

```
divergence = (testnet mid / real-market mid − 1) × 10,000 bps
|divergence| ≤ 50 bps  -> routable
otherwise              -> excluded, with the reason shown in the routing proof
```

It excludes in **both** directions. A Bybit BTC book 165 bps *above* the market would make a BUY expire (price protection) — but it would make a SELL fill 165 bps *better than the real market ever offered*, which is a fake gain. Excluding both is the honest rule. Testnet prices are public, so coherence is measured even before API keys exist — the Venue page shows which symbols will work up front.

### Three integrity mechanisms, four venues

| Venue | Snapshot | Integrity check | On a break |
|---|---|---|---|
| Binance, Gate | REST, merged with buffered stream | `[U, u]` update-id chain | re-seed that symbol |
| Bybit | first message in the stream | delta `u` = previous + 1 | reconnect |
| OKX | first message in the stream | `prevSeqId` = previous `seqId` | reconnect |
| Coinbase | first message in the stream | one `sequence_num` for the whole connection | reconnect |

**A spec-vs-reality finding:** OKX documents a CRC32 checksum for its order-book channel, and we implemented it — it matched 0 of 73 messages. Debugging showed the server sends `checksum: 0`. Code that followed the spec to the letter would have rejected every OKX message. OKX integrity therefore relies on its sequence chain (0 gaps observed). Lesson: verify protocol claims against live traffic.

### Refactor: one execution core for every venue

`backend/exchanges/ccxt_venue.py` now holds everything that is the same on every exchange: IOC child orders, lot/notional validation, fee parsing, balances, the kill-switch sweep, reconciliation after network errors, and "poll until final" (Bybit and OKX acknowledge an order with just its id). Each venue file only implements its market-data feed. Binance was moved onto the core and re-verified with a real testnet fill.

**Client order ids** had to satisfy four rule sets at once (Binance ≤36, Bybit ≤36, OKX ≤32 alphanumeric, Gate ≤28 after a mandatory `t-` prefix — and ccxt checks Gate's limit *before* adding the prefix). The universal format is `sor` + 24 hex chars of the child UUID = 27 characters.

### What's verified and what isn't (yet)

- **Verified live:** all four feeds (20 books, 0 gaps, 0 reconnects), coherence verdicts, NBBO across four venues, a real Binance testnet fill through the new core, routing proofs listing all four venues with reasons.
- **Not yet verified:** order placement, fill polling, balances and reconciliation on **Bybit, OKX and Gate** — they need testnet/demo API keys. The code paths are written against ccxt's documented behaviour, but "written" is not "tested". `python scripts/check_exchange_keys.py` checks all keys in one go once they're added.

### Files changed

- **New:** `backend/exchanges/ccxt_venue.py`, `update_id_sync.py`, `stream_sync.py`, `bybit/`, `okx/`, `gate/`; `backend/services/venue_monitor/coherence.py`; `scripts/check_exchange_keys.py`; `backend/tests/test_exchanges/test_phase3.py`.
- **Rewritten:** Binance adapter (now a thin feed on the shared core), exchange registry.
- **Edited:** config (keys for Bybit/OKX/Gate, OKX passphrase, coherence settings, default venues), routing engine (per-symbol executable venues), health monitor (per-symbol coherence), models, dependencies, main; frontend venue coherence chips, Gate V6.
- **Tests:** 58 passing.

### Addendum (same day): Bybit moves from Testnet to Demo Trading

While writing the API-keys guide we checked Bybit's docs again and found a second environment: **Demo Trading** (`api-demo.bybit.com`), separate from Testnet. Measured side by side:

| BTC/USDT | ETH | SOL | BNB | XRP |
|---|---|---|---|---|
| Testnet **+170 bps** | +72 | 0 | no asks | **+410** |
| Demo Trading **0.0 bps** | −0.9 | 0.0 | 0.0 | −0.7 |

Bybit now uses Demo Trading (`CcxtVenueAdapter.test_environment = "demo"` → ccxt `enable_demo_trading`), and all five Bybit pairs pass the coherence check instead of only SOL. The coherence monitor is what made the Testnet problem visible in the first place — that's the mechanism working as intended. Venues now also report their own name for the environment ("testnet" vs "demo") in the UI and in error messages.

Full key setup for every venue: `project-info/API_KEYS_GUIDE.md`.

---

## 2026-09-29 — Live testing with real keys: five bugs only real venues could show

Sarmad added Bybit, OKX and Gate keys. Status after testing: **Binance ✅ and Bybit ✅ fully verified; Gate ❌ (secret incomplete); OKX ❌ (secret + passphrase missing, and the network blocks okx.com).** Every execution path was exercised against the real exchange systems (fake funds): IOC fills, fill polling, fees, reconciliation by client id, IOC expiry, minimum checks, kill-switch sweep of a genuinely resting order — then a real multi-venue split through the full gateway.

### What broke, and why it's worth telling the panel

1. **Venues have their own price limits.** Bybit rejected our protected BUY: *"Buy order price cannot be higher than 83436.4"*. Bybit publishes `priceLimitRatioX = 0.5%` per symbol; our 50 bps band sat on the edge. Fix: venues declare `max_limit_deviation_bps` (Bybit: 0.5% × 0.8 = 40 bps) and the engine uses the tighter of ours and theirs.
2. **Reconciliation must be exact, not a scan.** Looking a Bybit order up by our client id by scanning "recent closed orders" missed it: that list lags execution and excludes cancelled/expired IOCs. Bybit's API can query by `orderLinkId` directly; `_reconcile` (with retries) now finds filled *and* expired orders on both venues.
3. **Present-but-wrong keys looked tradable.** Gate's key was rejected, yet the router still allocated a child to Gate. Fix: credentials are verified at start-up and on every wallet refresh; an auth error makes the venue non-tradable with the exchange's own reason, and a fixed key re-enables it without a restart.
4. **Fees in the coin change the position.** Bybit took its buy fee in XRP: buying 731.7 delivered 730.9683, so selling 731.7 back failed with "insufficient balance". Positions now use the *net* base-asset change (`net_base_change`).
5. **Floats silently lose a lot step.** A 0.08 BNB slice went out as 0.0799 (float sums can land just under the grid: `0.1 + 0.7 == 0.7999999999999999`) and left the parent PARTIALLY_FILLED. Fix: de-noise to 12 decimals before rounding. Note: the first regression test "passed" without the fix because `0.03 + 0.05` happens to be exact — a test that passes without the fix proves nothing; it now uses the real trap.

### The big one: inventory is fragmented across venues

A SELL was sent entirely to the best-bid venue, but most of the coins sat on the other venue. With real exchanges, **each venue can only sell the coins it holds and only buy with the USDT it holds**. Now:
- Before routing, the wallet refreshes if anything traded (`ensure_fresh`), and each venue's **capacity** is computed (coins to sell, or USDT ÷ price to buy).
- The depth walk never gives a venue more than its capacity — verified live: *"Binance capped at 0.664 BNB by its available BNB balance"*, remainder filled on Bybit.
- A new **funding gate** rejects orders the venues can't fund, before touching any exchange: *"Insufficient balance: the venues can fund 3.89 of 10 BNB from their BNB (V1 0, V4 3.89)"*.

### Verified live

- Multi-venue BUY 4.058 BNB → Bybit 2.969 + Binance 1.089, **FILLED**; capacity-capped SELL 1.164 BNB → Binance 0.664 + Bybit 0.5, **FILLED**.
- 63 unit tests pass (new: Bybit reconciliation path verified live; capacity, net-of-fee and float-grid regressions).

### Network finding

`okx.com` stopped resolving on this network: the default DNS server *refuses* it while 1.1.1.1 / 8.8.8.8 resolve it (Bybit resolves normally through the same server) — an ISP-level block. That's an environment problem, not code, but it matters for a live panel demo: don't depend on OKX unless the demo network reaches it.

---

## 2026-09-29 — Phase 4: a book of record — audit log, P&L, reconciliation, portfolio risk

### First, the Gate key mystery

Gate's "Signature mismatch" wasn't a half-copied secret. `.env` had **two** `GATE_TESTNET_API_SECRET` lines: the real 64-character Gate secret, and — right under the OKX key — a 32-character uppercase-hex value, which is exactly what an OKX secret looks like. The OKX secret had been pasted under the Gate name, and the later line won. Renaming that one line fixed Gate (now authenticates; its testnet account needs test funds) and gave OKX its secret (it still needs the passphrase, and the network still blocks okx.com).

### Why Phase 4 was needed — three things found in the code

1. **A race that lost fills.** If the kill switch (or an operator cancel) landed while an IOC child was at the exchange, the late fill tried `CANCELLED → FILLED`, the state machine raised, and the position was never updated. The exchange filled it — reality must win. Now the fill is recorded on the cancelled parent and the status stays CANCELLED.
2. **Nothing survived a restart.** Orders, fills, positions, the kill switch and risk limits were all in memory. Worst case: an engaged kill switch was silently *released* by restarting the gateway.
3. **No cost basis, so no honest P&L.** Testnet accounts start pre-funded. Positions counted only our fills, so selling coins you already held showed up as "short BTC".

### What we built

**Audit store** (`backend/shared/database/store.py`). SQLite, embedded, one file per mode (`data/apnasor-testnet.db`; mainnet gets its own so fake and real records never mix). Zero setup for all four of us. Writes are queued and applied by **one** background writer in the order they happened, so a later snapshot of an order can never be overwritten by an earlier one, and the trading path never waits on disk. The SQL is plain so a PostgreSQL/TimescaleDB backend can implement the same interface later — PLAN §6 lists Timescale as a stretch; the audit log itself is core.

**Audit recorder** (`backend/services/audit/recorder.py`). It listens to the event bus, so no service had to learn that an audit log exists. Every order step, risk check, routing decision, child execution, kill-switch action, limit change, venue control, baseline change and gateway start/stop becomes one record: **who** (user, `risk-engine`, `router`, the venue, `risk-monitor`, `system`) and a **plain-English summary**. The order proof modal now shows the whole timeline; there is also an Audit Log page.

**Portfolio ledger + P&L** (`backend/services/wallet/ledger.py`, `pnl.py`). Average cost per venue and asset:
- *Opening balances* are captured automatically the first time a venue's balances are readable and fully priced, marked at our own mid prices. Never while an order is executing or a fill is settling (that would double-count it).
- Every fill after that is applied on top. Fees are handled as they really happen: in USDT (cash out), in the base coin (fewer coins), or in a third coin such as BNB.
- One identity holds exactly and is unit-tested: **value now − opening value = realized + unrealized − fees**.
- Split into **hold P&L** (the opening balances' market move — not our doing) and **trading P&L** (total − hold: what ApnaSOR's trades added versus just holding). This split matters for the panel: testnet accounts hold ~2 BTC, so a 0.1% BTC move swamps any trading result. Only trading P&L says anything about the router.

**Reconciliation.** Expected holdings (opening + fills) vs. what each exchange reports, per asset. A recent fill shows as *settling*, not as a break. A real break (testnet reset, manual trade, deposit) is flagged with the difference, and a risk manager can **accept the exchange balances** as a new baseline for that venue — audited, history kept.

**Portfolio risk monitor** (`backend/services/risk_engine/risk_monitor.py`), every 2 s:
- **Trading-loss limit** (default $1,000). A breach **engages the kill switch automatically**, and it can't be released while the loss is still beyond the limit — the operator must raise the limit or reset the baseline, a deliberate act that gets audited.
- **Concentration**: one non-stable asset above 60% of the portfolio, or one exchange holding more than 80% (counterparty risk — exchanges do fail). Alerts fire when a limit is first crossed, not every tick.

**Kill switch halts in-flight work.** Checked again right before every child is sent, not only at pre-trade. It cancels only orders being executed right now: an IOC parent left PARTIALLY_FILLED is finished (its remainder expired on the venue), so "cancelling" it would be fiction — the API now says so. The state is persisted and restored first on start-up: a halted gateway stays halted.

**Restart survival.** Orders (last 500), execution reports, fills, baselines, the kill switch and risk limits come back. An order that was mid-flight when the gateway stopped is closed with an honest reason ("check the exchange and the reconciliation for late fills").

### Design choices worth defending

- **Risk "positions" = net traded since the baseline**, rebuilt from the same fills as the ledger — the exposure *ApnaSOR* added. Holdings live on the Wallet page. Two clearly labelled views of one source, instead of one ambiguous number.
- **SQLite now, not Postgres.** A database every teammate must install is friction with no demo value yet. Swapping the backend later is one class.
- **Trading P&L, not total P&L, drives the loss limit.** Total P&L on pre-funded testnet accounts would trip the kill switch on ordinary BTC moves that have nothing to do with our trading.

### Verified live (Binance testnet + Bybit demo)

- Baselines captured automatically for Binance, Bybit and Gate; reconciliation **MATCHED** on all three.
- BUY 0.0002 BTC → Binance, SELL 0.0002 BTC → Bybit (fee 0.0167 USDT). Ledger and exchange agree to the cent after each fill; trading P&L = −$0.02, exactly the fee.
- The full 9-step audit timeline for each order, in order, with actors.
- Restart: kill switch still engaged, changed limit kept, orders, execution reports and fills restored.
- Loss limit set to $0.01 in the UI → kill switch engaged by `risk-monitor` within 2 s → release **refused** with the reason → limit restored → released. Every step is in the audit log.
- 90 unit tests (27 new: ledger identity and fee cases, store ordering/persistence, late fill after cancel, kill switch before child send, restart recovery, loss limit, concentration, reconciliation break vs. settling).

### Files

- **New:** `backend/shared/database/store.py`, `backend/shared/models/{audit,portfolio}.py`, `backend/services/audit/`, `backend/services/wallet/{ledger,pnl}.py`, `backend/services/risk_engine/risk_monitor.py`, `backend/gateway/routers/audit.py`, `backend/tests/test_portfolio/`, `frontend/src/pages/AuditLog.tsx`, `frontend/src/components/AuditTimeline.tsx`.
- **Changed:** order manager (in-flight tracking, late fills, restore), routing engine (per-child kill-switch gate, routing-decision event, restored stats), kill switch (restore), pre-trade risk (audited, persisted limits), gateway start-up (restore order: kill switch first), routers (P&L, baseline, audit, refused release, actors on every operator action), Wallet / Risk Manager / Kill Switch pages, order proof modal.

---

## 2026-10-01 — Phase 5a: the TWAP execution bot

### What a TWAP is, and why it's the first strategy

A **TWAP** (time-weighted average price) splits one big "parent" order into equal slices spread evenly over a time window. Sending 1 BTC at once walks deep into the book and moves the price against you; sending 0.1 BTC every 30 s lets the book refill between slices. It is the textbook first execution algorithm: simple enough to explain, and a real product feature.

### The rule that keeps it honest: the bot is just another client

Every slice is an **ordinary order through `OrderEntry.submit`** (`backend/services/order_state/entry.py`) — the same function the order button's REST endpoint now calls. So each slice gets pre-trade risk, the funding gate, best-price routing across venues and price-protected IOC children, with its own routing proof and audit trail. The bot never talks to an exchange. The LLM copilot will use the same entry point next.

### The algorithm (`backend/services/algos/twap.py`)

```
for i in 1..N:                                   # O(N) slices
    wait until slice i is due        # start + (i-1)·D/N, optional ± jitter
    if kill switch active: pause; stop
    qty = floor_to_lot(min(target_i - filled, 2 · Q/N, per-order cap))
    order = OrderEntry.submit(qty)   # routed: O(L log L) over L book levels
    filled += order.filled
status = COMPLETED if filled == Q else EXPIRED (shortfall reported)
```

- **Catch-up schedule.** Slice *i* aims for a *cumulative* Q·i/N, so a slice that fills short rolls its remainder into the next one — capped at 2× a normal slice so catching up never turns into a market-impact spike. Seen live: Bybit returned a network error on slice 4 (reconciliation confirmed the order was never executed); slice 5 asked for 2× and the TWAP still completed.
- **Jitter** (optional ±20% of the interval) makes the slice timing harder for other traders to spot. It's capped so slices never swap order.
- **Measured, not claimed.** Each TWAP reports its cost vs the **arrival price** (the market mid when it started — implementation shortfall) and vs the **interval TWAP** (the average market mid at the slice times — the benchmark a TWAP should beat or match). Both are in bps, positive = cost.

### Checks before it starts (so it fails in one second, not twenty minutes in)

- The slice must be at least the lot size and at least one venue's minimum order — the error says how many slices would work.
- The venues must hold enough to fund the whole parent (each slice is checked again).
- **New limit `max_algo_notional_usdt` (default $50,000).** Without it, slicing a $50k order into $5k pieces walks straight past the $10k per-order limit — a contradiction in the risk design that only appears once algos exist.

### Safety

- **Pause and cancel never interrupt a slice that is at the exchange** (a cancelled task mid-order could lose a fill); they take effect between slices.
- **Kill switch pauses every running TWAP**; resuming is refused until it's released.
- **Restart never resumes a TWAP** — it comes back PAUSED with the reason (same fail-safe as the kill switch).
- **Three slices in a row rejected before reaching any venue** (risk, funding, no liquidity) pause the TWAP with the reason instead of burning through the schedule. A slice that reaches a venue but finds no liquidity in its price band just rolls forward.

### A router bug the TWAP exposed

The depth walk never checked each venue's **minimum order** (lot and value). Big orders never hit it, but TWAP slices are small by design: a split could hand one venue a $3 sliver it rejects, leaving the slice partly unfilled. The router now drops a venue whose share would be below its minimum and walks again (at most once per venue), and says so in the routing proof.

### Verified live (Binance testnet + Bybit demo)

- BUY 0.0006 BTC in 3 slices (all Binance) and SELL 0.0006 BTC in 3 slices: COMPLETED, ~±1 bps vs arrival.
- From the UI: BUY 0.0006 BTC in 5 slices over 30 s, all on Bybit — the network-error catch-up above.
- Pause → nothing sent for 8 s → resume → kill switch paused it (actor `kill-switch`) → resume refused → released → resumed → COMPLETED. Every step is in the TWAP's audit timeline.
- After 17 real fills, Binance and Bybit still reconcile exactly.
- 107 unit tests (17 new: schedule math, catch-up, shortfall, kill switch, cancel, rejections, restart, validation; router minimum-size cases).

### Also found

- Market data looked "frozen" (BTC mid identical across slices). Checked against Binance's own public quote: identical, with book sizes changing every sample — just a quiet market at 02:30. No bug; good to have ruled out.
- Gate now returns "Invalid key provided" (the key was deleted or expired on Gate's side; `.env` unchanged) — regenerate it.

### Files

- **New:** `backend/services/order_state/entry.py`, `backend/services/algos/`, `backend/shared/models/strategy.py`, `backend/gateway/routers/algos.py`, `backend/tests/test_algos/`, `frontend/src/pages/AlgoOrders.tsx`, `frontend/src/components/TwapChart.tsx`.
- **Changed:** best-price strategy (venue minimums), routing engine (passes minimums), orders router (uses `OrderEntry`), order model (`parent_id`), risk limits (`max_algo_notional_usdt`), audit store (`algos` table), gateway start-up/shutdown (TWAPs load paused, stop between slices), Risk Manager (new limit), Audit Log (algo filter), order proof modal ("slice of TWAP-…").
