# Project Guide — read this first

> The single source of truth for anyone (teammate or coding assistant) working on this repo.
> **Then read [`PROGRESS.md`](PROGRESS.md)** (current state + who is working on what) and
> **[`project-info/ROADMAP.md`](project-info/ROADMAP.md)** (what's next, with acceptance criteria).
> Team workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md). New machine / new teammate: [`ONBOARDING.md`](ONBOARDING.md). History and the *why* behind every change: [`DEVELOPMENT_JOURNAL.md`](DEVELOPMENT_JOURNAL.md).

---

## Project identity

| Field | Value |
|---|---|
| Name | **ApnaSOR** — Apna Exchange's Smart Order Router (formerly DEIRCP) |
| Group | CS14 — Usman Institute of Technology (UITU), Final Year Project, 4 members |
| Supervisors | Sir Usman Javed, Miss Shiza |
| Domain | Crypto trading infrastructure: smart order routing, pre-trade risk, execution algos |
| Repo | https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange (**private**). Default branch `feature/crypto-pivot` (Sarmad merges into it at milestones). Working branches: `feature/backend-v2` (Fizza ⇄ Sarmad **relay**, one at a time) and `feature/frontend-v2` (Mahnoor + Maham, split by page) |

Team: Sarmad Sufyan Ahmed (23SP-078-CS, lead, backend) · Fizza Mubeen (23SP-070-CS) · Mahnoor Nadeem (23SP-065-CS) · Maham Ikram (23SP-101-CS).

## What it is (one paragraph)

A trader submits an order. ApnaSOR keeps live order books from **real crypto exchanges**, computes the NBBO, runs deterministic **pre-trade risk**, checks the venues can **fund** it, then walks the **consolidated order book** to split the order across venues at the best prices and sends **price-protected IOC child orders** to each exchange's **testnet / demo** environment (fake funds, real exchange systems). Fills update a **unified wallet**, a **P&L ledger** that is **reconciled** against each exchange, and an append-only **audit log**. A **risk monitor** (trading-loss limit → automatic kill switch, concentration alerts) watches the whole book, and a **TWAP bot** slices large orders over time through the same order path.

> **History:** until June 2026 this was a simulated 5-venue *stock* system. The FYP panel rejected simulated data, so the team pivoted to real crypto exchanges (July 2026). The simulators live on the `archive/venue-simulators` branch. **Several docs in `project-info/` still describe the old design — see "Docs you can trust" below.**

---

## Architecture (as built)

```
 Frontend (React 18 + TS + Vite, Zustand) ── REST + one WebSocket ──┐
                                                                    ▼
 Gateway (FastAPI, async) ── routers: orders · algos · venues · market-data · wallet · risk · audit · routing · auth
   │
   ├─ OrderEntry.submit()  ← the ONE way an order enters (REST endpoint, TWAP bot, future LLM copilot)
   │     └─ RoutingEngine: symbol → lot size → pre-trade risk → funding gate → best-price depth walk
   │                        → IOC LIMIT children (price-protected) sent concurrently → fills applied
   ├─ PreTradeRiskEngine (7 checks) · KillSwitch (persisted) · PositionTracker
   ├─ RiskMonitor (every 2 s): trading-loss limit → auto kill switch; concentration alerts
   ├─ MarketDataCollector + Aggregator (NBBO) · VenueHealthMonitor · CoherenceMonitor
   ├─ WalletService (real balances) · PnLService (opening balances + fills → P&L, reconciliation)
   ├─ TwapEngine (algo orders) · AuditRecorder → AuditStore (SQLite: data/apnasor-<mode>.db)
   │
 Exchange adapter layer (backend/exchanges/) — ccxt for REST execution + own WebSocket book sync
   V1 Binance  · V4 Bybit · V5 OKX · V6 Gate  (+ V2 Coinbase data-only, V3 Kraken impossible)
```

Internal events flow over an in-process async event bus (`backend/shared/events/event_bus.py`); the WebSocket fans selected channels out to the browser (`venue_health`, `market_data`, `nbbo_update`, `order_update`, `risk_alert`, `kill_switch`, `execution_report`, `wallet_update`, `algo_update`).

### Venues

| ID | Exchange | Order book from | Execution | Status (see PROGRESS.md for live state) |
|---|---|---|---|---|
| V1 | Binance | mainnet public feed | **spot testnet** | ✅ verified live |
| V4 | Bybit | mainnet public feed | **Demo Trading** (not Bybit Testnet — its books drift) | ✅ verified live |
| V5 | OKX | mainnet public feed | Demo Trading (needs key + secret + **passphrase**) | ❌ blocked by some ISPs' DNS; passphrase missing |
| V6 | Gate | mainnet public feed | spot testnet | ⚠️ authenticates; spot testnet wallet empty |
| V2 | Coinbase | mainnet public feed | none (no real testnet) — data-only, off by default | — |
| V3 | Kraken | — | no spot testnet | not possible |

