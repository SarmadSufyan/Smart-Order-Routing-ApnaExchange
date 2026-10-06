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

A trader submits an order. ApnaSOR keeps live order books from **real crypto exchanges**, computes the NBBO, runs deterministic **pre-trade risk**, checks the venues can **fund** it, then walks the **consolidated order book** to split the order across venues at the best prices and sends **price-protected IOC child orders** to each exchange's **testnet / demo** environment (fake funds, real exchange systems). Fills update a **unified wallet**, a **P&L ledger** that is **reconciled** against each exchange, and an append-only **audit log**. A **risk monitor** (trading-loss limit → automatic kill switch, concentration alerts) watches the whole book, and a **TWAP bot** slices large orders over time through the same order path. An **arbitrage detector** watches the real-market books for cross-exchange price gaps and measures them against the taker fees (detection only — it never trades).

> **History:** until June 2026 this was a simulated 5-venue *stock* system. The FYP panel rejected simulated data, so the team pivoted to real crypto exchanges (July 2026). The simulators live on the `archive/venue-simulators` branch. **Several docs in `project-info/` still describe the old design — see "Docs you can trust" below.**

---

## Architecture (as built)

```
 Frontend (React 18 + TS + Vite, Zustand) ── REST + one WebSocket ──┐
                                                                    ▼
 Gateway (FastAPI, async) ── routers: orders · algos · copilot · venues · market-data · wallet · risk · audit · routing · arbitrage · auth
   │
   ├─ OrderEntry.submit()  ← the ONE way an order enters (REST endpoint, TWAP bot, LLM copilot)
   │     └─ RoutingEngine: symbol → lot size → pre-trade risk → funding gate → best-price depth walk
   │                        → IOC LIMIT children (price-protected) sent concurrently → fills applied
   │                        → a venue's REFUSED quantity re-routed without it (≤ 2 rounds); a venue that
   │                          refuses our key's right to trade is paused for TRADING_BLOCK_COOLDOWN_S
   ├─ PreTradeRiskEngine (7 checks = one pure function, checks.py) · KillSwitch (persisted) · PositionTracker
   │     └─ risk batch: pre-flight of many orders (a TWAP's slices before it starts, POST /api/risk/preflight)
   │        + Monte Carlo stress test, on a pool of worker PROCESSES when the batch is big enough
   ├─ RiskControls ← the ONE way to change the kill switch, risk limits, venue blacklist
   │     └─ RAFT_ENABLED=true: written through the RAFT leader, committed by a majority; the gateway keeps
   │        a local copy for the order path (tighten = at once even without a majority; loosen = majority)
   ├─ RiskMonitor (every 2 s): trading-loss limit → auto kill switch; concentration alerts
   ├─ MarketDataCollector + Aggregator (NBBO) · VenueHealthMonitor · CoherenceMonitor
   ├─ WalletService (real balances) · PnLService (opening balances + fills → P&L, reconciliation)
   ├─ TwapEngine (algo orders) · AuditRecorder → AuditStore (SQLite: data/apnasor-<mode>.db)
   ├─ ArbitrageDetector (4 scans/s): every venue pair, both directions, gross vs after taker fees → windows +
   │     statistics (saved), profitable windows audited. Detection only: read-only lookups, cannot trade
   ├─ Copilot: sentence → LLM (Gemini free tier) fills a strict form → code validates → proposal
   │            → user confirms → OrderEntry / TwapEngine (the LLM never submits)
   │
 RAFT cluster (backend/services/consensus/, Phase 6) — 3 node processes on 127.0.0.1:9001-9003, each with its
   own SQLite log (data/raft-<mode>/); leader election + log replication; started by start_all.py when enabled
   │
 Exchange adapter layer (backend/exchanges/) — ccxt for REST execution + own WebSocket book sync
   V1 Binance  · V4 Bybit · V5 OKX · V6 Gate  (+ V2 Coinbase data-only, V3 Kraken impossible)
```

