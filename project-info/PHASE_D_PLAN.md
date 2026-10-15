# Phase D — real money: the plan

> **Status (2026-10-15): proposed, waiting for Sarmad's decisions (§5 A).** Once decided, each decision is written
> into §15 and this status line changes to *decided*. This file is the knowledge base for Phase D: what we build,
> in which order, what Sarmad does, and when each step is finished. Details that already exist live in
> [`REAL_MONEY_GUIDE.md`](REAL_MONEY_GUIDE.md) (the safety rails), [`LAB_SERVER_GUIDE.md`](LAB_SERVER_GUIDE.md)
> (the sandbox server), [`AUTOMATION_GUIDE.md`](AUTOMATION_GUIDE.md) (bots), [`COPILOT_GUIDE.md`](COPILOT_GUIDE.md).

---

## 0. In one paragraph

Real money gets **its own server** in the cloud, with **its own fixed internet address**. The real exchange keys
live **only there**, and each key is **locked to that address**: a key copied anywhere else is useless. The
sandbox stays where it is: the laptop for building, and the lab server for long experiments. **Code reaches the
real-money server only after it has run in the sandbox**, and the real-money server is **tested empty in sandbox
mode first**, before any real key goes on it. Then Sarmad places the first real orders with small amounts, and we
widen one step at a time: more exchanges, higher limits, and bots only if the lab proves them.

---

## 1. Where we stand

| Part | State | Notes |
|---|---|---|
| Safety rails for real money | ✅ built 2026-10-12 | trade-only key confirmed by the exchange, hard caps in `.env`, real passwords only, red confirmations, cycle bots off — `REAL_MONEY_GUIDE.md` §2 |
| Binance (spot) | ✅ proven in the sandbox | every execution path, TWAPs, splits |
| Bybit (spot) | ✅ proven in the sandbox | Demo Trading; splits with Binance |
| OKX | ⚠️ not proven | its demo refused our orders (50124 "no trading permission") — needs a new demo key |
| Gate | ⚠️ not proven | its spot test wallet can't be funded (test funds go to futures only) |
| Fees | ⚠️ public base rate (0.10 %) | the account's real rate is not read yet |
| Bots | ⏳ lab verdict pending | cycle bots: every sale profitable, but the held coins carry the price risk; the advisor lost as the research predicted (2026-10-15: −$18 on $1,700 overall). Lab runs until about 2026-10-22 |
| Copilot | ✅ works (Gemini) | asks before every action; Auto is sandbox-only |
| Real orders | ❌ none yet | Sarmad places the first ones (stage D4) |

---

## 2. The principle: one codebase, three separate places

The same code runs everywhere. What separates real money from the sandbox is **where it runs, which keys it holds,
and which database it writes**. This is how companies run production systems: develop → test → production, never
mixed.

```
   ┌──────────────────────┐   promote    ┌──────────────────────┐   promote    ┌──────────────────────────┐
   │ 1. DEVELOPMENT       │ ───────────► │ 2. LAB  (sandbox)    │ ───────────► │ 3. REAL MONEY            │
   │ Sarmad's laptop      │  a commit    │ AWS Zurich, 24/7     │  a commit    │ AWS, its own server      │
   │ sandbox keys         │  with CI ✅  │ sandbox keys only    │  that ran    │ real keys ONLY           │
   │ build + try things   │              │ bots, lab, long runs │  ≥ 3 days ✅ │ fixed IP, keys locked    │
   └──────────────────────┘              └──────────────────────┘              └──────────────────────────┘
        apnasor-testnet.db                     its own DB                         apnasor-mainnet.db
        badge: SANDBOX                         badge: LAB SERVER                  badge: REAL MONEY (red)
```