Trading universe: **BTC, ETH, SOL, BNB, XRP vs USDT**, decimal quantities. `MARKET_DATA_MODE=mainnet` (real-market books for decisions) is independent of `EXCHANGE_MODE=testnet` (where orders execute); the coherence monitor excludes a venue+symbol whose test book drifts > 50 bps from the real market.

---

## Repository map (what actually exists)

```
backend/
  exchanges/            base.py (ExchangeAdapter, SymbolRules, ExecutionResult), ccxt_venue.py (shared execution
                        core: IOC, client-id idempotency, reconciliation, fees), orderbook.py, precision.py,
                        update_id_sync.py / stream_sync.py (book integrity), registry.py (EXCHANGE_CATALOG),
                        binance/ bybit/ okx/ gate/ coinbase/ (per-venue feed + quirks)
  gateway/              main.py (lifespan: open store → restore state → start services), config.py (Settings,
                        SecretStr keys, mainnet interlock), dependencies.py (wiring), middleware/auth.py
                        (JWT, hardcoded POC users), routers/*, websocket/ws_manager.py
  services/
    order_state/        order_manager.py (state machine, in-flight tracking, restore), entry.py (OrderEntry)
    routing_engine/     engine.py (pipeline + child execution), strategies/best_price.py (depth walk)
    risk_engine/        pre_trade.py, kill_switch.py, position_tracker.py, risk_monitor.py
    market_data/        collector.py, aggregator.py
    venue_monitor/      health_checker.py, coherence.py
    wallet/             portfolio.py (balances), ledger.py (pure P&L math), pnl.py (P&L + reconciliation)
    audit/              recorder.py (event bus → audit log)
    algos/              twap.py (TWAP engine; pure schedule math at the top)
    consensus/          EMPTY — Phase 6 home for the RAFT cluster
    execution/ policy/ risk/ routing/ venue_health/   empty legacy placeholders (unused)
  shared/               models/ (Pydantic v2), database/store.py (AuditStore), events/event_bus.py,
                        exceptions.py, utils/logger.py (structlog)
  tests/                unittest suites: test_exchanges, test_routing, test_portfolio, test_algos, ...
frontend/src/
  pages/                Dashboard, MarketData, AlgoOrders, Wallet, HowItWorks, VenueHealth, RiskManager,
                        KillSwitch, AuditLog, Login (OrderTicket is unrouted legacy)
  components/           AppShell (nav), NewOrderModal, OrderProofModal, RoutingProof, AuditTimeline,
                        TwapChart, shared.tsx
  services/api.ts       every REST call + response types      services/websocket.ts  one multiplexed WS
  stores/dataStore.ts   Zustand: all server state; WS events trigger refetches
  theme.ts / format.ts  colour + font tokens (C, F), number formatting (fmtPrice, fmtQty, fmtUsd, fmtPnl)
scripts/                check_exchange_keys.py (all venues), check_binance_testnet.py
project-info/           ROADMAP.md, API_KEYS_GUIDE.md (current) + older design docs (partly stale)
venues/ tests/ diagrams/ infra/   legacy from the simulated system — not used by the running app
start_all.py            starts gateway + frontend
data/                   local SQLite audit DB per machine (gitignored, created automatically)
```

---

## Run, test, verify

```powershell
pip install -r backend/requirements.txt          # + backend/requirements-dev.txt for tests
cd frontend; npm install; cd ..
copy .env.example .env                           # then add YOUR OWN testnet/demo keys (project-info/API_KEYS_GUIDE.md)
python scripts/check_exchange_keys.py            # which venues authenticate, with test balances
python start_all.py                              # gateway :8000 (/docs) + frontend :5173
python -m unittest discover -s backend/tests -t .   # all backend tests (107 as of 2026-10-01)
npx --prefix frontend tsc --noEmit -p frontend   # frontend type-check
```

POC logins (hardcoded in `backend/gateway/middleware/auth.py`): `admin` (ADMIN), `trader1` (TRADER), `risk_mgr` (RISK_MANAGER) — password = username.

**Verify on the real venues, not just in tests.** Every venue quirk so far (price-limit bands, fees in coins, lagging order history, float lot loss, fragmented inventory, venue minimums) was found live. Use small quantities (e.g. 0.0002 BTC).

---

## Non-negotiable design rules

