# Incident Guard Guide

> What ApnaSOR does when an exchange is in trouble, why, what it can't do, and how to rehearse it.
> Code: [`backend/services/venue_monitor/incident_guard.py`](../backend/services/venue_monitor/incident_guard.py),
> [`backend/exchanges/transfers.py`](../backend/exchanges/transfers.py). Tests:
> [`backend/tests/test_incident_guard.py`](../backend/tests/test_incident_guard.py).

---

## 1. Why it exists

On 2026-10-29 Binance suffered the largest exchange theft on record: about $6.3bn by its own estimate, from hot
wallets, and it closed withdrawals. We asked what ApnaSOR would have done that night. The answer was uncomfortable: **it
would have kept routing to Binance.** Everything we measured looked fine.

Measured on 2026-10-30, about 24 hours in, with withdrawals still closed:

| What we looked at | What it showed |
|---|---|
| Binance's own coin list | Withdrawals closed on **990 of 990** coins, **21 of our 21**; deposits open on all 21 |
| Our health monitor | Binance **HEALTHY, 75 %**: books synced, feed live, latency normal |
| Its prices against the others | Median **+3.6 to +5.7 bps**: nothing a price check would flag |
| Best price, Binance vs Bybit, Gate, HTX | Binance the **best sell on 17 of 20 coins, after fees** (20:50 UTC) |
| A normal exchange the same day | Gate 1 of 21 closed (TRX), HTX 3 of 21 (SUI, HBAR, ARB) |

The last two rows are the point. With withdrawals closed, arbitrage can't move coins between exchanges, so prices stop
being pulled together, and a router that follows the best price follows it into the exchange nobody can leave. **A
healthy order book says the market is open. It doesn't say the exit is.**

## 2. What it does

```
 every 60 s   each exchange: are deposits and withdrawals open, per coin?   (transfers.py)
 every  5 s   each exchange: how far is its price from the other exchanges?
                                    │
              NORMAL ──────► WATCH ──────► GUARDED
              a few coins    the owner     blacklisted through RiskControls:
              closed is      is told;      no new orders; orders, algos and bots
              an ordinary    routing       keep running on the other exchanges;
              day            unchanged     the proof shows the price passed up
```

| Level | When |
|---|---|
| **WATCH** | A quarter of the coins closed for withdrawal, or USDT itself, or half of the coins closed for deposit, or 3+ coins far from the market |
| **GUARDED** | **Half** of the coins closed for withdrawal, or half of them far from the market |

A coin counts as closed only when **none** of its networks is open. "Far from the market" means ±75 bps
(`INCIDENT_GAP_BPS`) from the median of the *other* exchanges, held for 30 s (`INCIDENT_GAP_HOLD_S`), on fresh
real-market books. A single coin that drifts leaves routing for that coin alone: that price is a warning, not a bargain.

GUARDED uses the existing **blacklist** on purpose: it goes through `RiskControls` (design rule 1), so it is replicated
by RAFT, survives a restart and also takes the venue out of the NBBO. Nothing new can drift out of step with the
kill-switch rules.

**The rule is the kill switch's: tightening is automatic, loosening is a person's.**

- A signal that clears is reported, but the block stays until someone lifts it on the Venues page.
- If someone lifts it while the signal continues, that is respected until the episode ends. A restart forgets this
  decision and blocks again: fail-safe.
- A level rises at once and falls only after 5 quiet minutes (`INCIDENT_CLEAR_AFTER_S`), so a flicker can't flood the
  phone.
- If no exchange is left, orders are **refused** and the proof says why. Nothing is forced.
- An unreadable list is **not** an incident and not an all-clear. The last answer stands for five reads, then it counts
  as unknown; the price signal keeps working.

From the phone: `/block binance` takes an exchange out (tightening, so the phone may do it). Lifting needs the screen.

## 3. Where each answer comes from

| Exchange | Source | Needs a key? |
|---|---|---|
| Binance | Public list on its website (not a documented API, so it may change). With a real-money key, the documented `GET /sapi/v1/capital/config/getall` | No |
| Gate | Public `GET /api/v4/spot/currencies` | No |
| HTX | Public `GET /v2/reference/currencies` | No |
| Bybit | `GET /v5/asset/coin/query-info` | **Yes** |
| OKX | `GET /api/v5/asset/currencies` | **Yes** |

So in the sandbox and on the lab server, Bybit and OKX show "not published" and only the price signal covers them.

The lists are read directly and parsed in a worker thread, not with ccxt's `fetch_currencies`: measured, ccxt held the
event loop ~250 ms on Gate's and HTX's lists, and every order and book update waits behind that; this way it is ~20 ms.

