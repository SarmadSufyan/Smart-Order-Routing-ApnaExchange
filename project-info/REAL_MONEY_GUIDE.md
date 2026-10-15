# Real money — guide (Phase D)

> **Status (2026-10-12):** the **safety rails are built and tested** (this guide's part 2). No real order has been
> placed yet: that step is Sarmad's (part 5), with his own keys and a small amount.
> **2026-10-15: the plan changed — real money gets its own server with a fixed IP, keys locked to it, and nothing
> real on the laptop.** The stages, the walls between sandbox and real money and what Sarmad does are in
> [`PHASE_D_PLAN.md`](PHASE_D_PLAN.md); parts 3–5 below are rewritten around the server in its stage D1-11.
> Read with: [`ROADMAP.md`](ROADMAP.md) *Full product* D, [`SECURITY_GUIDE.md`](SECURITY_GUIDE.md) (logins),
> [`EXCHANGE_API_KEY_SECURITY.md`](EXCHANGE_API_KEY_SECURITY.md) (how keys are stored).

---

## 1. What "real money" means in ApnaSOR

The money stays **on the exchange** (Binance, Bybit, …) in your own account. ApnaSOR holds no funds: it reads your
balances and places orders through an **API key** you create on the exchange. So the one thing that matters most is
**what that key is allowed to do**:

| The key can… | Needed? | Why |
|---|---|---|
| read balances and orders | ✅ yes | the wallet, the P&L, reconciliation |
| trade spot | ✅ yes | every order ApnaSOR sends is a spot order |
| **withdraw** | ❌ **never** | with it, a leaked `.env` = money gone; without it, the worst a thief can do is trade |

Same code as on testnet: every order still goes through `OrderEntry` → pre-trade risk → the router → price-protected
IOC children. Only the keys, the database and the safety rails below change.

---

## 2. The safety rails (what the code does for you)

| Rail | What it does | Where |
|---|---|---|
| **Two switches** | `EXCHANGE_MODE=mainnet` **and** `ALLOW_MAINNET=true`, or the gateway refuses to start | `config.py` |
| **Trade-only key, confirmed by the exchange** | At start and every 10 min the gateway asks each exchange what the key may do (one read-only call). A venue trades only once the exchange confirms *trade spot: yes, withdraw: no*. A key that can withdraw, can't trade, or whose answer is unclear gets **no orders**, with the reason on the *Venue Connectivity* page | `exchanges/key_permissions.py`, `ccxt_venue.py: _watch_key_permissions` |
| **Hard caps in `.env`** | `MAINNET_MAX_ORDER_USDT` (25), `MAINNET_MAX_ALGO_USDT` (100), `MAINNET_MAX_EXPOSURE_USDT` (150), `MAINNET_MAX_TRADING_LOSS_USDT` (10). The limits in force are never above them; the Risk page can tighten a limit but a change past a cap is refused; the loss limit can't be switched off. Raising a cap = edit `.env` and restart: a person at the machine, not a click or a stolen login | `risk_engine/ceilings.py` |
| **Loss limit → kill switch** | trading P&L below −$10 (default) engages the kill switch by itself | `risk_monitor.py` |
| **Real passwords only** | demo logins (password = user name) are off; a user without a real password can't log in | `SECURITY_GUIDE.md` |
| **Every money action is confirmed on screen** | the order ticket and the algo form have a red *REAL MONEY* tick box (the button stays locked until it is ticked, and changing the order clears it); approving an advisor's trade and *Sell open lots now* ask first | `components/shared.tsx: RealMoneyCheck, confirmRealMoney` |
| **Bots** | profit-cycle bots trade without asking, so on real money they are **off** until you set `MAINNET_CYCLE_BOTS=true` (after reading the practice lab's verdict). The advisor works but **always asks** (auto-approve is refused). Lab bots never run on real money | `automation/engine.py` |
| **Separate records** | real money has its own database (`data/apnasor-mainnet.db`) and its own login-signing key; testnet history never mixes in | `config.py` |
| **Never on the lab server** | the server's install refuses `EXCHANGE_MODE=mainnet`, and `deploy.py env` never copies a mainnet key | `deploy/lab-server/` |

What the exchange is asked, per exchange (checked against each one's API documentation):

| Exchange | Endpoint | Fields read |
|---|---|---|
| Binance | `GET /sapi/v1/account/apiRestrictions` | `enableSpotAndMarginTrading`, `enableWithdrawals`, `ipRestrict` |
| Bybit | `GET /v5/user/query-api` | `readOnly`, `permissions.Spot` has `SpotTrade`, `permissions.Wallet` has `Withdraw`, `ips`, `expiredAt` |
| OKX | `GET /api/v5/account/config` | `perm` (`read_only`, `trade`, `withdraw`), `ip` |
| Gate | `GET /api/v4/account/main_keys` | each area's `read_only` flag. Gate shows no separate withdraw flag, so **its wallet area must be read-only** or the key is refused (the strict reading) |

Live so far: on Bybit's demo the call answered *trade spot: yes, withdraw: no, IP list: no* for our demo key. Binance's
and Gate's **test** environments don't offer the endpoint. That doesn't matter, because the check runs only on real
money, but it means the Binance, OKX and Gate answers are first seen with your real keys (part 4 does that, read-only).

---

## 3. Before you start (one time)

1. **An exchange account with identity verification done.** Start with **Binance and Bybit**: both are proven on their
   test environments. OKX: some ISPs block okx.com (CLAUDE.md *Gotchas*), and its demo refused our orders (50124).
   Gate: execution never tested (its spot test wallet is empty).
2. **Only a small amount on it**: e.g. $50–100 of USDT on spot. Best is a **separate sub-account or a separate
   account** that holds only ApnaSOR's money. A manual SELL in ApnaSOR uses whatever coins the account holds, so
   keep your own savings elsewhere. (Bots only ever spend their own pocket.)
3. **Create the API key** on the exchange's website (API management). Choose a system-generated (HMAC) key with:
   - reading **on**, **spot trading on**, **withdrawals OFF**, and nothing else (no futures, margin, transfers);
   - optionally **bind it to your IP address**. That is safer, but only if your IP is fixed: a home IP can change
     (the likely reason the lab server's SSH timed out on 2026-10-12), and a bound key then stops working. Bybit disables a key
     with no IP after 90 days (the check tells you the date).
4. **Real login passwords for all three users** on this laptop: `python scripts/hash_password.py admin` (then
   `trader1`, `risk_mgr`), put the lines in `.env`, then `python scripts/security_check.py` must say
   "Every attack was refused." ([`SECURITY_GUIDE.md`](SECURITY_GUIDE.md))

---

## 4. Check the keys before switching (read-only)

Put the keys in `.env` under **Real money** (`BINANCE_MAINNET_API_KEY` / `_SECRET`, `BYBIT_MAINNET_API_KEY` /
`_SECRET`, …). Leave `EXCHANGE_MODE=testnet`. Then:

```
python scripts/check_exchange_keys.py --mode mainnet
```

It reads balances and asks each exchange what the key may do, and never places an order. You want, per exchange:

```
  Binance (V1)     OK - authenticated on mainnet. Balances: USDT 50
                   OK for real money - trade-only key (trade spot: yes, withdraw: no, IP list: no)
                     note: not limited to your IP address (recommended if your IP is fixed)
```

`REFUSED FOR REAL MONEY - … can WITHDRAW …` → delete that key on the exchange, make a new one without
withdrawals, and check again. Never "fix" this by weakening the check.

---

## 5. Switch on, and the first real order (Sarmad does this)

1. In `.env`: `EXCHANGE_MODE=mainnet`, `ALLOW_MAINNET=true`. Keep the caps small; set `ENABLED_EXCHANGES` to the
   exchanges you funded (e.g. `binance,bybit`).
2. `python start_all.py`, log in with your real password. You should see: the red **MAINNET · REAL FUNDS** badge;
   on *Venue Connectivity* a green 🔒 *Trade-only key confirmed by the exchange* per exchange (until the
   exchange answers, the venue shows "checking with the exchange…" and gets no orders); on *Risk Manager* the
   **REAL MONEY — hard caps** note; in the *Audit Log*: "Gateway started (mainnet …) — REAL MONEY, hard caps: …".
3. **First order: about $12–15**, e.g. BUY 0.00015 BTC, Market, Best price. Tick the red box and submit. Then
   check: the order proof (which exchange, price, fee), the *Wallet* (balances, P&L), reconciliation (MATCHED).
4. Sell it back the same way. Then one small TWAP (e.g. $20 in 4 slices).
5. Test the brakes: engage the kill switch and confirm an order is refused; release it.
6. Write the results in `DEVELOPMENT_JOURNAL.md` (prices, fees, slippage vs the planned price: real numbers for the
   report and the panel).

**Back to testnet** at any time: `EXCHANGE_MODE=testnet`, restart. The real-money records stay in their own
database.

---

## 6. What happens when…

| Situation | What ApnaSOR does | What you do |
|---|---|---|
| A key can withdraw | that exchange gets no orders; the reason is on the Venues page and in the log (`key_refused_for_real_money`) | new key without withdrawals → `.env` → restart |
| The exchange doesn't answer the permission question (network) | no orders there until it answers; it asks again every 30 s | nothing (check your internet / VPN, CLAUDE.md *Gotchas*) |
| Someone edits the key on the exchange's site | seen at the next check (≤ 10 min) | — |
| A limit change past a cap | refused: "Refused on real money: the per-order limit $1,000.00 is above the real-money ceiling $25.00 (MAINNET_MAX_ORDER_USDT in .env)…" | raise the cap in `.env` and restart, if you mean it |
| An order above the per-order cap | refused by pre-trade risk, nothing sent | smaller order, or an algo (≤ the algo cap) |
| Trading loss reaches the loss limit | kill switch engages; nothing trades until a risk manager releases it | find out why first |
| You try to start a profit-cycle bot | refused until `MAINNET_CYCLE_BOTS=true` | read the lab's verdict first (`AUTOMATION_GUIDE.md`) |
| A restart | kill switch state kept, algos and bots paused (never resumed automatically on real money) | resume what you want |

---

## 7. Honest limits (say these to the panel)

- **Custody:** the exchange holds the money, not ApnaSOR. We are not "non-custodial"; we read balances and trade
  through a key.
- **A trade-only key limits the damage; it doesn't remove it.** A stolen key can still trade (at bad prices); the
  caps, the IP binding and the small balance are what bound that.
- **The bot fund on one exchange account is a bookkeeping separation** (ApnaSOR's gate), not a physical one. A
  sub-account per fund would make it physical (a later Phase D step).
- **Fees:** the router uses each exchange's public base-tier taker fee (Binance 0.10 %, Bybit 0.10 %). Your
  account's real rate (VIP level, a BNB discount) can be lower; the exchange's fill report, which is recorded,
  is the truth.
- **Testnet results don't carry over one-for-one:** real books have different queues and fills; the first real
  orders are a measurement, not a demo of profit. Nothing here promises profit.
- **Gate's permission reading is strict** (a writable wallet area counts as "may withdraw"), so a Gate key may be
  refused although it can't withdraw. That's the safe side.

---

## 8. Next in Phase D

1. **Sarmad:** parts 3–5 with Binance (and Bybit), first real orders, numbers into the journal.
2. Real fee rates from the account (`fetch_trading_fees`) instead of the public base tier, once a real key exists to
   test against.
3. After the lab's verdict: decide `MAINNET_CYCLE_BOTS`, the threshold and the advisor's bracket on real money;
   then where real-money bots run 24/7 (the lab server is testnet only, on purpose).
4. Later: a Web3 wallet / DEX venue (ROADMAP D).

## 9. Panel Q&A

- *"What stops your system from stealing or losing the user's money?"* The key can't withdraw (the exchange itself
  confirms it, or we don't trade), every order is capped by limits that no screen can raise, a loss limit stops
  everything, and every action is in the audit log with who did it.
- *"Why ask the exchange instead of trusting the user's setup?"* People make mistakes when they create keys. The
  exchange's own answer is the only fact; a key we can't verify is treated as unsafe.
- *"Why can't an admin raise the limits from the screen?"* A stolen admin login or a mis-click shouldn't be able to
  turn a $25 system into a $10,000 one. Raising a cap needs the machine itself (edit `.env`, restart).
- *"Did you trade real money?"* Only what part 5 records in the journal. Before that, the honest answer is: the
  real-money path is the testnet path plus these rails, verified on testnet and in a no-keys real-money dry run.