| | 1. Development | 2. Lab (sandbox) | 3. Real money |
|---|---|---|---|
| Where | the laptop | AWS EC2, Zurich (live since 2026-10-12) | **new** AWS EC2 server, its own fixed IP (Elastic IP) |
| Money | exchange sandbox funds | exchange sandbox funds | **real**, small |
| Exchange keys | sandbox keys (`*_TESTNET_*`) | sandbox keys | **real keys only** (`*_MAINNET_*`), **locked to the server's IP**, trade-only |
| Can it hold a real key? | no (stays empty, CLAUDE.md rule 2) | no (its installer refuses) | yes — and no sandbox key |
| Database | its own | its own | its own (`apnasor-mainnet.db`) |
| Who uses it | everyone (the relay) | Sarmad (read-only checks by the assistant) | **Sarmad only** |
| How you see it | `localhost:5173` | tunnel → `localhost:5180` (blue LAB SERVER) | tunnel → `localhost:5190` (red REAL MONEY) |
| What it is for | building features | experiments, bots, the lab's verdicts | real trading, the M3 evidence |

---

## 3. The walls between sandbox and real money

Each wall works on its own; together, a mistake has to get through all of them. ✅ = exists, 🔨 = to build in D1.

| # | Wall | How |
|---|---|---|
| 1 | Two switches | `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`, or the gateway won't start ✅ |
| 2 | Separate machines | sandbox on the laptop and the lab server; real money only on the real-money server 🔨 |
| 3 | Keys locked to one address | each real key accepts calls only from the real-money server's fixed IP; a copied key is useless elsewhere 🔨 (the server) + exchange setting (Sarmad) |
| 4 | The gateway checks the lock | on real money, a key that is **not** IP-locked, can withdraw, or can't be verified gets no orders 🔨 (IP-lock rule; the trade-only part ✅) |
| 5 | Keys never touch the laptop | real keys are typed once into a hidden prompt and go straight into the server's `.env` (owner-only file) — never stored on the laptop, never in chat, never in git 🔨 |
| 6 | Installers refuse the wrong mode | the lab installer refuses real money ✅; the real-money installer refuses sandbox keys once live 🔨 |
| 7 | Only tested code is promoted | a commit reaches the real-money server only after **≥ 3 days on the lab server** and **CI ✅** — or with a written reason that goes into the audit log 🔨 |
| 8 | Hard caps no screen can raise | `MAINNET_MAX_*` in the server's `.env`; a raise = edit the file on the server + restart ✅ |
| 9 | Separate records and logins | own database, own login-signing key, real passwords only (no demo login) ✅ |
| 10 | You always know where you are | red REAL MONEY badge, red confirmation boxes, a different port (5190) 🔨 (port + label; the red UI ✅) |
| 11 | Nothing open to the internet | the gateway listens on the server itself only; you reach it through an SSH tunnel; only SSH is open, from your IP ✅ (same as the lab) |
| 12 | Brakes | loss limit → kill switch ✅; kill switch from your phone 🔨 (D1-6) |

**We don't need proxies.** The real-money server has its own fixed address, so IP changes at home don't matter (you
reach the server through SSH, and the exchanges only ever see the server). Please don't send real-money traffic
through third-party proxies: they see where the traffic goes, and an exchange can freeze an account whose calls come
from a country it doesn't serve. The server's location is checked once, read-only, before any money goes on it (D3).

---

## 4. The road: stages D0 → D7

Each stage has a goal, the steps, and **"done when"** — we don't start the next stage until the current one is done.

### D0 — Decisions and accounts (Sarmad, can start today, in parallel with D1)
- Decide the open points (§5 A).
- Open / verify the exchange accounts (§5 B): identity verification, 2FA, small funding.
- **Done when:** §15 is filled in, and Binance + Bybit accounts are verified with ~$50 USDT each ready to move in.

