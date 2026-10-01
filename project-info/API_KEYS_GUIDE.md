# API Keys Guide — which keys ApnaSOR needs, from which venue

> **Audience:** anyone on the team setting up ApnaSOR locally.
> **Last verified:** 2026-09-28 (steps checked against each exchange's official docs; environments measured live).
> **Golden rule:** keys go **only** in your local `.env` file (gitignored). Never commit them, never paste them in chat, screenshots or WhatsApp.

---

## 1. Summary — what you need

| ID | Venue | Environment we trade on | Key type to create | Account needed | `.env` variables | Needed now? |
|---|---|---|---|---|---|---|
| V1 | **Binance** | Spot **Testnet** | Testnet HMAC key | GitHub login only | `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` | ✅ Yes |
| V4 | **Bybit** | **Demo Trading** (⚠️ *not* Testnet) | Demo Trading API key | Normal Bybit account | `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` | ✅ Yes |
| V5 | **OKX** | **Demo Trading** | Demo Trading API key **+ passphrase** | Normal OKX account | `OKX_TESTNET_API_KEY`, `OKX_TESTNET_API_SECRET`, `OKX_TESTNET_API_PASSPHRASE` | ✅ Yes |
| V6 | **Gate** | Spot **TestNet** | API v4 key with account type **Testnet** | Normal Gate account | `GATE_TESTNET_API_KEY`, `GATE_TESTNET_API_SECRET` | ✅ Yes |
| V2 | Coinbase | none — data only | **No key needed** | — | — | ❌ No |
| V3 | Kraken | not usable | **No key** (no spot testnet exists) | — | — | ❌ No |
| — | Anthropic (LLM copilot) | — | Anthropic API key | Anthropic Console account | `ANTHROPIC_API_KEY` | ⏳ Phase 5 |

**Market data never needs a key.** Every order book ApnaSOR shows comes from each exchange's *public* real-market feed. Keys are only for **placing orders and reading balances**. Without a venue's keys, ApnaSOR still shows its prices, but won't route orders to it (the UI says "no … API keys configured").

**Why the variables say `TESTNET` even for "demo" venues:** in our config, `*_TESTNET_*` means "non-real-money keys". For Bybit and OKX, the non-real-money environment is called *Demo Trading*, and those demo keys go in the `*_TESTNET_*` variables.

---

## 2. Why these environments (and not others)

We decide where to route on **real-market** order books and execute in **test environments**. That only makes sense if the test environment's prices track the real market. We measured every candidate on 2026-09-28:

| Environment | vs. real market | Verdict |
|---|---|---|
| OKX Demo Trading | ~0–3 bps, all 5 pairs | ✅ use |
| **Bybit Demo Trading** | **<1 bps, all 5 pairs** | ✅ use |
| Bybit **Testnet** | BTC ~+170 bps, XRP ~+410 bps, BNB had no sellers | ❌ **don't use** — a separate, drifted market |
| Gate TestNet | ~0–10 bps, all 5 pairs | ✅ use |
| Binance Spot Testnet | ~1–5 bps, all 5 pairs | ✅ use |
| Coinbase sandbox | static, pre-written responses — no matching engine | ❌ fake execution |
| Kraken | no spot testnet at all | ❌ |
| Phemex / HitBTC / Poloniex testnets | BTC at $49k / $21k / sandbox down | ❌ |

ApnaSOR re-checks this every 5 seconds (the *coherence monitor*, shown as green/red chips on the Venue Connectivity page). A venue/symbol whose test prices drift more than ±50 bps from the real market is automatically excluded from routing.

> **Be honest in the report:** Testnet and Demo Trading are exchange-run environments using fake funds. Our orders are real API orders handled by the exchange's own systems, at real-market prices, but with no real money until Phase 8.

---

## 3. Step-by-step, per venue

Exchange websites change their menus. If a step doesn't match what you see, look for the same words (*API*, *Demo Trading*, *Testnet*) nearby.

### V1 — Binance (Spot Testnet)

1. Go to **https://testnet.binance.vision/**.
2. Click **"Log In with GitHub"** (make a GitHub account first if you don't have one).
3. Click **"Generate HMAC_SHA256 Key"**, give it a label (e.g. `apnasor-dev`), confirm.
4. Copy the **API Key** and **Secret Key** right away, because **the secret is shown only once**.
5. Funds: the testnet account is funded automatically (e.g. 10,000 USDT, 1 BTC, plus many test tokens).
6. Add to `.env`:
   ```
   BINANCE_TESTNET_API_KEY=...
   BINANCE_TESTNET_API_SECRET=...
   ```
- ⚠️ Binance **resets the testnet periodically** (keys and balances). If Binance suddenly shows "AUTH FAILED", generate a new key.

### V4 — Bybit (Demo Trading, NOT Testnet)

> ⚠️ Bybit has **two** separate environments. Use **Demo Trading**. Keys from `testnet.bybit.com` will **not** work with ApnaSOR, because that Testnet is a separate market whose prices are far from the real one.

1. Log in to your normal Bybit account at **https://www.bybit.com** (create one if needed).
2. Switch to **Demo Trading**. It's an independent demo account with its own user ID, and it holds virtual funds only.
3. **While in Demo Trading mode**, hover over your **avatar → API** and create a new API key (system-generated).
4. Permissions: **Read-Write** with **Spot trading** enabled. **Do not** enable withdrawals (not available in demo anyway).
5. Copy the API key and secret.
6. Funds: the Demo Trading account uses virtual funds. If it's empty, top up virtual funds from the demo account (Bybit also offers a demo-funds request, limited to about once per minute).
7. Add to `.env`:
   ```
   BYBIT_TESTNET_API_KEY=...      # the DEMO TRADING key
   BYBIT_TESTNET_API_SECRET=...
   ```

### V5 — OKX (Demo Trading)

1. Log in to **https://www.okx.com** (create an account if needed).
2. Official path: **Trade → Demo Trading → Personal Center → Demo Trading API → Create Demo Trading API Key**.
3. Set a **passphrase** when creating the key. **Write it down**, because OKX needs it on every request.
4. Permissions: **Read** + **Trade**. (Withdraw isn't supported in demo; never enable it on real keys either.)
5. Copy the API key, secret and your passphrase.
6. Funds: the demo account comes with virtual funds.
7. Add to `.env`:
   ```
   OKX_TESTNET_API_KEY=...        # the DEMO TRADING key
   OKX_TESTNET_API_SECRET=...
   OKX_TESTNET_API_PASSPHRASE=... # the passphrase YOU chose
   ```
- Demo keys are separate from live keys and only work against demo. ApnaSOR adds OKX's required `x-simulated-trading: 1` header automatically.

### V6 — Gate (Spot TestNet)

1. Log in to your Gate account at **https://www.gate.com** (create one if needed).
2. Hover over the **profile icon → API Management → Create API Key**.
3. Choose **API v4 Key**, and for **Account type** choose **Testnet**.
4. IP binding: optional for testnet (leave unbound for development).
5. Permissions: **Read and Write**, with spot trading allowed.
6. Submit, then copy the API key and secret.
7. Funds: run the checker (section 5) to see your TestNet balance. If it's zero, log in at testnet.gate.com, open **Spot → Assets** and look for a test-funds option there. Careful: Gate's documented test-funds claim (1,000 USDT or 0.1 BTC, once per 72 h) goes to the **futures** testnet account and **cannot be moved to spot** — ApnaSOR trades Gate **spot**, so those funds don't help. (As of 2026-10-01 our Gate spot testnet wallet is empty; Gate stays connected for market data and is skipped for orders with "no USDT balance on this venue".)
8. Add to `.env`:
   ```
   GATE_TESTNET_API_KEY=...
   GATE_TESTNET_API_SECRET=...
   ```
- Gate's TestNet spot endpoints and order books are live and track the real market. **Your first order is the final confirmation** that spot trading works on your TestNet account. Tell the backend owner if it's rejected.

---

## 4. Venues that need NO keys (and why they're listed)

### V2 — Coinbase (optional, data-only)

- **No key needed.** Coinbase has no real test environment: its Advanced Trade sandbox returns static, pre-written responses, and its institutional sandbox lists no USDT pairs. Sending orders there would be fake execution.
- It's **off by default**. To show its real order book as a reference venue (it then appears in the NBBO, marked *DATA ONLY*), add it:
  ```
  ENABLED_EXCHANGES=binance,bybit,okx,gate,coinbase
  ```
- **Phase 8 (real money), only if the team decides to add Coinbase execution:** that would need a Coinbase *Advanced Trade* API key created in the Coinbase Developer Platform, with trade permission and no withdrawal permission. This isn't built or tested yet; confirm the exact key format when it is.

### V3 — Kraken

- **No key.** Kraken offers no spot test environment (only a futures demo), so it isn't part of ApnaSOR. It stays in the catalog only as a documented "not possible".

### Public market data (all venues)

- **No keys.** Order books and tickers come from public WebSocket/REST feeds of Binance, Bybit, OKX, Gate (and Coinbase if enabled).

---

## 5. Check your keys (one command)

From the project root:

```bash
python scripts/check_exchange_keys.py
```

Expected output, one line per venue:

```
Binance (V1)     OK - authenticated on testnet. Balances: USDT 9999.97, BTC 1, ...
Bybit (V4)       OK - authenticated on demo. Balances: ...
OKX (V5)         OK - authenticated on demo. Balances: ...
Gate (V6)        NO KEYS in .env  -> add GATE_TESTNET_API_KEY / GATE_TESTNET_API_SECRET
```

The script reads keys through the gateway's own settings and **never prints them**. After changing `.env`, restart the gateway (`python start_all.py`).

### Common problems

| Symptom | Likely cause |
|---|---|
| Bybit `AUTH FAILED` | Key was created on **testnet.bybit.com** instead of in **Demo Trading** mode on bybit.com |
| OKX `NO KEYS in .env` although you added the key | OKX needs **three** values: key, secret **and** passphrase. Check all three lines exist |
| OKX `AUTH FAILED` | Wrong/missing **passphrase**, or a **live** key instead of a **Demo Trading** key |
| OKX shows "Feed Disconnected" / `getaddrinfo failed` | Your network's DNS is **blocking `okx.com`** (seen 2026-09-29 on a Pakistani ISP: default DNS *refused* it, while 1.1.1.1 and 8.8.8.8 resolved it). Fix on **your** PC: set Windows DNS to `1.1.1.1` / `8.8.8.8`, or use a VPN. For the panel demo, don't depend on OKX unless the demo network can reach it |
| Gate `AUTH FAILED` - "Signature mismatch" | The **secret** is wrong. First check `.env` for the **same variable name twice** — the *later* line wins. Seen 2026-09-29: the OKX secret (32 uppercase hex characters) had been pasted as a second `GATE_TESTNET_API_SECRET` under the OKX key, overriding the real Gate secret (64 characters). Otherwise the secret was copied incompletely; Gate shows it once, so create a new key |
| Gate `AUTH FAILED` - other | Key's account type is not **Testnet**, or an IP whitelist that doesn't match your IP |
| Binance `AUTH FAILED` after working before | Binance **reset the testnet**, so generate a new key |
| Any venue: timestamp / "recvWindow" errors | Your PC clock is off. Turn on *Set time automatically* in Windows settings |
| `NO KEYS in .env` | Variable name typo, or `.env` isn't in the project root `D:\Sor-Fyp` |

---

## 6. Later phases — keys you'll need then (not now)

| Phase | What | Keys | Notes |
|---|---|---|---|
| **Phase 5** | LLM natural-language copilot | `ANTHROPIC_API_KEY` from the **Anthropic Console → API Keys** | The copilot only turns English into order parameters. Every order still passes the same risk checks. |
| **Phase 8** | Live demo with small real money ($10–20) | `BINANCE_MAINNET_API_KEY` / `_SECRET` (and mainnet keys for any other venue used live) | See the safety rules below. The app refuses to start in mainnet mode unless **both** `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true` are set. |

**Mainnet key safety rules (Phase 8):**
1. Permissions: **Read + Spot Trade only. Never enable Withdraw.**
2. Bind the key to your **IP address** (IP whitelist) wherever the exchange allows it.
3. Fund only what you can afford to lose ($10–20 per venue); ApnaSOR's kill switch and $10 per-order cap are the backstop, not the plan.
4. Create mainnet keys **the day of the demo** and delete them right after.

---

## 7. Security checklist (everyone)

- [ ] Keys are only in `D:\Sor-Fyp\.env`, which is gitignored. Run `git status` and make sure `.env` never appears.
- [ ] Never share keys in chat, screenshots, email, WhatsApp, or AI assistants.
- [ ] If a key is ever exposed, **delete it on the exchange and create a new one** (even testnet/demo).
- [ ] No withdrawal permission on any key, ever.
- [ ] Testnet/demo keys and mainnet keys use **different variables**, and ApnaSOR only reads mainnet keys in mainnet mode.

---

## 8. Quick reference — all key variables

```
# ── Mode ──
EXCHANGE_MODE=testnet            # testnet = fake funds (testnet or demo per venue)
ALLOW_MAINNET=false              # must ALSO be true for real money (Phase 8)
ENABLED_EXCHANGES=binance,bybit,okx,gate

# ── V1 Binance (Spot Testnet) ──
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=

# ── V4 Bybit (DEMO TRADING key) ──
BYBIT_TESTNET_API_KEY=
BYBIT_TESTNET_API_SECRET=

# ── V5 OKX (DEMO TRADING key) ──
OKX_TESTNET_API_KEY=
OKX_TESTNET_API_SECRET=
OKX_TESTNET_API_PASSPHRASE=

# ── V6 Gate (TestNet key) ──
GATE_TESTNET_API_KEY=
GATE_TESTNET_API_SECRET=

# ── Later ──
ANTHROPIC_API_KEY=               # Phase 5
BINANCE_MAINNET_API_KEY=         # Phase 8 only
BINANCE_MAINNET_API_SECRET=      # Phase 8 only
```

---

### Official references

- Binance Spot Testnet: https://testnet.binance.vision/
- Bybit Demo Trading (API docs): https://bybit-exchange.github.io/docs/v5/demo
- Bybit Testnet (the one **not** to use): https://www.bybit.com/en/help-center/article/How-to-Request-Test-Coins-on-Testnet
- OKX API v5, Demo Trading section: https://www.okx.com/docs-v5/en/
- Gate API v4 (TestNet): https://www.gate.com/docs/developers/apiv4/en/
- Gate API key management: https://gate.io/help/guide/17521/how-to-utilize-api
- Coinbase Advanced Trade sandbox (why it's data-only): https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/sandbox
