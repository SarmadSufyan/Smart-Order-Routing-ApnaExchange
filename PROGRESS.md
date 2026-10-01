# Progress

> Live snapshot of the project. **Read this first** when starting any session — then [`project-info/ROADMAP.md`](project-info/ROADMAP.md) for what "done" means.
> Rules and architecture: [`CLAUDE.md`](CLAUDE.md). Team workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md). Why things are built the way they are: [`DEVELOPMENT_JOURNAL.md`](DEVELOPMENT_JOURNAL.md).
> Updated at the end of every working session (and before a long session runs out of context).

---

## Last updated

**2026-10-01** — Phases 0–4 and the TWAP bot (5a) are built and verified live on Binance testnet + Bybit demo. Repo made **private**; `feature/crypto-pivot` is the default branch. Team split: **backend** = Fizza ⇄ Sarmad relay on `feature/backend-v2`; **frontend** = Mahnoor + Maham on `feature/frontend-v2`. Everyone works locally with their own `.env`. New teammates: [`ONBOARDING.md`](ONBOARDING.md). Next: Phase 5b (LLM copilot), Phase 6 (parallelism + RAFT), 5c (arbitrage detector) — in parallel workstreams.

---

## Who is working on what

Team split (decided 2026-10-01). Update **your own** row's status at the end of every session.

| Area | People | Branch | Scope | Status | Last update |
|---|---|---|---|---|---|
| **Backend** (relay) | **Fizza ⇄ Sarmad** — one at a time, never together | `feature/backend-v2` | In order: 5b LLM copilot API → 6 multiprocessing risk + RAFT → 5c arbitrage detector → 7 CI + doc rewrite (ROADMAP) | not started | 2026-10-01 |
| **Frontend** — pages | **Mahnoor** | `feature/frontend-v2` | How It Works (rewrite for the crypto design), Dashboard (+ running-TWAP widget), Market Data, Login; the **copilot chat panel** once the copilot API is merged | not started | — |
| **Frontend** — pages | **Maham** | `feature/frontend-v2` | Wallet, Algo Orders, Venue Connectivity, Risk Manager, Kill Switch, Audit Log; narrow-screen / responsive fixes | not started | — |
| Integration | Sarmad | `feature/crypto-pivot` (default) | Merge `backend-v2` and `frontend-v2` at milestones; reviews; exchange keys | ongoing | 2026-10-01 |

Mahnoor and Maham may swap pages; just update the table. **Shared frontend files** (`services/api.ts`, `stores/dataStore.ts`, `App.tsx`, `components/AppShell.tsx`, `components/shared.tsx`, `theme.ts`, `format.ts`): `git pull` right before editing, push right after, and tell the other.

### Backend relay — handoff notes (newest on top)

Fizza and Sarmad never work at the same time. **Before starting:** message the other ("taking backend"), then `git pull` on `feature/backend-v2` and read the newest note here. **Before stopping:** tests pass, push everything (never leave uncommitted work), and add a note here: what you finished, what is half-done (file + function), the exact next step, anything that surprised you.

| Date | By | Done | Half-done / next step |
|---|---|---|---|
| 2026-10-01 | Sarmad | Phases 0–4 + TWAP (5a) on `feature/crypto-pivot`; branches `feature/backend-v2` and `feature/frontend-v2` created from it | Next: start 5b LLM copilot API (ROADMAP §5b) — `backend/services/agents/copilot.py` + `backend/gateway/routers/copilot.py`; needs `ANTHROPIC_API_KEY` in `.env` |

---

## Picking this up cold?

1. New machine? Follow [`ONBOARDING.md`](ONBOARDING.md) (clone straight onto your branch, deps, your own `.env`, checks). Otherwise `git checkout feature/backend-v2` or `feature/frontend-v2` and `git pull`; backend relay: read the newest handoff note below first.
2. Set up your **own** exchange keys (`project-info/API_KEYS_GUIDE.md`) → `python scripts/check_exchange_keys.py` → `python start_all.py` → log in as `admin` / `admin`.
3. Run `python -m unittest discover -s backend/tests -t .` — expect **107 passing**.
4. Read the ROADMAP section for your workstream; it lists what "done" means.
5. The order path every feature must use: `OrderEntry.submit` (`backend/services/order_state/entry.py`). The TWAP bot (`backend/services/algos/twap.py`) is the reference example of a strategy built on top of it.

---

## Current phase

**Crypto pivot — Phases 0–4 + TWAP (5a) done. Next: 5b LLM copilot, 6 parallelism + RAFT, 5c arbitrage detector.**

### Venue lineup

| ID | Venue | Order book | Execution | Status |
|---|---|---|---|---|
| V1 | Binance | real market | spot testnet | ✅ verified live (all 5 pairs, every execution path, TWAPs) |
| V4 | Bybit | real market | **Demo Trading** | ✅ verified live (all 5 pairs, multi-venue splits with Binance, TWAPs) |
| V6 | Gate | real market | spot testnet | ⚠️ key authenticates (2026-10-01), feed + coherence OK, routable — but the **spot testnet wallet is empty**, so the router skips Gate ("no USDT balance on this venue"). Gate documents test funds only for its *futures* testnet (not transferable to spot). Execution unverified until spot funds exist |
| V5 | OKX | real market | Demo Trading | ❌ passphrase missing (`OKX_TESTNET_API_PASSPHRASE`); this network's DNS blocks okx.com |
| V2 | Coinbase | real market | none (no real testnet) | data-only, off by default |
| V3 | Kraken | — | — | not possible: no spot testnet |

