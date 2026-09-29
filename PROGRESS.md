# Progress

> Live snapshot of the project's state. Updated at the end of every working session, and automatically when the AI assistant's context window approaches 90% full.
>
> **Read this first** when starting a new session — it tells you exactly where to pick up.

---

## Last updated

**2026-09-29** — Live-tested with real keys: Binance and Bybit (demo) fully verified incl. real multi-venue splits; five bugs found and fixed; balance-aware routing + funding gate added. Gate (bad secret) and OKX (missing secret/passphrase, DNS-blocked network) still to verify.

---

## Current phase

**ApnaSOR crypto pivot — Phase 3 built; waiting on Bybit/OKX/Gate testnet keys to verify multi-venue execution. Phase 4 (balance-aware risk, audit log) next.**

### Venue lineup (checked against each real testnet on 2026-09-28)

| ID | Venue | Order book | Execution | Status |
|---|---|---|---|---|
| V1 | Binance | real market | testnet | ✅ **verified live 2026-09-29** — all 5 pairs, every execution path |
| V4 | Bybit | real market | **demo trading** | ✅ **verified live 2026-09-29** — all 5 pairs, every execution path, real multi-venue splits with Binance |
| V5 | OKX | real market | demo trading | ❌ `.env` has only the key — add `OKX_TESTNET_API_SECRET` + `OKX_TESTNET_API_PASSPHRASE`; **and** this network's DNS blocks okx.com (see API_KEYS_GUIDE troubleshooting) |
| V6 | Gate | real market | testnet | ❌ key rejected ("Signature mismatch") — secret looks half-copied (32 chars, Gate secrets are normally 64); create a new key |

**➡ Which API keys to create, and exactly how: [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md)**
| V2 | Coinbase | real market | none (no real testnet) | optional data-only, off by default |
| V3 | Kraken | — | — | not possible: no spot testnet |

After the June POC review the panel rejected simulated venues. The team pivoted to a Smart Order Router over **real crypto exchanges** (Binance, Coinbase, Kraken, Bybit, OKX). Decision record: `notes/decisions/ADR-001-Crypto-Pivot.md`. The simulated system is preserved on the `archive/venue-simulators` branch.

> ⚠️ `CLAUDE.md` and `project-info/*` still describe the old simulated/stock-market design. They are being reconciled; until then, trust this file and the code.

---

## What works now (verified 2026-09-28)