1. **One way in.** Every order — button, TWAP, future copilot — goes through `OrderEntry.submit`. Never call an exchange adapter or `RoutingEngine.route_order` from a new feature directly.
2. **Testnet / demo only** until Phase 8. Mainnet needs `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; mainnet keys stay empty in dev.
3. **Children are IOC LIMIT, price-protected.** No resting orders; MARKET parents are capped at the worst planned price ± `CHILD_PRICE_PROTECTION_BPS` (and the venue's own price-limit rule).
4. **Reality wins.** A fill reported by an exchange is always recorded (even on a cancelled parent). Never "fix" a mismatch by editing the ledger — reconciliation shows it; an operator accepts exchange balances (audited).
5. **Fail-safe state.** The kill switch and risk limits are persisted; a restart keeps an engaged kill switch engaged and leaves running TWAPs **paused**.
6. **Never interrupt a call that is at the exchange** (no task cancellation mid-order). Pause/cancel take effect between steps.
7. **Everything is audited** with an actor and a plain-English summary (`backend/shared/events/event_bus.py: audit()`).
8. **Honest claims only** (the panel probes them): not "non-custodial" (exchanges custody funds; we read balances); arbitrage is a fee-aware *detector*; the value is integration, not a novel algorithm; only claim benchmarks we can show; testnet/demo = exchange-run fake funds.

## Coding conventions

**Python 3.11+** (developed on 3.14): `async def` everywhere in FastAPI; Pydantic v2 (`model_dump()`); type hints on every signature; business logic in services, routers stay thin; domain errors subclass `DomainError`; `structlog` logging (no `print` in services); Google-style docstrings on public functions; comments explain *why* (match the existing density); unit tests with `unittest` (`IsolatedAsyncioTestCase` for async); pure logic (math, schedules) kept in pure functions and tested directly.

**TypeScript**: strict mode; functional components; named exports; no `any`; styling is **inline styles with the theme tokens** `C` / `F` from `theme.ts` (no Tailwind, no CSS files); numbers through `format.ts`; server state in `dataStore.ts`, REST in `services/api.ts`.

**Git**: conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`); work only on your team's branch (`feature/backend-v2` or `feature/frontend-v2`, see CONTRIBUTING.md); only Sarmad merges into `feature/crypto-pivot`, with merge commits (never squash the long-lived branches); no pushes to `main`. **No AI/tool attribution anywhere** — no co-author trailers, no "generated with …" lines in commits, PRs, code or docs.

## Session protocol (every working session, human or assistant)

1. **Start:** `git pull` your branch (and `git merge origin/feature/crypto-pivot`); read this file, `PROGRESS.md` (*Who is working on what* — frontend: stay in your own pages; backend relay: read the **newest handoff note** first and continue from it), and the relevant `ROADMAP.md` section.
2. **Plan before building:** read the real code (docs can lag), list contradictions/risks, then build.
3. **Verify:** run the tests + type-check; exercise the feature live on testnet with a small order.
4. **End:** update your row in `PROGRESS.md` (backend relay: add a handoff note — done, half-done with file/function, exact next step) and append a dated entry to `DEVELOPMENT_JOURNAL.md` (what, why, design choices, files). Commit with a conventional message; push your branch. Never leave uncommitted work behind.

## Gotchas (each one cost us time)

- **Windows PowerShell 5.1 corrupts UTF-8 files** when you rewrite them with `Get-Content`/`Set-Content` (writes a BOM, turns `—` into `â€”`). Edit files with an editor or Python, not PowerShell pipelines.
- **Vite on Windows can miss new files** — restart `npm run dev` before trusting what the browser shows.
- **Each person needs their own exchange accounts/keys.** Two gateways trading the same testnet account see each other's fills as reconciliation BREAKs (each ledger only knows its own fills).
- **`data/apnasor-testnet.db` is per machine** (gitignored). Opening balances are captured automatically the first time your gateway reads your balances.
- **`.env`: the same variable twice → the later line wins.** That once made Gate fail with "Signature mismatch" (the OKX secret had been pasted under the Gate name).
- **OKX**: some ISPs' default DNS refuses `okx.com` — don't depend on OKX for a demo unless that network reaches it.
- **Gate**: its documented test funds go to the *futures* testnet and can't move to spot; ApnaSOR trades Gate spot.
- **Quiet markets look frozen**: an unchanged mid for several seconds at night is real (verified against Binance's public quote), not a stale feed.

## Docs you can trust

| Current (trust these) | Stale (old simulated / stock design — read critically, being rewritten) |
|---|---|
| this file, `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md`, `CONTRIBUTING.md`, `project-info/ROADMAP.md`, `project-info/API_KEYS_GUIDE.md`, the code | `project-info/ARCHITECTURE.md`, `API_SPECIFICATION.md`, `ALGORITHMS.md`, `BACKEND_GUIDE.md`, `FRONTEND_GUIDE.md`, `DATABASE_SCHEMA.md`, `DEPLOYMENT.md`, `MILESTONES.md`, `PROJECT_STRUCTURE.md`, `DEVELOPMENT_SETUP.md` |

The live API contract is the gateway's Swagger page: http://127.0.0.1:8000/docs.