### D1 — Build and test everything in the sandbox (the assistant; no real money anywhere)
| # | What | Done when (tested how) |
|---|---|---|
| D1-1 | **Real-money server kit** (`deploy/real-server/`, reusing the lab kit): set up the machine, install a release, roll back, status, logs, backups, tunnel; *rehearsal* mode (sandbox keys) and a deliberate *go-live* command | unit tests + CI deploys it to a clean Ubuntu machine (like the lab kit) |
| D1-2 | **Promotion gate** in its `release`: only a commit that has run ≥ 3 days on the lab server and has CI ✅ (or a typed reason, audited) | tested against fake histories |
| D1-3 | **`keys` command**: hidden prompt per key → straight into the server's `.env`, owner-only; shows only the last 4 characters | tested with dummy keys on the test machine |
| D1-4 | **IP-lock rule** (`MAINNET_REQUIRE_IP_LOCK=true`): on real money, a key without an IP list gets no orders, with the reason on screen | unit tests on each exchange's recorded answer (Binance `ipRestrict`, Bybit `ips`, OKX `ip`, Gate's IP list) |
| D1-5 | **Your account's real fees** (`fetch_trading_fees`): routing, bots and P&L use the rate you actually pay (VIP level, BNB discount); the Venues page shows it next to the public rate | unit tests with recorded answers; confirmed at D3 with the real keys |
| D1-6 | **Alerts on your phone (Telegram)**: gateway started/stopped or down, kill switch on/off, loss limit hit, each real fill, an order an exchange refused, a key problem, a reconciliation BREAK, a daily summary. **`/stop` from your phone engages the kill switch** (stopping only — releasing it needs the screen); only your chat is obeyed; audited | tested on the lab server first (sandbox) |
| D1-7 | **Real-money screen mode**: `npm --prefix frontend run dev:real-server` → `localhost:5190`, red REAL MONEY SERVER label, its own tunnel port | checked in the browser against the rehearsal server |
| D1-8 | **Monthly records export** (CSV: orders, fills, fees, P&L) for your own records | unit tests |
| D1-9 | **OKX in the sandbox**: with your new OKX demo key (§5 B4), the full execution check; if it still refuses, OKX stays out of D4 | live, small sandbox orders |
| D1-10 | **The lab gets today's version** after its week (≈ 2026-10-22), so later releases can be promoted | `deploy.py status` healthy, bots resumed |
| D1-11 | Docs: `REAL_MONEY_GUIDE.md` rewritten around the server; a runbook and the incident playbook (§11) | — |
- **Done when:** all of the above pass, tests + CI ✅, the lab ran the new version ≥ 3 days.

### D2 — The real-money server, tested empty (Sarmad: 10 minutes in AWS; the assistant: the rest)
- Sarmad creates the server and its fixed IP (§5 C). The assistant installs it in **rehearsal mode** (sandbox keys,
  no real key exists yet) and runs the full rehearsal:

| Rehearsal check | Pass means |
|---|---|
| Tunnel + screen on `localhost:5190`, real password login | the red label shows; demo logins refused |
| A sandbox order, a split order, a TWAP | same results as on the laptop |
| Kill switch: engage → order refused → release | refused with the reason; audited |
| Kill switch from the phone (`/stop`) | engaged within seconds; alert received |
| Restart the gateway; reboot the server | comes back by itself; kill switch state kept; algos paused |
| Backups, rollback | a backup is made and pulled; rollback returns to the previous release |
| Alerts | every alert type arrives on your phone |
| `scripts/security_check.py` on the server | "Every attack was refused." |
| Open ports | only SSH is reachable from the internet |
- **Done when:** every row passes; the results are in the journal.

### D3 — Real keys, checked read-only (Sarmad creates keys; nothing is traded)
- Sarmad creates **trade-only, IP-locked** keys on Binance and Bybit (§7) and types them with the `keys` command.
- The server runs `check_exchange_keys.py --mode mainnet`: authenticated, balances, **trade spot: yes, withdraw: no,
  IP-locked: yes**, your real fee rate. It never places an order.
- **Done when:** both exchanges say OK for real money from the server's address. If an exchange refuses the server's
  location, we move the server to a region that exchange serves (a 30-minute change) — never a proxy.

### D4 — Go live, tiny (Sarmad places the orders)
- `go-live` command: removes the sandbox keys from the server, sets real-money mode with the small caps
  ($25 per order, $100 per algorithm, $150 total exposure, $10 loss limit), Binance + Bybit only, restarts; the audit
  log says "REAL MONEY". Bots stay off; copilot as decided (§9).
- **The first-order checklist** (all in the screen, Sarmad clicks):