- **Two real venues, compared on real-market data** — order books come from **mainnet public feeds** (`MARKET_DATA_MODE=mainnet`, no keys needed) for Binance (V1) and Coinbase (V2); orders execute on the **Binance testnet**. Pairs: BTC/ETH/SOL/BNB/XRP vs USDT (BNB isn't listed on Coinbase).
- **Coinbase is data-only** — it has no real testnet (its sandbox returns mocked responses; the institutional sandbox has no USDT pairs). Its book counts in the NBBO and shows in every routing proof as *excluded* with the reason. Coinbase execution arrives with mainnet in Phase 8.
- **Book integrity** — Binance: REST snapshot + update-id chain (resync a symbol on a gap). Coinbase: in-stream snapshot + per-connection sequence numbers (reconnect on a gap). Both auto-reconnect with backoff.
- **Real execution with price protection** — every child is an IOC LIMIT: user's limit for LIMIT orders, or the worst planned price ±50 bps for MARKET orders (`CHILD_PRICE_PROTECTION_BPS`), so a moved book makes a child expire instead of overpaying. Lot-size rounding, min-notional checks, `clientOrderId` reconciliation on network errors. Routing proof shows expected VWAP vs actual fill (shortfall in bps; live ~1 bps).
- **Consolidated depth-walk routing** — best-price strategy walks the merged order book of all eligible venues; routing proof shows expected VWAP, levels walked, slippage (bps) and plain-English notes.
- **Unified wallet** — `GET /api/wallet/` returns real exchange balances for the trading universe, valued in USDT from our own books; refreshes every 15 s and right after each fill.
- **Venue health** — measured REST latency (p50/p95/p99), feed freshness, books synced, uptime; client-side rejects don't count against the venue; demo "Degrade" adds artificial latency to our connection.
- **Risk** — 7 pre-trade checks incl. a new per-order notional limit (default $10,000); kill switch blocks new orders first, then cancels OMS orders and sweeps exchange-side open orders.
- **Frontend** — crypto symbols, decimal quantities, adaptive price formatting, real Wallet page, TESTNET/MAINNET badge, working WebSocket subscriptions.
- **Execution coherence** — every 5 s, per venue and symbol, the testnet/demo book is compared with the real-market book; beyond ±50 bps (`COHERENCE_THRESHOLD_BPS`) that venue+symbol is excluded from routing in both directions, with the reason in the routing proof and per-symbol chips on the Venue page.
- **Balance-aware routing** — each venue's slice is capped by what it actually holds (coins to sell / USDT to buy, refreshed after every fill); a funding gate rejects orders the venues can't fund, with a per-venue breakdown.
- **Credential health** — keys are verified at start-up and every wallet refresh; a rejected key makes the venue non-tradable with the exchange's reason.
- **Tests** — 63 unit tests: `python -m unittest discover -s backend/tests -t .`

---

## How to run locally

Prerequisites: Python 3.11+ (verified on 3.14), Node 18+, and Binance **testnet** keys (optional — without keys you still get live market data; trading is disabled).

```powershell
# 1. One-time: install dependencies
pip install -r backend/requirements.txt
cd frontend; npm install; cd ..

# 2. One-time: configure keys (copy the template, then paste your TESTNET keys)
copy .env.example .env
#    get keys at https://testnet.binance.vision/  (log in with GitHub -> Generate HMAC_SHA256 Key)

# 3. Optional: check every exchange's keys at once
python scripts/check_exchange_keys.py

# 4. Start gateway + frontend
python start_all.py
```

- Frontend: http://localhost:5173 (POC users: `HARDCODED_USERS` in `backend/gateway/middleware/auth.py`)
- API docs: http://127.0.0.1:8000/docs
- Order books take ~3 seconds to sync after start-up.

---

## What's next

1. **Fix the Gate secret and the OKX secret/passphrase** (and OKX's DNS block), run `python scripts/check_exchange_keys.py`, then verify OKX and Gate live the same way Binance and Bybit were. Their execution code is written but not yet proven against a real account.
2. **Rotate every exchange key** once testing is finished (Sarmad's plan) — the keys were used during live testing (never printed).
3. **Phase 4 (remaining)** — balance-aware routing and the funding gate are already done (built early, 2026-09-29); still to do: P&L from real fills, concentration alerts, TimescaleDB audit log.
4. Doc reconciliation: rewrite `CLAUDE.md` + `project-info/` for the crypto design; write `ARCHITECTURE_V2.md` and `ADR-003` (wallet + strategy/agents layer).
5. Remove the unused `venues/` folder from the active branch (it lives on `archive/venue-simulators`) — waiting for the team's go-ahead.
6. Later phases (see `PLAN.md` locally): TWAP bot, LLM copilot, multiprocessing risk + RAFT, C++ NBBO.

---

## Open questions / blockers

- **Rotate the Binance testnet key.** During setup on 2026-09-27 a config validation error printed partial (middle-truncated) key/secret values to the console. Testnet only, no real funds — but regenerate it at testnet.binance.vision and update `.env`. The root cause is fixed (keys are `SecretStr` now).
- Mainnet (Phase 8) needs `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; mainnet keys stay blank until then.

---

## Branch / commit state

All crypto-pivot work (Phases 0–3 + live-testing fixes) is committed and pushed on **`feature/crypto-pivot`** (2026-09-29), branched from `feature/integration`. It also contains the final simulated-POC polish that had been uncommitted (degrade/recover buttons, Market Data page, AWS labels). `feature/integration` on GitHub still shows the last simulated-venue state. Next: open a PR `feature/crypto-pivot` → `main` for review.

Not in git on purpose: `.env` (keys), `PLAN.md` and `notes/` (personal, gitignored), `notes.zip` and the old `sor-poc-frontend/` copy (untracked).

---

## How to update this file

- **End of every working session:** update "Last updated", refresh "What works" and "What's next."
- **When AI context hits ~90% used:** the AI assistant updates this automatically so the next session can pick up cold.
- **When a decision flips:** edit "Open questions" or "What works" with the new decision and a date.

Keep it factual. No marketing, no aspirational language. A teammate pulling this branch should know exactly what state the codebase is in after reading this file alone.
