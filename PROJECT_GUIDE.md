# Project Guide — read this first

> The single source of truth for anyone working on this repo.
> **Then read [`PROGRESS.md`](PROGRESS.md)** (current state + who is working on what) and
> **[`project-info/ROADMAP.md`](project-info/ROADMAP.md)** (what's next, with acceptance criteria).
> Team workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md). New machine / new teammate: [`ONBOARDING.md`](ONBOARDING.md). History and the *why* behind every change: [`DEVELOPMENT_JOURNAL.md`](DEVELOPMENT_JOURNAL.md).

---

## Project identity

| Field | Value |
|---|---|
| Name | **ApnaSOR** — Apna Exchange's Smart Order Router (formerly DEIRCP) |
| Group | CS14 — Usman Institute of Technology (UITU), Final Year Project, 4 members |
| Supervisor | Sir Shamaail Jafri |
| Domain | Crypto trading infrastructure: smart order routing, pre-trade risk, execution algos |
| Repo | https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange (**private**). **Since 2026-10-07 `feature/crypto-pivot` (the default branch) is the ONE working branch for everyone** — backend and frontend, **one person at a time** (a relay; Sarmad decides who). `feature/backend-v2` / `feature/frontend-v2` are retired; `theme/liquid-glass` keeps Mahnoor + Maham's redesign as a theme, not the product |

Team: Sarmad Sufyan Ahmed (23SP-078-CS, lead, backend) · Fizza Mubeen (23SP-070-CS) · Mahnoor Nadeem (23SP-065-CS) · Maham Ikram (23SP-101-CS).

## What it is (one paragraph)

