# Progress

> Live snapshot of the project. **Read this first** when starting any session — then [`project-info/ROADMAP.md`](project-info/ROADMAP.md) for what "done" means.
> Rules and architecture: [`CLAUDE.md`](CLAUDE.md). Team workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md). Why things are built the way they are: [`DEVELOPMENT_JOURNAL.md`](DEVELOPMENT_JOURNAL.md).
> Updated at the end of every working session (and before a long session runs out of context).

---

## Last updated

**2026-10-02 (afternoon)** — **Router hardened** (Fizza): a venue that refuses an order no longer sinks it — the refused quantity is re-routed to the next-best venues (≤ 2 extra rounds), and a venue that refuses our API key's right to trade is paused for 10 min. Verified live: OKX refused (code 50124), the same order filled on Bybit, the next order skipped OKX. **Fizza keeps the backend relay** (continuing with the OKX key, then Phase 6).

2026-10-02 — **5b LLM copilot API done** (Fizza, `feature/backend-v2`): plain English → proposal → confirm → the normal order path, on **Google Gemini's free tier** (not Anthropic: cost). Verified live on Binance testnet + Bybit demo. Waiting for Sarmad's merge into `feature/crypto-pivot` so the frontend can build the chat panel. Next backend: Phase 6 (parallelism + RAFT), then 5c.

2026-10-01 — Phases 0–4 and the TWAP bot (5a) built and verified live. Repo **private**; `feature/crypto-pivot` is the default branch. Team split: backend = Fizza ⇄ Sarmad relay on `feature/backend-v2`; frontend = Mahnoor + Maham on `feature/frontend-v2`. New teammates: [`ONBOARDING.md`](ONBOARDING.md).

---

## Who is working on what

Team split (decided 2026-10-01). Update **your own** row's status at the end of every session.

| Area | People | Branch | Scope | Status | Last update |
|---|---|---|---|---|---|
| **Backend** (relay) | **Fizza ⇄ Sarmad** — one at a time, never together | `feature/backend-v2` | In order: 5b LLM copilot API → 6 multiprocessing risk + RAFT → 5c arbitrage detector → 7 CI + doc rewrite (ROADMAP) 5b API ✅ + router hardening ✅ (2026-10-02, ready to merge); **Fizza continues** → §6 | 2026-10-02 |
| **Frontend** — pages | **Mahnoor** | `feature/frontend-v2` | How It Works (rewrite for the crypto design), Dashboard (+ running-TWAP widget), Market Data, Login; the **copilot chat panel** once the copilot API is merged | not started | — |
| **Frontend** — pages | **Maham** | `feature/frontend-v2` | Wallet, Algo Orders, Venue Connectivity, Risk Manager, Kill Switch, Audit Log; narrow-screen / responsive fixes | not started | — |
| Integration | Sarmad | `feature/crypto-pivot` (default) | Merge `backend-v2` and `frontend-v2` at milestones; reviews; exchange keys | ongoing | 2026-10-01 |

Mahnoor and Maham may swap pages; just update the table. **Shared frontend files** (`services/api.ts`, `stores/dataStore.ts`, `App.tsx`, `components/AppShell.tsx`, `components/shared.tsx`, `theme.ts`, `format.ts`): `git pull` right before editing, push right after, and tell the other.

### Backend relay — handoff notes (newest on top)

Fizza and Sarmad never work at the same time. **Before starting:** message the other ("taking backend"), then `git pull` on `feature/backend-v2` and read the newest note here. **Before stopping:** tests pass, push everything (never leave uncommitted work), and add a note here: what you finished, what is half-done (file + function), the exact next step, anything that surprised you.

