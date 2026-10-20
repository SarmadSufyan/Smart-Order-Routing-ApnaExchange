# Real money — guide (Phase D)

> **Status (2026-10-20):** the safety rails (part 2) and the **real-money server kit** (parts 3–7) are built and
> tested — the kit end to end on a clean Ubuntu machine in CI. No real order has been placed yet: that is Sarmad's
> (part 6). Real money runs only on its own server with a fixed IP, keys locked to it, nothing real on the laptop:
> the plan, the stages and the walls between sandbox and real money are in [`PHASE_D_PLAN.md`](PHASE_D_PLAN.md).
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
| **Key locked to the server's IP** | on real money a key the exchange doesn't report as IP-locked gets no orders (`MAINNET_REQUIRE_IP_LOCK=true`, the default; Binance `ipRestrict`, Bybit `ips`, OKX `ip`, Gate `ip_whitelist`) | `key_permissions.py: real_money_problem` |
| **Your own fee rate** | on real money each exchange is asked for the account's taker fee (at start, then every 6 h); routing, the bots and the lab use it instead of the public rate; the Venues page and `check_exchange_keys.py --mode mainnet` show it | `exchanges/fees.py`, `LiveFees` |
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
money, but it means the Binance, OKX and Gate answers are first seen with your real keys (part 5 does that, read-only).

---

## 3. Where real money runs: its own server

Real money runs **only on the real-money server** (AWS Zurich, Maham's account, its own fixed IP — decision A1);
the laptop and the lab server stay in the sandbox ([`PHASE_D_PLAN.md`](PHASE_D_PLAN.md) §2–3). Everything below is
done from the laptop with `deploy/real-server/deploy.py`, over SSH; you see the server's screen through a tunnel.
Real keys never touch the laptop: you type them into a hidden prompt and they go straight into the server's
root-only `keys.env`.

**Before you start (one time):** Binance and Bybit accounts verified, 2FA with an authenticator app, an
anti-phishing code, a withdrawal address whitelist, ~$50 USDT each in the spot / trading wallet (your savings in
the *Funding* wallet). Don't create the API keys yet — part 5.

---

## 4. Set the server up and rehearse (sandbox keys, no real money) — stage D2

```
python deploy/real-server/deploy.py init --host <the server's Elastic IP> --key C:\Users\<you>\.ssh\apnasor-real.pem
python deploy/real-server/deploy.py replace-ssh-key     # a key made on this laptop; the AWS-made one stops working
python deploy/real-server/deploy.py passwords           # the server's own login passwords (no demo login there)
python deploy/real-server/deploy.py setup --test        # machine, settings, this laptop's SANDBOX keys, first release
python deploy/real-server/deploy.py telegram            # phone alerts + /stop (optional now, needed before go-live)
python deploy/real-server/deploy.py status
```

To see its screen: `python deploy/real-server/deploy.py tunnel` in one terminal,
`npm --prefix frontend run dev:real-server` in another, then http://localhost:5190 (a red **REAL MONEY SERVER**
badge; in rehearsal the environment still says SANDBOX). Then go through the rehearsal checklist
(PHASE_D_PLAN D2): orders, a TWAP, the kill switch from the screen and from the phone, a restart, a reboot, a backup,
a rollback, `security_check.py`.

The server refuses an unsafe setup by itself: before every release and before go-live, `check_settings.sh` checks
the stage's rules (rehearsal: sandbox mode; live: mainnet, no sandbox key left, a real password for every user).

---

## 5. Real keys, checked read-only — stage D3

1. On each exchange, create the API key exactly as [`PHASE_D_PLAN.md`](PHASE_D_PLAN.md) §7 says: reading + spot
   trading only, **withdrawals OFF**, **restricted to the server's Elastic IP**.
2. `python deploy/real-server/deploy.py keys` — type each key and secret (hidden). They go to the server only.
3. `python deploy/real-server/deploy.py check-keys` — from the server's own address, read-only. Per exchange:

```
  Binance (V1)     OK - authenticated on mainnet. Balances: USDT 50
                   OK for real money - trade-only key (trade spot: yes, withdraw: no, IP list: yes)
                   account fee: your Binance taker fee is 0.100%, public rate 0.100% (routing and bots use yours)
```

`REFUSED FOR REAL MONEY - … can WITHDRAW …` or `… isn't locked to an IP address …` → delete that key on the
exchange, make a new one with the right settings, run `keys` and `check-keys` again. Never weaken the check.
If an exchange refuses the server's location, tell the assistant: the server moves region (never a proxy).

---

## 6. Go live, and the first real orders — stage D4 (Sarmad does this)