| # | Test | Check |
|---|---|---|
| 1 | BUY ~$12 BTC, market, best price | order proof: exchange, price, fee; Wallet updates |
| 2 | SELL it back | P&L shows the round trip including real fees |
| 3 | An order that splits across Binance + Bybit | both fills, one parent order |
| 4 | A small TWAP (~$30 over 5 minutes) | slices, arrival + market-VWAP benchmark |
| 5 | An order above the cap | refused by pre-trade risk, nothing sent |
| 6 | Kill switch (screen and phone) | orders refused; released only from the screen |
| 7 | Restart | kill switch kept, algos paused, balances re-read |
| 8 | Reconciliation | **MATCHED** on both exchanges (our ledger = the exchange's balances) |
- **Done when:** all 8 pass, and the journal has the real numbers (prices, fees, slippage, fill times) — the M3
  evidence.

### D5 — Widen, one step at a time (after 1 week of D4 with no surprises)
- **More exchanges, one at a time:** OKX (if D1-9 passed), then Gate with **$5 orders** (no sandbox exists for its
  spot; we say so honestly). Each one: key check (D3) → orders 1–3 and 8 of the D4 checklist.
- **Caps ladder** (only by editing the server's `.env`, the reason written in the journal):

| Step | Per order | Per algorithm | Total exposure | Loss limit | When |
|---|---|---|---|---|---|
| 1 (start) | $25 | $100 | $150 | $10 | D4 |
| 2 | $50 | $200 | $300 | $20 | after 20 clean orders and 7 days of MATCHED reconciliation |
| 3 | your call | | | | after a month at step 2 |
- **Done when:** 3–4 exchanges trade real money (the report's "3–5 exchanges"), reconciliation stays MATCHED.

### D6 — Bots on real money (only if the lab proves them)
- After the lab's extended run (≈ 2026-10-22), we read its verdict together. Only a bot whose results hold up after
  real fees **and** over a falling stretch gets real money.
- Today's best candidate: **profit-cycle, BTC, 1 % target**, a **$30** budget, on a **separate sub-account** if the
  exchange allows one (so the bot's money is physically apart from manual trading). `MAINNET_CYCLE_BOTS=true` only
  then.
- **The advisor does not trade real money** until the lab shows an edge after costs (it lost as predicted).
- **Done when:** a real-money bot ran 2 weeks within its budget, every sale profitable after real fees, its held coins
  reported honestly.

### D7 — Run it and show it (ongoing)
- The daily routine (§11), monthly records export, key renewal, backups pulled to the laptop weekly.
- **M3 evidence:** the D4 journal entry, screenshots of the red screen and the order proofs, the audit log, the
  reconciliation, the alert messages, the cost and slippage numbers.

---

## 5. What I need from you (Sarmad)

**Never paste a key, a secret, a password or a Telegram token into the chat.** You type them into the hidden
prompts yourself.

### A. Decisions (please answer in chat — they block D1/D2)
| # | Decision | Recommended |
|---|---|---|
| A1 | Where real money runs | **its own AWS server in Zurich** (t4g.small, fixed IP, ≈ $20/month), separate from the lab server |
| A2 | Exchanges and order | **Binance + Bybit first**; OKX third (after its sandbox works); Gate fourth with $5 orders. No new exchange needed now |
| A3 | Starting money | **~$50 USDT on each of Binance and Bybit**; caps stay at step 1 |
| A4 | Alerts | **Telegram** (free; also gives you `/stop` from the phone) |
| A5 | Copilot on real money | **AI off at first** (the quick analysis still works; your real balances aren't sent to an outside AI). Later: Claude (paid, its API data isn't used for training) if you want the chat there |
| A6 | Bots on real money | **none until the lab's verdict**; then at most the BTC 1 % cycle bot with $30 |
| A7 | Timeline | tell me **the M3 date** so the stages fit before it |

### B. Accounts (exchange websites — whenever you're ready)
1. **Binance and Bybit accounts in your own name**, identity verification done, **2FA with an authenticator app**
   (not SMS), an **anti-phishing code**, and a **withdrawal address whitelist** (even a stolen web login then can't
   send money to a new address).
2. **Fund** ~$50 USDT each (P2P or deposit). Keep your own savings out of the **Spot / Trading** wallet: on Binance
   in *Funding*, on Bybit in *Funding* — or better, a **sub-account** just for ApnaSOR where the exchange offers it.
   Don't activate margin or futures.
3. **Don't create the API keys yet** — they need the server's fixed IP (after D2). §7 lists the exact settings.
4. **OKX:** a new **Demo Trading** API key (sandbox: read + trade, no withdraw) for D1-9; a real OKX account only
   when we reach D5.
5. **Telegram:** create a bot with **@BotFather** (`/newbot`) and keep its token for the `keys` prompt; send your
   bot one message so it can find your chat.

### C. AWS (console — at D2, about 10 minutes, I'll give click-by-click steps then)
1. **MFA on your AWS login** (the account will hold a machine with real keys).
2. Launch the real-money instance (`apnasor-real`, Zurich, t4g.small, Ubuntu 24.04, its own key pair
   `apnasor-real.pem`), **allocate an Elastic IP** and attach it, security group: SSH from *My IP* + the EC2 Instance
   Connect range only, termination protection on.
3. **Before 2026-11-28:** upgrade the AWS plan (the free plan ends 2026-12-05) — needed for both servers.

### D. During the stages
- D3: create the keys (§7) and type them into the `keys` prompt; type the real-money server's login passwords.
- D4: place the first-order checklist yourself.
- Every cap raise: you edit the server's `.env` (I show you how), with a reason in the journal.

---

## 6. What I (the assistant) will do

Everything in D1, the D2 installation and rehearsal, the D3 read-only check, the `go-live` command, the journal
entries and the docs. I never place a real order, type a key or password, or change AWS or exchange settings — those
stay yours.

---

## 7. Exchange keys: the exact settings

| | Binance | Bybit | OKX (D5) | Gate (D5) |
|---|---|---|---|---|
| Key type | System generated (HMAC) | System-generated API key | API key (+ **passphrase**) | API v4 key |
| Allow | Reading; **Enable Spot & Margin Trading** (spot only is used; don't activate margin) | Read-Write; **Spot: Trade** only | Read; **Trade** | **Spot trade**; everything else read-only |
| Must be OFF | Withdrawals, Universal Transfer, Futures, Margin loan, Options | Withdraw, Transfer, Derivatives, Earn, NFT | **Withdraw** | Withdraw / wallet area (Gate is read strictly: a writable wallet area = refused) |
| IP | **Restrict to trusted IPs → the server's Elastic IP** | **Bind IP → the server's IP** (also stops the 90-day expiry) | **IP whitelist → the server's IP** | **IP whitelist → the server's IP** |
| What the gateway asks | `apiRestrictions` | `query-api` | `account/config` | `main_keys` |

If the gateway refuses a key, **make a new key** with the right settings — never weaken the check.

---

## 8. Money and limits

- Start with **~$100 in total** (D4). The caps bound the worst case: a single order ≤ $25, everything open ≤ $150,
  and a $10 trading loss stops all trading until you release it.
- The caps rise only by the ladder in D5, on the server, by you.
- What the money pays for: exchange fees (≈ 0.1 % per trade, less with a VIP level or BNB discount — D1-5 shows your
  real rate). Expect small losses from fees on the test orders; that's the price of real evidence.

---

## 9. Bots, advisor and copilot on real money

| | Real money |
|---|---|
| Manual orders and algorithms (TWAP, VWAP, IS, POV, iceberg) | ✅ from D4, every order confirmed with the red box |
| Profit-cycle bot | ⏳ only after the lab verdict (D6), `MAINNET_CYCLE_BOTS=true`, small budget, sub-account |
| Advisor | ❌ until the lab shows an edge after costs; even then it always asks |
| Lab bots | ❌ never (they stay on the lab server) |
| Copilot | per decision A5; **Auto never** (code-enforced); every proposal waits for your confirm |

---

## 10. Keys and access, in one list

- Real keys exist in **two places only**: the exchange, and the server's `.env` (owner-only file). Not the laptop,
  not the lab server, not git, not chat, not a teammate's machine.
- Each real key: trade-only, IP-locked, verified by the exchange's own answer every 10 minutes.
- The server: only SSH open, only from your IP (+ the AWS browser terminal), its own key file on your laptop only.
- Renew keys every 3 months, and **delete them on the exchange** the day we stop.
- Teammates never get real keys or the real server's access; they work on the sandbox. For the panel demo, the
  laptop opens the real-money screen through the tunnel.
- Logins on the real-money screen: real passwords only (scrypt hashes on the server), audited.

---

## 11. Running it

**Daily (2 minutes):** read the phone's daily summary; if anything is red, open the screen.
**Weekly:** `backup-pull` (the records to the laptop); glance at the reconciliation.

**Incident playbook:**
| Situation | What happens by itself | What you do |
|---|---|---|
| Anything looks wrong | — | `/stop` on the phone (kill switch on), then look |
| Loss limit reached | kill switch on, alert | find the cause before releasing |
| Exchange refuses orders / key problem | that exchange gets no orders, alert | check the key on the exchange's site |
| Reconciliation BREAK | alert; nothing is "fixed" automatically | find the cause (a trade outside ApnaSOR?) before accepting balances |
| Gateway down | alert; restarts itself | if it stays down: `status`, `logs` |
| Server unreachable | the bots and trading stop with it; IOC orders leave nothing resting on the exchanges | AWS console; if needed, **delete the API keys on the exchanges** (instant, total stop) |
| Key leaked (suspected) | — | delete the key on the exchange; new key; check the trade history |

Because every order is immediate-or-cancel, **nothing is ever left resting on an exchange** — stopping the system
never leaves orders behind.

---

## 12. Evidence for M3 and the panel

The journal entry of D4 (real prices, fees, slippage vs plan, fill times), the order proofs, the audit log of the
first real orders, reconciliation MATCHED, the key verdicts (trade-only, IP-locked), the alerts, and the
separation diagram (§2). Honest wording: "real orders with small amounts behind hard caps"; no profit claims.

---

## 13. Costs

| Item | Monthly |
|---|---|
| Real-money server (t4g.small + disk + fixed IP) | ≈ $20 |
| Lab server (existing) | ≈ $21 |
| Telegram, Gemini free tier | $0 |
| Exchange fees | ≈ 0.1 % of what is traded (a few cents per test order) |

The AWS free plan ends **2026-12-05**: upgrade before 2026-11-28, or wind the lab down after its verdict.

---

## 14. Risks and honest limits

- **Price risk is real:** a bot or a position can lose; the caps and the loss limit bound it, they don't remove it.
- **A trade-only, IP-locked key** can still trade badly if the server itself is taken over — that is why the server
  has only SSH open, from your IP, and the caps.
- **Gate can't be rehearsed in a sandbox** (no spot test funds): it is checked with $5 real orders, and we say so.
- **Exchange rules can change** (permissions, regions, fees); the gateway's checks show a change within minutes, and
  the server can move region.
- **Regulation:** use accounts in your own name, from a country each exchange serves; check that personal crypto
  trading is allowed for you. Nothing here is financial or legal advice.
- **No profit is promised:** the first weeks of real money are a measurement.

---

## 15. Decisions log

| # | Decision | Answer | Date |
|---|---|---|---|
| A1 | Where real money runs | — | — |
| A2 | Exchanges and order | — | — |
| A3 | Starting money | — | — |
| A4 | Alerts | — | — |
| A5 | Copilot on real money | — | — |
| A6 | Bots on real money | — | — |
| A7 | M3 date | — | — |
| — | Lab extended run | until ≈ 2026-10-22, or until Sarmad stops it; no release to the lab before then | 2026-10-15 |
| — | Sandbox and real money fully separate (own server, own keys, own records) | yes (Sarmad) | 2026-10-15 |
| — | Proxies | not used for real money (fixed-IP server instead) | 2026-10-15 |