| Date | By | Done | Half-done / next step |
|---|---|---|---|
| 2026-10-02 (pm) | Fizza | **Router hardening complete**, nothing half-done. `ExecutionResult.retry_elsewhere` / `blocks_trading` (`exchanges/base.py`), set in `CcxtVenueAdapter.place_order` (`is_permission_error` also catches OKX 50124, which ccxt doesn't map); `RoutingEngine._execute_round` / `_reroute` (≤ `MAX_REROUTES` = 2) / `_block_trading` (`TRADING_BLOCK_COOLDOWN_S`, default 600). 13 new tests (160 total). Live with OKX enabled: OKX refused → same order FILLED on Bybit; OKX shown as "trading paused"; next order skipped it with the reason in the proof. Copilot `reference_mid` rounded | **Fizza continues the relay** (Sarmad: please don't start backend work until Fizza hands over; merging `feature/backend-v2` is welcome). Next: (1) Fizza recreates the OKX demo key (Read + Trade) — the manual Spot trade did NOT clear 50124; then accept OKX's exchange balances in the Wallet page (the manual trade shows as a reconciliation BREAK, as it should); (2) ROADMAP §6 |
| 2026-10-02 | Fizza | **5b copilot API complete**, nothing half-done. `backend/services/agents/` (`llm.py`: `LLMClient` interface + `GeminiClient`; `copilot.py`: `Copilot.propose/confirm/cancel`), router `gateway/routers/copilot.py`, models `shared/models/copilot.py`, settings `LLM_PROVIDER` / `GEMINI_API_KEY` / `GEMINI_MODEL` / `COPILOT_*`. 40 new tests (147 total). Live: market order filled, TWAP completed (Bybit + Binance), prompt-injection "sell 1000 BTC" proposal rejected by pre-trade risk on confirm. `httpx` moved to runtime requirements → run `pip install -r backend/requirements.txt` | **Sarmad:** add your own free `GEMINI_API_KEY` to `.env` (aistudio.google.com/apikey, no billing), review + merge `feature/backend-v2` → `feature/crypto-pivot` so Mahnoor can build the chat panel (API contract: ROADMAP §5b). **Next backend step:** ROADMAP §6 — multiprocessing risk batch first (process pool over pre-trade checks for a TWAP's/basket's slices + benchmark), then the RAFT cluster in the empty `backend/services/consensus/`. Surprise: Gemini *did* obey an injected "sell 1000 BTC" — the safety is confirm + risk, never the model |
| 2026-10-01 | Sarmad | Phases 0–4 + TWAP (5a) on `feature/crypto-pivot`; branches `feature/backend-v2` and `feature/frontend-v2` created from it | Next: start 5b LLM copilot API (ROADMAP §5b) — `backend/services/agents/copilot.py` + `backend/gateway/routers/copilot.py`; needs `ANTHROPIC_API_KEY` in `.env` |

---

## Picking this up cold?

1. New machine? Follow [`ONBOARDING.md`](ONBOARDING.md) (clone straight onto your branch, deps, your own `.env`, checks). Otherwise `git checkout feature/backend-v2` or `feature/frontend-v2` and `git pull`; backend relay: read the newest handoff note below first.
2. Set up your **own** exchange keys (`project-info/API_KEYS_GUIDE.md`) → `python scripts/check_exchange_keys.py` → `python start_all.py` → log in as `admin` / `admin`.
3. Run `python -m unittest discover -s backend/tests -t .` — expect **160 passing**.
4. Read the ROADMAP section for your workstream; it lists what "done" means.
5. The order path every feature must use: `OrderEntry.submit` (`backend/services/order_state/entry.py`). The TWAP bot (`backend/services/algos/twap.py`) and the LLM copilot (`backend/services/agents/copilot.py`) are the reference examples of features built on top of it.

---

## Current phase

**Crypto pivot — Phases 0–4, TWAP (5a) and the copilot API (5b) done. Next: copilot chat panel (frontend), 6 parallelism + RAFT, 5c arbitrage detector.**

### Venue lineup