## 4. How to rehearse it (the fire drill)

The Venues page has an **Incident drill** button on each exchange (RISK_MANAGER or ADMIN, sandbox only; real money
refuses it, `POST /api/venues/{id}/incident-drill`). It pretends that exchange closed its withdrawals, and everything
else is real:

1. Press **Incident drill** on Binance: the banner, the "Incident: Binance" pill and the alert appear, marked DRILL.
2. Send a small order: it fills on the other exchange and its proof names what was left out.
3. **End drill**: the signal clears, and the block stays, as after a real incident.
4. **Lift the block**: the exchange comes back. Audit Log → Risk shows every step with who did it.

## 5. Verified live

2026-10-30, on the real incident (nothing simulated in the detection; orders in the sandbox):

- Within seconds of starting, the guard read Binance's list and blocked it; RAFT agreed (log index 36).
- A sandbox sell of 0.0002 BTC filled on Bybit, and its proof says: "Binance (V1) quoted a better bid ($76,956.00, 7.6
  bps better) but was skipped: incident guard: withdrawals closed on 21 of 21 coins".
- With a drill on Bybit as well, the order was refused cleanly: both exchanges named in the proof.
- A drill ended: the signal cleared, the block stayed; a person lifted it; a `trader` login was refused (403).
- Four restarts: the block held each time.
- Found live and fixed: an alert that promised "the other exchanges" when none was left, and one that quoted $7,696 of
  a $96,640 balance because wallet prices were still arriving (hence the 15 s warm-up).

## 6. Honest limits (say these first)

- **It can't stop a hack.** This one began inside Binance. No router touches that.
- **It can't bring money back.** Our keys can't withdraw, by design, so balances on an exchange stay there. The guard
  limits *new* exposure and shows how much is parked. Spreading funds across exchanges before trouble is the only real
  defence, and the venue-concentration alert exists for that.
- **It reacts to the exchange, it doesn't predict it.** The transfer signal fires when the exchange closes withdrawals,
  so it is after the theft has begun. In this incident the price signal would not have fired (gaps stayed far below
  ±75 bps on most coins).
- **It was not running that night.** It was built the day after and verified while withdrawals were still closed.
- **Its signals have gaps.** Binance's public list is undocumented. Bybit and OKX need a real-money key, and those signed
  reads have been tested against recorded formats only: no real key is ever on a development machine.
- **It costs price.** Skipping a venue can mean a worse fill: 7.6 bps on the order above. That is the point, and the
  proof shows it.
- **A false alarm is possible.** A real outage or a data error could block a healthy exchange until a person lifts it.
  `INCIDENT_GUARD_AUTO_BLOCK=false` makes it warn without blocking.

## 7. Settings

```
INCIDENT_GUARD_ENABLED=true          # off = no signals are read
INCIDENT_GUARD_AUTO_BLOCK=true       # off = it warns but never blocks
INCIDENT_GAP_BPS=75
INCIDENT_GAP_HOLD_S=30
INCIDENT_TRANSFERS_INTERVAL_S=60
INCIDENT_CLEAR_AFTER_S=300
```

## 8. Panel Q&A

**Would this have saved money on 29 October?** Not by itself, and we don't claim it. Binance says it will cover the
losses. What the guard does is stop adding exposure at the moment nobody outside can know whether an exchange is
solvent. That night, nobody could.

**Why block on sells too? Selling reduces exposure.** Selling a coin on that exchange leaves the USDT on the same
exchange: custody risk doesn't change. Add that fills during an incident may be disputed or reversed, and it is simpler
and safer to stop.

**Why not move the money out automatically?** Our keys can't withdraw. That rule protects far more often than it would
help: a stolen key that could withdraw is a lost account. And when this signal fires, withdrawals are already closed.

**Why half of the coins?** Measured: on an ordinary day exchanges close a few coins for wallet maintenance (Gate 1, HTX
3 of 21), and in this incident it was all of them. A quarter warns, half blocks.

**Why trust the exchange's own list?** It is the one thing the exchange can't fake without misleading every customer
trying to withdraw, and it needs no account. It also works in the sandbox, where the exchange's test systems know
nothing about the real one.

**Isn't a price gap a chance to profit?** That is exactly the trap. A gap that stays open usually means money can't move
to close it. The arbitrage detector measures such gaps; it never trades.

**What stops the guard itself from being abused?** It can only tighten: it has no order entry and no way to release
anything. Its one action is the same blacklist an operator uses, audited with the actor `incident-guard`.