Internal events flow over an in-process async event bus (`backend/shared/events/event_bus.py`); the WebSocket fans selected channels out to the browser (`venue_health`, `market_data`, `nbbo_update`, `order_update`, `risk_alert`, `kill_switch`, `execution_report`, `wallet_update`, `algo_update`, `arb_update`). **`/ws` is for logged-in users only:** the first message must be `{"type": "auth", "token": …}` (never put a token in a URL — URLs are printed in the server log); anything else, or a bad / expired token, is closed with code 4401, and an open connection is closed when its login expires (`websocket/ws_manager.py`).

### Venues

| ID | Exchange | Order book from | Execution | Status (see PROGRESS.md for live state) |
|---|---|---|---|---|
| V1 | Binance | mainnet public feed | **spot testnet** | ✅ verified live |
| V4 | Bybit | mainnet public feed | **Demo Trading** (not Bybit Testnet — its books drift) | ✅ verified live |
| V5 | OKX | mainnet public feed | Demo Trading (needs key + secret + **passphrase**) | ⚠️ authenticates; orders get 50124 "no trading permission" (2026-10-02) — router pauses it; some ISPs' DNS blocks okx.com |
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
                        SecretStr keys, mainnet interlock, CORS rule), dependencies.py (wiring),
                        middleware/auth.py (users, logins, JWT signed with this machine's own key),
                        middleware/passwords.py (scrypt hashes), routers/*, websocket/ws_manager.py
  services/
    order_state/        order_manager.py (state machine, in-flight tracking, restore), entry.py (OrderEntry)
    routing_engine/     engine.py (pipeline + child execution), strategies/best_price.py (depth walk)
    risk_engine/        pre_trade.py (gate), checks.py (the 7 checks, pure), batch.py (pre-flight, stress test,
                        process pool), preflight.py (basket API), volatility.py (live realized vol),
                        controls.py (RiskControls: local or through RAFT), kill_switch.py, position_tracker.py,
                        risk_monitor.py
    market_data/        collector.py, aggregator.py
    venue_monitor/      health_checker.py, coherence.py
    wallet/             portfolio.py (balances), ledger.py (pure P&L math), pnl.py (P&L + reconciliation)
    audit/              recorder.py (event bus → audit log)
    algos/              twap.py (TWAP engine; pure schedule math at the top)
    arbitrage/          detector.py (5c: pure edge math + WindowTracker at the top, ArbitrageDetector service)
    agents/             copilot.py (propose / confirm / cancel), llm.py (one LLM interface; GeminiClient)
    consensus/          RAFT: core.py (the algorithm — no I/O, no clock, tick/step), storage.py (SQLite),
                        state_machine.py (kill switch / limits / blacklist), node.py (one node process:
                        python -m backend.services.consensus.node --id N), client.py (gateway + scripts),
                        wire.py (every message signed: HMAC-SHA256 + time stamp + one-time nonce)
    execution/ policy/ risk/ routing/ venue_health/   empty legacy placeholders (unused)
  shared/               models/ (Pydantic v2), database/store.py (AuditStore), events/event_bus.py,
                        exceptions.py, utils/logger.py (structlog)
  tests/                unittest suites: test_exchanges, test_routing, test_portfolio, test_algos, test_arbitrage, ...
frontend/src/
  pages/                Dashboard, MarketData, AlgoOrders, Wallet, HowItWorks, VenueHealth, RiskManager,
                        KillSwitch, AuditLog, Login (OrderTicket is unrouted legacy)
  components/           AppShell (nav), NewOrderModal, OrderProofModal, RoutingProof, AuditTimeline,
                        TwapChart, shared.tsx
  services/api.ts       every REST call + response types      services/websocket.ts  one multiplexed WS
  stores/dataStore.ts   Zustand: all server state; WS events trigger refetches
  theme.ts / format.ts  colour + font tokens (C, F), number formatting (fmtPrice, fmtQty, fmtUsd, fmtPnl)
scripts/                check_exchange_keys.py (all venues), check_binance_testnet.py, benchmark_risk_batch.py,
                        raft_demo.py (kill the leader; --gateway --twap 0.0006 against the live system),
                        arb_watch.py (arbitrage detector live in the terminal), hash_password.py (the .env
                        line for a real login password), security_check.py (tries the §7 attacks, read-only)
project-info/           ROADMAP.md, API_KEYS_GUIDE.md (current) + older design docs (partly stale)
venues/ tests/ diagrams/ infra/   legacy from the simulated system — not used by the running app
start_all.py            starts the RAFT nodes (if enabled), gateway + frontend
.github/workflows/ci.yml  CI on every push/PR to the 3 team branches: backend tests (Linux + Windows), ruff, tsc
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
python -m unittest discover -s backend/tests -t .   # all backend tests (295 as of 2026-10-06)
npx --prefix frontend tsc --noEmit -p frontend   # frontend type-check
python -m ruff check backend scripts start_all.py  # lint (rules: backend/pyproject.toml)
python scripts/raft_demo.py                      # RAFT: 3 nodes, kill the leader, nothing lost (standalone)
python scripts/benchmark_risk_batch.py --quick   # one process vs worker pool for the risk batch
```

Use the project venv's Python (`.venv/Scripts/python.exe`); a plain `python` on PATH may lack the packages.
**CI** (`.github/workflows/ci.yml`) runs the tests, ruff and the type-check on a clean Linux and Windows machine for
every push and PR to the team branches; its ✅/❌ is next to each commit on GitHub (details: the Actions tab).
**RAFT on:** set `RAFT_ENABLED=true` and your own `RAFT_SHARED_SECRET` (make one with
`python -c "import secrets; print(secrets.token_hex(32))"`) in `.env`, then `python start_all.py` starts the 3 nodes
before the gateway (a killed node is restarted after 15 s). Every RAFT message is signed with that secret.
`GET /api/consensus/status` shows leader, terms, log positions and refused messages.

Logins: `admin` (ADMIN), `trader1` (TRADER), `risk_mgr` (RISK_MANAGER). On a developer machine the **demo login** works
(password = user name; testnet only, never on mainnet). A real password is a scrypt hash in `.env`
(`python scripts/hash_password.py admin`) — **the demo laptop must have all three**, then `python scripts/security_check.py`
must say "Every attack was refused." Details, options and panel Q&A: `project-info/SECURITY_GUIDE.md`.

**Verify on the real venues, not just in tests.** Every venue quirk so far (price-limit bands, fees in coins, lagging order history, float lot loss, fragmented inventory, venue minimums) was found live. Use small quantities (e.g. 0.0002 BTC).

---

## Non-negotiable design rules

1. **One way in.** Every order — button, TWAP, copilot — goes through `OrderEntry.submit`. Never call an exchange adapter or `RoutingEngine.route_order` from a new feature directly. Likewise every change to the kill switch, risk limits or venue blacklist goes through `RiskControls` (`svc.risk_controls`), so it is replicated when RAFT is on.
2. **Testnet / demo only** until Phase 8. Mainnet needs `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; mainnet keys stay empty in dev.
3. **Children are IOC LIMIT, price-protected.** No resting orders; MARKET parents are capped at the worst planned price ± `CHILD_PRICE_PROTECTION_BPS` (and the venue's own price-limit rule).
4. **Reality wins.** A fill reported by an exchange is always recorded (even on a cancelled parent). Never "fix" a mismatch by editing the ledger — reconciliation shows it; an operator accepts exchange balances (audited).
5. **Fail-safe state.** The kill switch, risk limits and venue blacklist are persisted (and replicated with RAFT on); a restart keeps an engaged kill switch engaged and leaves running TWAPs **paused**. Without a RAFT majority, *tightening* (engage, blacklist) still happens at once; *loosening* (release, un-blacklist, limit changes) is refused.
6. **Never interrupt a call that is at the exchange** (no task cancellation mid-order). Pause/cancel take effect between steps.
7. **Everything is audited** with an actor and a plain-English summary (`backend/shared/events/event_bus.py: audit()`).
8. **Honest claims only** (the panel probes them): not "non-custodial" (exchanges custody funds; we read balances); arbitrage is a fee-aware *detector*; the value is integration, not a novel algorithm; only claim benchmarks we can show; testnet/demo = exchange-run fake funds.
9. **No secrets in the code.** Keys, signing secrets and password hashes live in `.env` or `data/` (per machine, gitignored); never a fixed default in the code; a bad value stops the start with the fix in the message. Only our own frontend may use the API from a browser (`CORS_ORIGINS`, the same rule on `/ws`). See `project-info/SECURITY_GUIDE.md`.

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
- **OKX**: some ISPs' default DNS refuses `okx.com`. Fix: set Windows DNS to `8.8.8.8` / `8.8.4.4` (verified 2026-10-02: the OKX API answers through Google DNS; Cloudflare 1.1.1.1 failed on the same network), then `ipconfig /flushdns` — Windows caches a failed lookup. Do it on the demo laptop too. Right after a DNS change, Bybit's WebSocket can hang for ~2 min (IPv6 addresses on a network without IPv6) and then recovers by itself.
- **Gate**: its documented test funds go to the *futures* testnet and can't move to spot; ApnaSOR trades Gate spot.
- **Windows + local networking (RAFT):** connecting to a closed `127.0.0.1` port takes ~2 s to fail, and `localhost` may try IPv6 first — always `127.0.0.1` and short timeouts. The venv's `python.exe` is a launcher: `Popen.pid` is not the real Python's pid (`os.getpid()`); killing the launcher kills its child.
- **Process pools on Windows** start workers by re-importing Python (~1 s cold start): keep the pool warm, and scripts that use one need `if __name__ == "__main__":`.
- **`IsolatedAsyncioTestCase` runs asyncio in debug mode** (a stack trace per callback): fine for most tests, but timer-heavy ones (RAFT over TCP) must `set_debug(False)` or elections never settle.
- **A VPN breaks the exchanges**: Binance answers 451 "restricted location", Bybit 403 (seen 2026-10-06). Turn it off for live tests and the demo.
- **Measuring layouts in an automated browser**: a hidden browser pane paints no frames, so `ResizeObserver` never fires and charts look stuck at their starting width. Force a frame (take a screenshot) after every resize before measuring.
- **Quiet markets look frozen**: an unchanged mid for several seconds at night is real (verified against Binance's public quote), not a stale feed.
- **A login with a real password takes ~1 s** (scrypt, slow on purpose; checks run one at a time). Not a bug. Tests must never depend on
  the passwords in your own `.env`: use `USERS[...]` and patch the settings (see `test_api/test_logins.py`).

## Docs you can trust

| Current (trust these) | Stale (old simulated / stock design — read critically, being rewritten) |
|---|---|
| this file, `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md`, `CONTRIBUTING.md`, `project-info/ROADMAP.md`, `project-info/API_KEYS_GUIDE.md`, `project-info/PHASE6_GUIDE.md` (risk batch + RAFT: explained, self-check, panel Q&A), `project-info/EXCHANGE_API_KEY_SECURITY.md` (how exchange API keys are protected), `project-info/SECURITY_GUIDE.md` (logins, signing key, CORS: options, decisions, demo-laptop checklist, panel Q&A), the code | `project-info/ARCHITECTURE.md`, `API_SPECIFICATION.md`, `ALGORITHMS.md`, `BACKEND_GUIDE.md`, `FRONTEND_GUIDE.md`, `DATABASE_SCHEMA.md`, `DEPLOYMENT.md`, `MILESTONES.md`, `PROJECT_STRUCTURE.md`, `DEVELOPMENT_SETUP.md` |

The live API contract is the gateway's Swagger page: http://127.0.0.1:8000/docs.
