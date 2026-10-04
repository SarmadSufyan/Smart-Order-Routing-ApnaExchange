# Exchange API keys — how ApnaSOR keeps them safe

> How we create, store, load and use the API keys for Binance, Bybit, OKX and Gate, and what protects them at every step.
> Every claim links to the code or document that proves it. How to *create* the keys: [`API_KEYS_GUIDE.md`](API_KEYS_GUIDE.md).
> Written 2026-10-03. Current state: **testnet / demo keys only** (exchange-run fake funds); real-money keys come only in Phase 8.

---

## 1. What an exchange API key is (plain words)

An exchange gives you two codes:

- **API key** — like a username. It says *who* is calling.
- **API secret** — like a password. It proves the call really comes from you.
  OKX adds a third one, the **passphrase**, that you choose yourself.

Whoever has the key + secret can do whatever the key is **allowed** to do on that account. So the protection has three layers: (1) limit what a key *can* do, (2) never let the secret leak, (3) make every request impossible to fake or replay.

---

## 2. Which keys we use, and where

| Exchange | Environment we trade on | Key used for | `.env` variables |
|---|---|---|---|
| Binance (V1) | Spot **testnet** | placing / checking orders, balances | `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` |
| Bybit (V4) | **Demo Trading** | same | `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` |
| OKX (V5) | **Demo Trading** | same | `OKX_TESTNET_API_KEY`, `OKX_TESTNET_API_SECRET`, `OKX_TESTNET_API_PASSPHRASE` |
| Gate (V6) | Spot **testnet** | same | `GATE_TESTNET_API_KEY`, `GATE_TESTNET_API_SECRET` |
| Coinbase (V2) | — (data only) | **no key at all** | — |

**Market prices need no keys.** The live order books come from each exchange's *public* feed. Keys are used only for the private actions: placing orders, checking orders, reading balances.

---

## 3. The protections, step by step

### 3.1 Limit what a key can do (at the exchange)