A trader submits an order. ApnaSOR keeps live order books from **real crypto exchanges**, computes the NBBO, runs deterministic **pre-trade risk**, checks the venues can **fund** it, then splits it across venues with a **routing strategy** (best price including fees, liquidity sweep, or smart) and sends **price-protected IOC child orders** to each exchange's **testnet / demo** environment (fake funds, real exchange systems). Large orders can be worked over time by an **execution algorithm** — TWAP, VWAP, implementation shortfall, POV or iceberg — or **Auto** picks one and explains why. Fills update a **unified wallet**, a **P&L ledger** that is **reconciled** against each exchange, and an append-only **audit log**. A **risk monitor** (trading-loss limit → automatic kill switch, concentration alerts) watches the whole book; every execution algorithm's children go through the same order path. **Research tools** forecast the next minute / hour / day with probabilities and a public track record, backtest rules with fees, study which classic trading techniques worked on years of history, and plan trades — read-only. A **copilot** answers in plain English from the live numbers and the research, analyses coins (BUY / NO TRADE / REDUCE with its reasons) and proposes orders, algorithms and bots — a person confirms. **Bots** trade from an **automation fund** the owner sets aside (kept apart from manual trading): a **profit-cycle bot** (sells each lot only above cost + both fees + a threshold) and an **advisor agent** (proposes bracketed trades with its reasons; auto-approve on testnet only); a **practice lab** learns from every bot trade (measured costs, variant scoreboard, outcome models). An **incident guard** reads whether money can leave each exchange and whether its prices still agree with the others: an exchange in trouble leaves routing automatically, orders fill on the rest, and only a person lets it back (it limits new exposure; it can't stop a hack or bring money back). An **arbitrage detector** watches the real-market books for cross-exchange price gaps and measures them against the taker fees (detection only — it never trades). **Real money** (Phase D, started 2026-10-12) uses the same path behind extra rails: an exchange gets orders only once it confirms our key can't withdraw, and hard caps in `.env` that no screen can loosen (`project-info/REAL_MONEY_GUIDE.md`).

> **History:** until June 2026 this was a simulated 5-venue *stock* system. The FYP panel rejected simulated data, so the team pivoted to real crypto exchanges (July 2026). The simulators live on the `archive/venue-simulators` branch. **Several docs in `project-info/` still describe the old design — see "Docs you can trust" below.**

---

## Architecture (as built)

```
 Frontend (React 18 + TS + Vite, Zustand) ── REST + one WebSocket ──┐
                                                                    ▼
 Gateway (FastAPI, async) ── routers: orders · algos · copilot · venues · market-data · wallet · risk · audit · routing · arbitrage · auth
   │
   ├─ OrderEntry.submit()  ← the ONE way an order enters (REST endpoint, TWAP bot, LLM copilot)
   │     └─ RoutingEngine: symbol → lot size → pre-trade risk → funding gate → the order's routing strategy
   │                        (best price incl. fees · liquidity sweep under one price cap · smart = + fill reliability)
   │                        → IOC LIMIT children (price-protected) sent concurrently → fills applied
   │                        → a venue's REFUSED quantity re-routed without it (≤ 2 rounds); a venue that
   │                          refuses our key's right to trade is paused for TRADING_BLOCK_COOLDOWN_S
   ├─ PreTradeRiskEngine (7 checks = one pure function, checks.py) · KillSwitch (persisted) · PositionTracker
   │     └─ real money: every limit clamped to the .env caps (MAINNET_MAX_*, ceilings.py); a change past one refused
   │     └─ risk batch: pre-flight of many orders (a TWAP's slices before it starts, POST /api/risk/preflight)
   │        + Monte Carlo stress test, on a pool of worker PROCESSES when the batch is big enough
   ├─ RiskControls ← the ONE way to change the kill switch, risk limits, venue blacklist
   │     └─ RAFT_ENABLED=true: written through the RAFT leader, committed by a majority; the gateway keeps
   │        a local copy for the order path (tighten = at once even without a majority; loosen = majority)
   ├─ RiskMonitor (every 2 s): trading-loss limit → auto kill switch; concentration alerts
   ├─ MarketDataCollector + Aggregator (NBBO) · VenueHealthMonitor · CoherenceMonitor
   ├─ IncidentGuard: what each exchange says about withdrawals + its prices vs the others → an exchange in trouble is
   │     blacklisted through RiskControls (automatic; lifting is a person's), orders fill on the rest, the proof shows
   │     the price passed up. Fire drill in the sandbox                       (project-info/INCIDENT_GUARD_GUIDE.md)
   ├─ WalletService (real balances) · PnLService (opening balances + fills → P&L, reconciliation)
   ├─ AlgoEngine: TWAP · VWAP · IS (Almgren–Chriss) · POV · ICEBERG + Auto recommendation — every child through OrderEntry
   │     └─ MarketHistory: real-market 1-min candles (public data, all exchanges summed) for VWAP profiles, POV volume,
   │        the market-VWAP benchmark                                    (project-info/ALGORITHMS_GUIDE.md)
   ├─ ResearchService: reference-market candles (data/market-history.db: 5 y hourly, ~8 y daily, public data) → indicators,
   │     forecasts (P(up) next minute / hour / day, walk-forward tested, live track record), backtests, technique study
   │     (6 classic techniques vs holding, after fees), trade plans — read-only           (project-info/RESEARCH_GUIDE.md)
   ├─ AutomationEngine (Phase C): automation fund (a pocket per bot; OrderEntry's gate keeps bots in their pockets and
   │     manual orders off the fund) · profit-cycle bot · advisor agent · practice lab (samples → costs, scoreboard,
   │     outcome models) — every bot order through OrderEntry                    (project-info/AUTOMATION_GUIDE.md)
   ├─ AuditRecorder → AuditStore (SQLite: data/apnasor-<mode>.db)
   ├─ ArbitrageDetector (4 scans/s): every venue pair, both directions, gross vs after taker fees → windows +
   │     statistics (saved), profitable windows audited. Detection only: read-only lookups, cannot trade
   ├─ Copilot: sentence → LLM (Gemini or Claude) fills a strict form → code validates → an order / algo / bot proposal
   │            → user confirms (Auto: small, sandbox only) → OrderEntry / AlgoEngine / bots (the LLM never submits);
   │            questions → facts from the live services + the ANALYST (pure rules on the research) → LLM words them,
   │            no numbers of its own (code words them if the LLM fails)            (project-info/COPILOT_GUIDE.md)
   │
 RAFT cluster (backend/services/consensus/, Phase 6) — 3 node processes on 127.0.0.1:9001-9003, each with its
   own SQLite log (data/raft-<mode>/); leader election + log replication; started by start_all.py when enabled
   │
 Exchange adapter layer (backend/exchanges/) — ccxt for REST execution + own WebSocket book sync;
   real money: a venue trades only once its exchange confirms the key can't withdraw (key_permissions.py)
   V1 Binance  · V4 Bybit · V5 OKX · V6 Gate · V7 HTX (real money only)  (+ V2 Coinbase data-only, V3 Kraken impossible)
```

Internal events flow over an in-process async event bus (`backend/shared/events/event_bus.py`); the WebSocket fans selected channels out to the browser (`venue_health`, `market_data`, `nbbo_update`, `order_update`, `risk_alert`, `kill_switch`, `execution_report`, `wallet_update`, `algo_update`, `arb_update`). **`/ws` is for logged-in users only:** the first message must be `{"type": "auth", "token": …}` (never put a token in a URL — URLs are printed in the server log); anything else, or a bad / expired token, is closed with code 4401, and an open connection is closed when its login expires (`websocket/ws_manager.py`).

### Venues

| ID | Exchange | Order book from | Execution | Status (see PROGRESS.md for live state) |
|---|---|---|---|---|
| V1 | Binance | mainnet public feed | **spot testnet** | ✅ verified live |
| V4 | Bybit | mainnet public feed | **Demo Trading** (not Bybit Testnet — its books drift) | ✅ verified live |
| V5 | OKX | mainnet public feed | Demo Trading (needs key + secret + **passphrase**) | ⚠️ authenticates; orders get 50124 "no trading permission" (2026-10-02) — router pauses it; some ISPs' DNS blocks okx.com |
| V6 | Gate | mainnet public feed | spot testnet | ⚠️ authenticates; spot testnet wallet empty |
| V7 | HTX | mainnet public feed (gzip `mbp.refresh.20` snapshots) | **real money only** (no sandbox: data-only on testnet) | added 2026-10-24 (Phase D, PVARA NOC in Pakistan); live data verified (5/5 books, within ±4 bps of Binance) |
| V2 | Coinbase | mainnet public feed | none (no real testnet) — data-only, off by default | — |
| V3 | Kraken | — | no spot testnet | not possible |

Trading universe (since 2026-10-25): **20 coins vs USDT** — BTC, ETH, SOL, XRP, BNB, DOGE, ADA, TRX, LINK, AVAX, LTC, DOT, NEAR, SUI, UNI, AAVE, XLM, HBAR, APT, ARB — the most traded of the 147 USDT coins all four real-money exchanges list (`SYMBOLS`; the web app reads it from `GET /api/market-data/symbols`), decimal quantities. `MARKET_DATA_MODE=mainnet` (real-market books for decisions) is independent of `EXCHANGE_MODE=testnet` (where orders execute); the coherence monitor excludes a venue+symbol whose test book drifts > 50 bps from the real market.

---

## Repository map (what actually exists)

```
backend/
  exchanges/            base.py (ExchangeAdapter, SymbolRules, ExecutionResult), ccxt_venue.py (shared execution
                        core: IOC, client-id idempotency, reconciliation, fees), key_permissions.py (real money:
                        what the key may do, per exchange, pure parsers + the verdict, incl. the IP lock), transfers.py
                        (are deposits and withdrawals open, per coin: pure parsers), fees.py
                        (the account's own taker fee + LiveFees, the shared fee view), orderbook.py, precision.py,
                        update_id_sync.py / stream_sync.py (book integrity), registry.py (EXCHANGE_CATALOG),
                        binance/ bybit/ okx/ gate/ htx/ coinbase/ (per-venue feed + quirks)
  gateway/              main.py (lifespan: open store → restore state → start services), config.py (Settings,
                        SecretStr keys, mainnet interlock, CORS rule), dependencies.py (wiring),
                        middleware/auth.py (users, logins, JWT signed with this machine's own key),
                        middleware/passwords.py (scrypt hashes), routers/*, websocket/ws_manager.py
  services/
    order_state/        order_manager.py (state machine, in-flight tracking, restore), entry.py (OrderEntry)
    routing_engine/     engine.py (pipeline + child execution, per-venue fill ratios), strategies/: best_price.py
                        (fee-aware depth walk), sweep.py (proportional, one price cap), smart.py (fill reliability)
    risk_engine/        pre_trade.py (gate), checks.py (the 7 checks, pure), ceilings.py (real-money caps, pure),
                        batch.py (pre-flight, stress test,
                        process pool), preflight.py (basket API), volatility.py (live realized vol),
                        controls.py (RiskControls: local or through RAFT), kill_switch.py, position_tracker.py,
                        risk_monitor.py
    market_data/        collector.py, aggregator.py, history.py (real-market 1-min candles, merged across exchanges)
    venue_monitor/      health_checker.py, coherence.py, incident_guard.py (signals -> level -> block, pure rules + service)
    wallet/             portfolio.py (balances), ledger.py (pure P&L math), pnl.py (P&L + reconciliation)
    audit/              recorder.py (event bus → audit log)
    alerts/             telegram.py (phone alerts from the audit summaries; /stop and /block only tighten, never release)
    algos/              engine.py (AlgoEngine: every kind + auto), schedules.py (pure maths: VWAP profile, Almgren–
                        Chriss, POV, iceberg, auto choice, min-slice merge), twap.py (old import path)
    arbitrage/          detector.py (5c: pure edge math + WindowTracker at the top, ArbitrageDetector service)
    research/           candles.py (CandleStore + CandleSync + history backfill), indicators.py, model.py (features,
                        logistic, walk-forward, scoring, ranges), signals.py, backtest.py, techniques.py (technique
                        study), sizing.py (trade plan), service.py — all maths pure
    automation/         fund.py (automation fund + the order gate), cycle.py / advisor.py (pure decisions), lab.py
                        (practice lab: samples, costs, scoreboard, outcome models, presets), engine.py (runs the bots)
    agents/             copilot.py (planner → proposals / answers, confirm / cancel, sandbox Auto), analyst.py (pure
                        verdict rules + facts per topic), llm.py (one LLM interface; GeminiClient, ClaudeClient)
    consensus/          RAFT: core.py (the algorithm — no I/O, no clock, tick/step), storage.py (SQLite),
                        state_machine.py (kill switch / limits / blacklist), node.py (one node process:
                        python -m backend.services.consensus.node --id N), client.py (gateway + scripts),
                        wire.py (every message signed: HMAC-SHA256 + time stamp + one-time nonce)
    execution/ policy/ risk/ routing/ venue_health/   empty legacy placeholders (unused)
  shared/               models/ (Pydantic v2), database/store.py (AuditStore), events/event_bus.py,
                        exceptions.py, utils/logger.py (structlog), utils/asyncio_fixes.py (Windows connection-reset fix)
  tests/                unittest suites: test_exchanges, test_routing, test_portfolio, test_algos, test_arbitrage, ...
frontend/src/
  pages/                Dashboard, MarketData, Research, AlgoOrders, Automation (Bots & Lab), Wallet, HowItWorks, VenueHealth, RiskManager,
                        KillSwitch, AuditLog, Login (OrderTicket is unrouted legacy)
  components/           AppShell (nav), CopilotPanel (the Copilot drawer), NewOrderModal, OrderProofModal, RoutingProof, AuditTimeline,
                        TwapChart, shared.tsx (Card, PageHeader, Button, Stat, Pill), Orb.tsx (thinking orbs: Orb, Loading, Thinking)
  services/api.ts       every REST call + response types      services/websocket.ts  one multiplexed WS
  stores/dataStore.ts   Zustand: all server state; WS events trigger refetches
  theme.ts / format.ts  colour + font tokens (C, F), number formatting (fmtPrice, fmtQty, fmtUsd, fmtPnl)
scripts/                check_exchange_keys.py (all venues; --mode mainnet = your real keys, read-only),
                        check_binance_testnet.py, benchmark_risk_batch.py,
                        raft_demo.py (kill the leader; --gateway --twap 0.0006 against the live system),
                        arb_watch.py (arbitrage detector live in the terminal), hash_password.py (the .env
                        line for a real login password), security_check.py (tries the §7 attacks, read-only),
                        lab_status.py (bots + lab from a database, read-only; --db for a backup),
                        export_records.py (a month's fills + orders as CSV, read-only),
                        backup_databases.py (online SQLite copies, gzipped, kept N days)
deploy/real-server/     the REAL-MONEY server (Phase D): deploy.py (init, replace-ssh-key, passwords, setup = rehearsal with
                        sandbox keys, keys = real keys via a hidden prompt -> root-only keys.env, check-keys, telegram,
                        ai-key = the copilot's Claude key (decision A5; off until typed),
                        go-live, release with the promotion gate, status, tunnel 28000, backup-pull, rollback),
                        check_settings.sh (each stage's rules), install_release.sh, write_keys.py, systemd/ units
                        (project-info/REAL_MONEY_GUIDE.md parts 3-7, PHASE_D_PLAN.md)
deploy/lab-server/      the 24/7 lab server on AWS (bots + practice lab, testnet only): deploy.py (laptop: init,
                        setup, release, env, passwords, status, logs, tunnel, backup-pull, rollback), setup.sh,
                        install_release.sh (backup -> install -> switch -> healthy or roll back), systemd/ units,
                        requirements.lock (exact Linux packages)                (project-info/LAB_SERVER_GUIDE.md)
project-info/           ROADMAP.md, API_KEYS_GUIDE.md (current) + older design docs (partly stale)
venues/ tests/ diagrams/ infra/   legacy from the simulated system — not used by the running app
start_all.py            starts the RAFT nodes (if enabled; waits for a leader), gateway + frontend; refuses a second
                        start (ports in use); Ctrl+C stops the gateway cleanly first, then everything else
                        (project-info/START_STOP_GUIDE.md)
.github/workflows/ci.yml  CI on every push/PR to the 3 team branches: backend tests (Linux + Windows), ruff, tsc;
                        lab-server-kit.yml / real-server-kit.yml deploy each server kit to a clean Ubuntu 24.04
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
python -m unittest discover -s backend/tests -t .   # all backend tests (650 as of 2026-10-30)
npx --prefix frontend tsc --noEmit -p frontend   # frontend type-check
python -m ruff check backend scripts start_all.py  # lint (rules: backend/pyproject.toml)
python scripts/raft_demo.py                      # RAFT: 3 nodes, kill the leader, nothing lost (standalone)
python scripts/benchmark_risk_batch.py --quick   # one process vs worker pool for the risk batch
python deploy/lab-server/deploy.py status        # the 24/7 lab server on AWS (project-info/LAB_SERVER_GUIDE.md)
python deploy/real-server/deploy.py status       # the real-money server (project-info/REAL_MONEY_GUIDE.md parts 3-7)
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
2. **Testnet / demo by default; real money only through the Phase D rails** ([`project-info/REAL_MONEY_GUIDE.md`](project-info/REAL_MONEY_GUIDE.md)). Mainnet needs `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; each key must be trade-only (the exchange is asked; a key that can withdraw gets no orders); the `.env` caps (`MAINNET_MAX_*`) are never loosened from a screen; mainnet keys stay empty on development machines and never reach the lab server.
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

**Git**: conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`); work on `feature/crypto-pivot` and only while you hold the relay (CONTRIBUTING.md); push each finished piece so the next person can continue; never squash or rewrite pushed history; no pushes to `main`. **No AI/tool attribution anywhere** — no co-author trailers, no "generated with …" lines in commits, PRs, code or docs.

## Session protocol (every working session)

1. **Start:** `git checkout feature/crypto-pivot` + `git pull`; read this file, `PROGRESS.md` (the **newest handoff note** first — continue from its "next step"), and the relevant `ROADMAP.md` section.
2. **Plan before building:** read the real code (docs can lag), list contradictions/risks, then build.
3. **Verify:** run the tests + type-check; exercise the feature live on testnet with a small order.
4. **End:** add a handoff note at the top of the relay table in `PROGRESS.md` (done, half-done with file/function, exact next step) and append a dated entry to `DEVELOPMENT_JOURNAL.md` (what, why, design choices, files). Commit with a conventional message; push `feature/crypto-pivot`; CI must be ✅. Never leave uncommitted work behind.

## Gotchas (each one cost us time)

- **Windows PowerShell 5.1 corrupts UTF-8 files** when you rewrite them with `Get-Content`/`Set-Content` (writes a BOM, turns `—` into `â€”`). Edit files with an editor or Python, not PowerShell pipelines.
- **Vite on Windows can miss new files** — restart `npm run dev` before trusting what the browser shows.
- **The team shares one set of testnet keys (decision 2026-10-06)**, so teammates' trades show as reconciliation BREAKs on your Wallet (each ledger only knows its own fills) — expected, don't chase them. Before a demo, only the demo laptop trades and it accepts the exchange balances once. Real-money keys are never shared. Rules: CONTRIBUTING.md *Exchange keys*.
- **`data/apnasor-testnet.db` is per machine** (gitignored). Opening balances are captured automatically the first time your gateway reads your balances.
- **`.env`: the same variable twice → the later line wins.** That once made Gate fail with "Signature mismatch" (the OKX secret had been pasted under the Gate name).
- **OKX**: some ISPs' default DNS refuses `okx.com`. Fix: set Windows DNS to `8.8.8.8` / `8.8.4.4` (verified 2026-10-02: the OKX API answers through Google DNS; Cloudflare 1.1.1.1 failed on the same network), then `ipconfig /flushdns` — Windows caches a failed lookup. **Some ISPs intercept plain DNS to any server** (2026-10-25, Sarmad's home: an `okx.com` lookup to 8.8.8.8 timed out, while OKX itself answered once resolved): then turn on Windows 11's **DNS over HTTPS** (DNS server assignment -> Manual -> 1.1.1.1 / 1.0.0.1, *DNS over HTTPS: On (automatic template)*). Do it on the demo laptop too. Right after a DNS change, Bybit's WebSocket can hang for ~2 min (IPv6 addresses on a network without IPv6) and then recovers by itself.
- **Gate**: its documented test funds go to the *futures* testnet and can't move to spot; ApnaSOR trades Gate spot.
- **Windows + local networking (RAFT):** connecting to a closed `127.0.0.1` port takes ~2 s to fail, and `localhost` may try IPv6 first — always `127.0.0.1` and short timeouts. The venv's `python.exe` is a launcher: `Popen.pid` is not the real Python's pid (`os.getpid()`); killing the launcher kills its child.
- **Process pools on Windows** start workers by re-importing Python (~1 s cold start): keep the pool warm, and scripts that use one need `if __name__ == "__main__":`.
- **`IsolatedAsyncioTestCase` runs asyncio in debug mode** (a stack trace per callback): fine for most tests, but timer-heavy ones (RAFT over TCP) must `set_debug(False)` or elections never settle.
- **A VPN breaks the exchanges**: Binance answers 451 "restricted location", Bybit 403 (seen 2026-10-06). Turn it off for live tests and the demo.
- **Windows asyncio and reset connections:** a connection reset at the wrong moment made asyncio's `_call_connection_lost` raise WinError 10054 before it detached from the server, so `Server.wait_closed()` (awaited by uvicorn before our shutdown, and by a RAFT node's `stop()`) never returned — Ctrl+C hung 60 s. Fixed by `backend/shared/utils/asyncio_fixes.py`, installed first thing by the gateway and each node: any new process that runs an asyncio server on Windows should call `fix_windows_connection_reset()` too.
- **Binance's spot testnet charges no trading fees** (fills come back with fee 0; Bybit's demo charges 0.10 %). Anything
  that judges profit (bots, the lab, the fund's P&L) must also count the real-market taker fee (`ARB_TAKER_FEES_BPS`),
  or testnet practice flatters itself.
- **Lot steps leave dust**: Binance's BTC step is 0.00001 (~$1) and the router rounds to the coarsest step; when a
  buy fee is taken in the coin, a $10 lot can't be sold whole. Bots carry the remainder into their next sale.
- **`scripts/check_exchange_keys.py` must load spot markets only** (each adapter's `ccxt_options`): with all market
  types it failed whenever Binance's *futures* testnet was down, though the spot testnet we trade on was fine.
- **`git archive` on Windows converts line endings like a checkout** (`core.autocrlf=true`): a shell script for the
  lab server would arrive with CRLF and fail on Linux (`$'
': command not found`). `.gitattributes` keeps `*.sh` and
  `deploy/lab-server/**` LF — keep new Linux-side files under those patterns.
- **The lab server runs a release, not the branch**: `deploy.py release` copies ONE commit there; nothing you
  commit reaches it otherwise. Its database is its own: the laptop's lab bots stay stopped while the server runs the lab.
- **Measuring layouts in an automated browser**: a hidden browser pane paints no frames, so `ResizeObserver` never fires and charts look stuck at their starting width. Force a frame (take a screenshot) after every resize before measuring.
- **ccxt's `fetch_currencies` holds the event loop ~250 ms** on Gate's and HTX's lists (measured 2026-10-30): every order and book update waits behind it. Big lists are read directly and parsed in a worker thread (`exchanges/transfers.py`, ~20 ms).
- **A healthy order book says nothing about withdrawals**: during the Binance incident its books, feed and health score stayed normal and it was the best sell on 17 of 20 coins. Don't judge an exchange by its prices alone.
- **Quiet markets look frozen**: an unchanged mid for several seconds at night is real (verified against Binance's public quote), not a stale feed.
- **The gateway's event loop is frozen ~2 s right after start** (ccxt loading the exchanges' markets). A network call with a sub-second timeout made at start-up fails although the other side answered — that was the "RAFT cluster unreachable at start-up" warning. Retry until a deadline instead of trusting one answer.
- **uvicorn runs the app's start-up BEFORE it takes the port**: a second gateway opens the same audit database and writes to it before it fails on 8000. `start_all.py` refuses a second start for this reason; don't start a second gateway by hand either.
- **Windows Ctrl+C / Ctrl+Break** (`start_all.py`): every child runs in its own process group, so only `start_all.py` receives Ctrl+C; it stops the gateway with Ctrl+Break (uvicorn treats it like Ctrl+C). A plain `time.sleep(60)` is woken early by Ctrl+C but **not** by Ctrl+Break (an asyncio loop is). `Popen.wait()` can't be interrupted by Ctrl+C on Windows — poll instead. Killing `npm` leaves `cmd` → `node` (Vite) running: kill the tree (`taskkill /T`).
- **A login with a real password takes ~1 s** (scrypt, slow on purpose; checks run one at a time). Not a bug. Tests must never depend on
  the passwords in your own `.env`: use `USERS[...]` and patch the settings (see `test_api/test_logins.py`).

## Docs you can trust

| Current (trust these) | Stale (old simulated / stock design — read critically, being rewritten) |
|---|---|
| this file, `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md`, `CONTRIBUTING.md`, `project-info/ROADMAP.md`, `project-info/ALGORITHMS_GUIDE.md` (every routing + execution algorithm: idea, maths, live results, panel Q&A), `project-info/RESEARCH_GUIDE.md` (forecasts, indicators, backtests, technique study, deep history, trade plans: how, how well, panel Q&A), `project-info/COPILOT_GUIDE.md` (the copilot and its analyst: what it does, how it decides, Gemini / Claude keys, sandbox Auto, safety, panel Q&A), `project-info/AUTOMATION_GUIDE.md` (automation fund, profit-cycle bot, advisor agent, practice lab: decisions, verify-yourself, panel Q&A), `project-info/API_KEYS_GUIDE.md`, `project-info/PHASE6_GUIDE.md` (risk batch + RAFT: explained, self-check, panel Q&A), `project-info/INCIDENT_GUARD_GUIDE.md` (an exchange in trouble: signals, levels, the fire drill, honest limits, panel Q&A), `project-info/EXCHANGE_API_KEY_SECURITY.md` (how exchange API keys are protected), `project-info/SECURITY_GUIDE.md` (logins, signing key, CORS: options, decisions, demo-laptop checklist, panel Q&A), `project-info/START_STOP_GUIDE.md` (`start_all.py`: RAFT leader wait, second start refused, ordered Ctrl+C), `project-info/LAB_SERVER_GUIDE.md` (the 24/7 lab server on AWS: costs, console steps, deploy, what happens when, security), `project-info/REAL_MONEY_GUIDE.md` (Phase D: the safety rails, trade-only keys, the switch, the first real orders, panel Q&A), `project-info/PHASE_D_PLAN.md` (Phase D plan: separate real-money server with a fixed IP, the walls between sandbox and real money, stages D0–D7 with "done when", what Sarmad provides, decisions log), the code | `project-info/ARCHITECTURE.md`, `API_SPECIFICATION.md`, `ALGORITHMS.md`, `BACKEND_GUIDE.md`, `FRONTEND_GUIDE.md`, `DATABASE_SCHEMA.md`, `DEPLOYMENT.md`, `MILESTONES.md`, `PROJECT_STRUCTURE.md`, `DEVELOPMENT_SETUP.md` |

The live API contract is the gateway's Swagger page: http://127.0.0.1:8000/docs.