1. `python deploy/real-server/deploy.py go-live` — type `REAL MONEY` to confirm. The sandbox keys are removed, the
   server switches to mainnet with the hard caps (`MAINNET_MAX_*`, default $25 / $100 / $150 / $10), the safety check
   must pass (if it refuses, the server goes back to rehearsal unchanged), and the release log records it.
2. `deploy.py status`: each exchange shows *key_confirmed_trade_only* and its account fee. On the screen: the red
   **MAINNET · REAL FUNDS** badge; on *Venue Connectivity* 🔒 *Trade-only key confirmed by the exchange (no
   withdrawals, accepted only from listed IP addresses)*; your phone gets "Gateway started (mainnet …)".
3. The first-order checklist (PHASE_D_PLAN D4): ~$12 BUY, SELL it back, a split order, a small TWAP, an order above
   the cap (refused), the kill switch from the screen and from the phone, a restart, reconciliation MATCHED.
4. Write the real numbers into `DEVELOPMENT_JOURNAL.md` (prices, fees, slippage vs plan, fill times) — the M3 evidence.

---

## 7. Day to day on the real-money server

| What | Command |
|---|---|
| How is it doing? (also the key and fee verdicts, the release log) | `deploy.py status` (your phone gets a summary every morning) |
| Its log | `deploy.py logs` (`-f` to follow) |
| The records to the laptop | `deploy.py backup-pull` (kept 90 days on the server) |
| A month's trades as CSV | `python scripts/export_records.py --db data/real-server/backups/<backup>/apnasor-mainnet.db --month 2026-11` |
| A new version | `deploy.py release` — only a commit that ran ≥ 3 days on the lab server with CI green; otherwise `--reason "…"` (written into the release log) |
| Back to the version before | `deploy.py rollback` |
| A cap change | edit the caps in your laptop's `.env`, `deploy.py env` (the server restarts with them; the reason goes in the journal) |
| Stop everything now | `/stop` on the phone, or the kill switch on the screen; in the worst case delete the API keys on the exchanges |

---

## 8. What happens when…

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

## 9. Honest limits (say these to the panel)

- **Custody:** the exchange holds the money, not ApnaSOR. We are not "non-custodial"; we read balances and trade
  through a key.
- **A trade-only key limits the damage; it doesn't remove it.** A stolen key can still trade (at bad prices); the
  caps, the IP binding and the small balance are what bound that.
- **The bot fund on one exchange account is a bookkeeping separation** (ApnaSOR's gate), not a physical one. A
  sub-account per fund would make it physical (a later Phase D step).
- **Fees:** on real money the router and the bots use the taker fee the exchange reports for your account (VIP
  level included); until it has answered, the public base tier (Binance 0.10 %, Bybit 0.10 %). A BNB discount
  isn't in that answer; the exchange's fill report, which is recorded, stays the truth for what was paid.
- **Testnet results don't carry over one-for-one:** real books have different queues and fills; the first real
  orders are a measurement, not a demo of profit. Nothing here promises profit.
- **Gate's permission reading is strict** (a writable wallet area counts as "may withdraw"), so a Gate key may be
  refused although it can't withdraw. That's the safe side.

---

## 10. Next in Phase D

1. **Sarmad:** parts 4–6 (rehearsal, keys, go-live) with Binance and Bybit; first real orders; numbers into the journal.
2. ~~Real fee rates from the account~~ ✅ 2026-10-20 (`exchanges/fees.py`; confirmed with the real keys at part 5).
3. After the lab's verdict: decide `MAINNET_CYCLE_BOTS`, the threshold and the advisor's bracket on real money;
   then where real-money bots run 24/7 (the lab server is testnet only, on purpose).
4. Later: a Web3 wallet / DEX venue (ROADMAP D).

## 11. Panel Q&A

- *"How do you keep testing and real money apart?"* Three places: the laptop and the lab server only ever hold
  sandbox keys (the lab's installer refuses real money); real money runs only on its own server, whose keys are
  locked by the exchanges to that server's address — and once it is live, that server refuses sandbox keys. A version
  reaches it only after three days on the lab server with all tests green.

- *"What stops your system from stealing or losing the user's money?"* The key can't withdraw (the exchange itself
  confirms it, or we don't trade), every order is capped by limits that no screen can raise, a loss limit stops
  everything, and every action is in the audit log with who did it.
- *"Why ask the exchange instead of trusting the user's setup?"* People make mistakes when they create keys. The
  exchange's own answer is the only fact; a key we can't verify is treated as unsafe.
- *"Why can't an admin raise the limits from the screen?"* A stolen admin login or a mis-click shouldn't be able to
  turn a $25 system into a $10,000 one. Raising a cap needs the machine itself (edit `.env`, restart).
- *"Did you trade real money?"* Only what part 6 records in the journal. Before that, the honest answer is: the
  real-money path is the testnet path plus these rails, verified on testnet and in a no-keys real-money dry run.