- Every key is created with **Read + Spot Trade only — never Withdraw**. Even if a key leaked, nobody could move money out with it. (Demo environments don't support withdrawal at all.) → [`API_KEYS_GUIDE.md`](API_KEYS_GUIDE.md) §3 (per exchange) and §7 (security checklist).
- **Testnet / demo keys only.** They hold the exchange's fake money, so a leak can't cost real money.
- **Each person makes their own keys** — never shared on WhatsApp, chat or screenshots, and never one key for the whole team. → [`../CONTRIBUTING.md`](../CONTRIBUTING.md)

### 3.2 Keep the secret out of the code and out of GitHub

- Keys live **only** in each person's local `.env` file, which git ignores (`.gitignore` has `.env`), so they can never be committed. → [`../.gitignore`](../.gitignore) (line 29); the template [`../.env.example`](../.env.example) has the variable names with **empty** values.
- Before every push we **scan everything being pushed** for the values in `.env` (the push of 2026-10-03 checked 11 secret values against 45 files: no hits).

### 3.3 Keep the secret out of logs, screens and error messages

- In the program, every key is a **`SecretStr`**: if anything prints it — a log line, an error, a debug view — it shows `**********`, never the value. → [`config.py:37`](../backend/gateway/config.py:37) to line 47
  *Why:* on 2026-09-27 part of a Binance testnet key appeared in a console printout. The root cause was fixed by making every key a `SecretStr`, and the key was marked for rotation.
- The real value is unwrapped in exactly **one place**, when the trading client is built. → [`config.py:182`](../backend/gateway/config.py:182) (`exchange_credentials`)
- Exchange error messages are **trimmed to the exchange's own short message** before they are logged or shown, so a long error dump can't carry a request with a key in it. → [`ccxt_venue.py:68`](../backend/exchanges/ccxt_venue.py:68) (`short_error`)

### 3.4 Only the right keys are ever loaded

- The program reads **only the keys of the mode it runs in**. In testnet mode it never even looks at mainnet keys, so in development there is no code path that can touch a real-money key. → [`config.py:182`](../backend/gateway/config.py:182)
- **Mainnet safety lock:** switching to real money needs **two** settings, `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`; with only one, the program refuses to start. A single typo can't fire real orders. → [`config.py:139`](../backend/gateway/config.py:139)

### 3.5 Two separate connections per exchange: public (no key) and private (key)

- Each exchange has a **data client that is never given the keys** (public prices) and an **execution client that holds them** (orders, balances). A bug in the price-reading code can't use a key it doesn't have. → [`ccxt_venue.py:9`](../backend/exchanges/ccxt_venue.py:9) (explanation), [`ccxt_venue.py:164`](../backend/exchanges/ccxt_venue.py:164) (`_make_client(..., with_keys=False)` for data)

### 3.6 Every request is encrypted and signed — the secret never travels

- **Encrypted connection:** every exchange connection is HTTPS or WSS (TLS — the same padlock as online banking), e.g. `wss://stream.binance.com:9443/stream`. → [`binance/adapter.py:23`](../backend/exchanges/binance/adapter.py:23), [`bybit/adapter.py:27`](../backend/exchanges/bybit/adapter.py:27), [`okx/adapter.py:27`](../backend/exchanges/okx/adapter.py:27), [`gate/adapter.py:28`](../backend/exchanges/gate/adapter.py:28)
- **Signed requests:** the **secret itself is never sent**. For each private request the program computes a *signature* — a fingerprint of the request made with the secret (HMAC) — and sends the key + the signature. The exchange recomputes it with its copy of the secret; if one character of the request was changed, the signature doesn't match and the request is refused.
- **Time stamp in every request:** each signed request carries the current time; the exchange refuses requests that are too old, so a recorded request can't be sent again later (replay).
- The signing is done by **ccxt**, a widely used open-source exchange library, pinned to a tested version (`ccxt==4.5.81` in [`requirements.txt`](../backend/requirements.txt)). Each exchange's scheme:

| Exchange | How a private request is signed (implemented by ccxt) |
|---|---|
| Binance | HMAC-SHA256 of the request with a `timestamp` (and `recvWindow`); key in the `X-MBX-APIKEY` header |
| Bybit | HMAC-SHA256; headers `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-RECV-WINDOW`, `X-BAPI-SIGN` |
| OKX | HMAC-SHA256 (Base64) of time + method + path + body; headers `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP`, `OK-ACCESS-PASSPHRASE` (the passphrase you chose travels inside the encrypted connection); demo requests carry `x-simulated-trading: 1` |
| Gate | HMAC-SHA512 of method + path + query + a SHA-512 hash of the body + time; headers `KEY`, `Timestamp`, `SIGN` |

### 3.7 A wrong, expired or limited key is noticed at once

- Each key is tested with a harmless private call at start-up and again on every balance refresh (every 15 s). If the exchange rejects it, that exchange is marked **not tradable**, with the reason on the Venue Connectivity page — no order is sent with a broken key; a fixed key makes it tradable again without a restart. → [`ccxt_venue.py:152`](../backend/exchanges/ccxt_venue.py:152) (start-up), [`ccxt_venue.py:543`](../backend/exchanges/ccxt_venue.py:543) (every refresh)
- If an exchange refuses a key's **right to trade** (e.g. OKX error 50124), the order is re-routed to another exchange and that exchange is **paused for 10 minutes**, audited. → [`ccxt_venue.py:75`](../backend/exchanges/ccxt_venue.py:75), [`engine.py:431`](../backend/services/routing_engine/engine.py:431)

### 3.8 Using the key responsibly

- **Rate limits respected:** every exchange client runs with `enableRateLimit`, so we never flood an exchange — flooding can get a key or IP blocked. → [`ccxt_venue.py:165`](../backend/exchanges/ccxt_venue.py:165)
- **No duplicate orders after a network error:** every order carries our own unique id (`clientOrderId`). If the network drops mid-request, we *ask the exchange* whether that id exists instead of sending it again. → [`ccxt_venue.py:396`](../backend/exchanges/ccxt_venue.py:396)
- **Everything is audited:** every order, fill, refusal and paused exchange is recorded with who and why (Audit Log page).

---

## 4. If a key leaks — what to do

1. **Delete the key on the exchange's website immediately** (API Management page). A deleted key stops working at once.
2. Create a new key (Read + Trade, no Withdraw) and put it in your `.env`.
3. Restart the program; the Venue Connectivity page should show the exchange as tradable again.
4. Check the exchange's order history for anything you didn't place, and tell the team.

Because the keys are testnet/demo and have no withdrawal right, the worst case today is fake orders with fake money.

---

## 5. Honest limits, and what changes for real money (Phase 8)

| Today (testnet / demo) | Before real money |
|---|---|
| Keys are stored as plain text in `.env` on each laptop | Keep `.env` only on the demo laptop, lock the laptop; ideally a secrets manager / OS key store |
| No IP whitelist on testnet keys (easier development) | **Bind every real key to the demo laptop's IP address** wherever the exchange allows it |
| Key rotation is manual (open item: rotate the testnet keys after testing) | Fresh keys for the demo, deleted right after |
| — | A **separate sub-account** with only $10-20 on each exchange, so the worst case is small |
| — | Read + Spot Trade only, **never Withdraw** — checked on every key |

Phase 8 checklist: [`API_KEYS_GUIDE.md`](API_KEYS_GUIDE.md) §6 (later-phase keys) and §7 (security checklist).

---

## 6. One-minute summary for the panel

*"Our exchange keys can only read and trade — never withdraw — and today they hold only the exchanges' test money. They live in a local file that is never uploaded, and the program masks them everywhere. The price feeds use no keys at all. For every order the secret never leaves the laptop: we send a signature made from it, time-stamped so it can't be replayed, over an encrypted connection. A wrong or limited key is detected and that exchange is paused. Real money needs two separate switches, and for the final demo we add IP whitelisting and a small separate sub-account."*