| ID | Venue | Order book | Execution | Status |
|---|---|---|---|---|
| V1 | Binance | real market | spot testnet | ✅ verified live (all 5 pairs, every execution path, TWAPs) |
| V4 | Bybit | real market | **Demo Trading** | ✅ verified live (all 5 pairs, multi-venue splits with Binance, TWAPs) |
| V6 | Gate | real market | spot testnet | ⚠️ key authenticates (2026-10-01), feed + coherence OK, routable — but the **spot testnet wallet is empty**, so the router skips Gate ("no USDT balance on this venue"). Gate documents test funds only for its *futures* testnet (not transferable to spot). Execution unverified until spot funds exist |
| V5 | OKX | real market | Demo Trading | ⚠️ (Fizza, 2026-10-02) authenticates, feed + coherence OK, but orders get **50124 "This API Key does not have trading permission for the market"** although the key reports `read_only,trade` and a manual demo Spot trade was made — the router now pauses OKX and re-routes. Next: recreate the demo key. Some ISPs' DNS blocks okx.com — **fix: Windows DNS 8.8.8.8 + `ipconfig /flushdns`** |
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
- **LLM copilot (API)** — `POST /api/copilot/propose` turns one sentence ("buy $50 of BTC over 2 minutes", even Roman Urdu) into a proposal via Gemini's free tier; code re-validates everything, converts $ → coins at our own mid (maths shown), picks TWAP slices that clear venue minimums, warns about limits. Only `POST /api/copilot/confirm` by the same user, within 2 min, once, with the price within 1% submits it — through `OrderEntry` / the TWAP engine, so risk still applies. Ambiguous → a question; unsupported → a refusal; all audited (`copilot_*`).
- **Venue refusals don't sink orders** — a child a venue refuses (bad key, no permission, balance, exchange error, or a lost request confirmed not executed) is re-planned on fresh books without that venue, up to 2 extra rounds, price-protected, stopped by the kill switch or a cancel; an IOC that expired at its limit is not chased. A venue that refuses our key's right to trade is paused for 10 min (reason shown in exclusions and the routing proof, audited `venue_trading_blocked`), then gets one retry.
- **Survives restarts** — orders, execution reports, fills, baselines, limits, kill switch, algo orders.
- **Frontend pages** — Dashboard, Market Data, Algo Orders (TWAP), Wallet (P&L, reconciliation, concentration), How It Works, Venue Connectivity, Risk Manager, Kill Switch & Alerts, Audit Log.
- **Tests** — 160 backend unit tests; frontend strict type-check passes.

---

## What's next

See [`project-info/ROADMAP.md`](project-info/ROADMAP.md) for specs and "done" criteria.

1. **Backend relay (Fizza ⇄ Sarmad):** ~~5b LLM copilot API~~ ✅ → ~~router hardening~~ ✅ → 6 parallelism + RAFT → 5c arbitrage detector → 7 CI + doc rewrite.
2. **Frontend (Mahnoor + Maham):** How It Works rewrite, page polish, running-TWAP widget, responsive fixes; the **copilot chat panel** as soon as Sarmad merges `feature/backend-v2` (contract in ROADMAP §5b).
3. Merge `backend-v2` / `frontend-v2` into `feature/crypto-pivot` at milestones; much later `feature/crypto-pivot` → `main`.
5. Gate spot funds; OKX passphrase + a network that reaches okx.com.
6. Production DB decision (SQLite now; PostgreSQL/TimescaleDB later).
7. Remove the unused `venues/` folder from the active branch (kept on `archive/venue-simulators`) — waiting for the team's go-ahead.

---

## Open questions / blockers

- **Gate spot testnet funds** — no documented way found yet (see API_KEYS_GUIDE).
- **OKX** — DNS is solved by switching Windows DNS to 8.8.8.8 (+ `ipconfig /flushdns` if it still times out). Each person needs an OKX Demo key **with passphrase**. Open (2026-10-02, Fizza): error **50124** on every order despite Read + Trade — recreating the key is the next try; the router pauses OKX meanwhile.
- **More venues (2026-10-02, Fizza):** Bitget Demo Trading is reachable from Pakistan and tracks the real market for **BTC/ETH only** (SOL drifted, no BNB/XRP) — an adapter is ~1 day, after Phase 6 if time allows. KuCoin/MEXC/HTX have no usable test environment. Details: API_KEYS_GUIDE §2.
- **Rotate exchange keys** after testing (Sarmad). One Binance testnet key had partial values printed to a console on 2026-09-27 (root cause fixed: keys are `SecretStr`).
- **Security hardening before the final demo** (found 2026-10-02, needs a team decision; not on the ROADMAP yet): POC logins are hardcoded with password = username (`gateway/middleware/auth.py`); `JWT_SECRET` falls back to a default written in `config.py` (set your own in `.env`); CORS allows every origin (`main.py`); `npm install` reports 8 known vulnerabilities in frontend dependencies (Mahnoor/Maham: `npm audit`).
- **Gemini free tier** — Google may use prompts to improve its products; the copilot sends only the trader's sentence and the coin list. Limits ~10–15 requests/min: the copilot answers "try again in a minute" when hit.
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