The simulated system (June POC) is preserved on the `archive/venue-simulators` branch.

---

## What works now

- **Real-market data, real orders on test environments** — order books from mainnet public feeds (`MARKET_DATA_MODE=mainnet`); orders execute on Binance testnet / Bybit demo (`EXCHANGE_MODE=testnet`). Pairs: BTC/ETH/SOL/BNB/XRP vs USDT.
- **Book integrity** — update-id chains (Binance, Gate), sequence chains (Bybit `u`, OKX `prevSeqId`), Coinbase per-connection sequence numbers; resync / reconnect with backoff.
- **Execution coherence** — every 5 s per venue+symbol the test book is compared with the real market; beyond ±50 bps that pair is excluded from routing both ways.
- **Consolidated depth-walk routing** — merged order book of all eligible venues, split at the best prices; respects each venue's balance (capacity), lot size and **minimum order** (sub-minimum shares are re-routed); routing proof with expected VWAP, slippage, exclusions in plain English.
- **Safe execution** — every child is an IOC LIMIT, MARKET parents price-protected (±50 bps, tighter if the venue's own price-limit rule requires); idempotent client ids; reconciliation by client id after network errors (seen live: a Bybit network error was confirmed "not executed"); fees in USDT, base coin or BNB handled.
- **Pre-trade risk** — 7 checks (kill switch, size, order notional, rate, position, exposure, symbol) + a funding gate.
- **Unified wallet** — real balances per exchange, valued at our own mid prices; refreshed every 15 s and after every fill.
- **P&L + reconciliation** — opening balances captured automatically per venue; average-cost ledger; total = realized + unrealized − fees, split into hold P&L and trading P&L; ledger reconciled against each exchange (OK / settling / BREAK; breaks can be accepted as a new baseline).
- **Risk monitor** — trading-loss limit (default $1,000) engages the kill switch automatically and blocks its release while breached; concentration alerts (asset > 60%, exchange > 80%). Limits editable by an admin, persisted.
- **Kill switch** — blocks new orders, stops unsent child orders, sweeps resting exchange orders, pauses TWAPs; persisted (stays engaged across restarts).
- **Audit log** — every order step, risk check, routing decision, fill, operator action and algo event with actor + summary, in `data/apnasor-<mode>.db` (SQLite, per machine). Audit Log page + per-order timeline.
- **TWAP bot** — Algo Orders page: slices through `OrderEntry`, catch-up schedule (≤ 2× a slice), optional jitter/limit, pause/resume/cancel between slices, execution chart, cost vs arrival and vs interval TWAP; restart leaves TWAPs paused; 3 pre-venue rejections pause it; max TWAP value limit ($50k).
- **Survives restarts** — orders, execution reports, fills, baselines, limits, kill switch, algo orders.
- **Frontend pages** — Dashboard, Market Data, Algo Orders (TWAP), Wallet (P&L, reconciliation, concentration), How It Works, Venue Connectivity, Risk Manager, Kill Switch & Alerts, Audit Log.
- **Tests** — 107 backend unit tests; frontend strict type-check passes.

---

## What's next

See [`project-info/ROADMAP.md`](project-info/ROADMAP.md) for specs and "done" criteria.

1. **Backend relay (Fizza ⇄ Sarmad):** 5b LLM copilot API (needs `ANTHROPIC_API_KEY`) → 6 parallelism + RAFT → 5c arbitrage detector → 7 CI + doc rewrite.
2. **Frontend (Mahnoor + Maham):** How It Works rewrite, page polish, running-TWAP widget, responsive fixes; then the copilot chat panel once the copilot API is merged into `feature/crypto-pivot`.
3. Merge `backend-v2` / `frontend-v2` into `feature/crypto-pivot` at milestones; much later `feature/crypto-pivot` → `main`.
5. Gate spot funds; OKX passphrase + a network that reaches okx.com.
6. Production DB decision (SQLite now; PostgreSQL/TimescaleDB later).
7. Remove the unused `venues/` folder from the active branch (kept on `archive/venue-simulators`) — waiting for the team's go-ahead.

---

## Open questions / blockers

- **Gate spot testnet funds** — no documented way found yet (see API_KEYS_GUIDE).
- **OKX** — passphrase + DNS.
- **Rotate exchange keys** after testing (Sarmad). One Binance testnet key had partial values printed to a console on 2026-09-27 (root cause fixed: keys are `SecretStr`).
- **Mainnet (Phase 8)** needs `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; mainnet keys stay blank until then.

---

## Branch / commit state

All work through Phase 5a is on **`feature/crypto-pivot`**, the repo's **default branch** (2026-10-01). Working branches cut from it: `feature/backend-v2` (Fizza ⇄ Sarmad relay) and `feature/frontend-v2` (Mahnoor + Maham); both merge back into `feature/crypto-pivot` at milestones. The repo is **private** (Sarmad admin; Fizza, Mahnoor, Maham write). `main` still has the June simulated POC; `feature/crypto-pivot` → `main` happens later via PR.

Not in git on purpose: `.env` (keys), `data/` (local audit database), `PLAN.md` and `notes/` (Sarmad's personal notes), `notes.zip`, the old `sor-poc-frontend/` copy, log files.

---

## How to update this file

- **End of every session:** "Last updated", your row in *Who is working on what*, "What works", "What's next".
- **Before a long session runs out of context:** update it so the next session can pick up cold.
- **When a decision flips:** edit "Open questions" / "What works" with the new decision and a date.

Keep it factual. A teammate pulling the branch should know exactly where things stand from this file alone.
