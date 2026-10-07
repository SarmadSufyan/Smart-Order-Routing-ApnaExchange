# Algorithms Guide — every routing and execution algorithm in ApnaSOR

> Current as of 2026-10-07. Replaces the stock-era `ALGORITHMS.md` for anything about trading algorithms.
> Code: `backend/services/routing_engine/strategies/` (routing) and `backend/services/algos/` (execution).
> Every formula below is a pure function with its own unit tests (`backend/tests/test_routing/`, `backend/tests/test_algos/`).

ApnaSOR has two families of trading algorithms:

| Family | Question it answers | Where you choose it |
|---|---|---|
| **Routing strategies** | *Where* does one order go right now — which exchanges, how much on each? | Order ticket → **Routing strategy**; Algo Orders → **Routing of each child** |
| **Execution algorithms** | *When* — how is a big order spread over time? | Algo Orders → **Algorithm** (or **✦ Auto**) |

They stack: an execution algorithm cuts a parent order into children over time, and every child is routed across the exchanges by the routing strategy you picked. **Every child goes through `OrderEntry.submit`** — the same pre-trade risk checks, balance check, kill switch and price protection as a manual order. No algorithm can trade any other way.

---

## Part 1 — Routing strategies (one order, many exchanges)

All three share one implementation (`best_price.py`): they merge every eligible exchange's order book into one ladder, respect each exchange's balance, lot size and minimum order (a share below an exchange's minimum is re-routed), and write the routing proof. They differ in one thing each.

### 1.1 Best price (fee-aware) — the default

**Idea:** buy from the cheapest sellers first (sell to the highest buyers), across all exchanges — but "cheapest" means **after the exchange's fee**, because that is what the trader really pays.

```
all_in(price, venue) = price × (1 + fee_bps/10⁴)   for a BUY
                     = price × (1 − fee_bps/10⁴)   for a SELL
sort every book level by all_in, best first; take levels until the order is filled
```

Fees come from `ARB_TAKER_FEES_BPS` (the same table the arbitrage detector uses; our children are IOC, so always taker). An exchange missing from the table gets the highest listed fee — an unknown fee never makes a venue look cheaper.

**Example:** Binance asks $100.00 with a 10 bps fee (all-in $100.10); Gate asks $99.95 with 20 bps (all-in $100.15). Raw price says Gate; all-in says Binance — and the proof explains why.

### 1.2 Liquidity sweep — fill now, slightly worse price

**Idea:** best price is optimal when books hold still. They don't — once liquidity is taken on one exchange, prices elsewhere can move, and a child planned on one book can find it gone. A sweep hits **every exchange at once**, sized by the liquidity each has under **one hard price cap**.

```
cap      = best all-in price × (1 ± SWEEP_BAND_BPS/10⁴)          (default 20 bps)
avail(v) = liquidity on exchange v whose all-in price is within the cap (capped by v's balance)
share(v) = Q × avail(v) / Σ avail                                (on v's lot grid; lots lost to
                                                                  rounding go to the best venue first)
every child is an IOC LIMIT at v's cap — it may walk v's book up to the cap, never past it
```

The cap is sent as the child's limit exactly (not widened by the usual 50 bps protection band). **Live (2026-10-07):** a 0.0006 BTC sweep sell split Binance 53 % / Bybit 47 %.

### 1.3 Smart — best price plus measured reliability

**Idea:** an exchange that quotes the best price but often fails to fill our IOC children isn't really the cheapest — the missed quantity must be bought again later or elsewhere.

```
ratio(v)  = what v filled / what we sent it, over our last 50 IOC children on v (measured)
miss(v)   = max(gap from v to the next-best exchange's all-in price, one consolidated spread)
extra(v)  = (1 − ratio(v)) × miss(v)        added to v's fee, then rank exactly like best price
```

Refusals for our own reasons (e.g. not enough balance) don't count against an exchange. Fewer than 5 measured children → ranked on price and fee only (the proof says so). With every exchange filling reliably, smart = best price.

---

## Part 2 — Execution algorithms (one big order, over time)

One engine (`algos/engine.py`) runs them all; the maths is in `algos/schedules.py`.

**Shared behaviour:** the planned children are pre-flighted through pre-trade risk before the first one is sent (an algo that would cross a limit half way is refused up front); **no planned slice is smaller than the smallest order a *funded* exchange accepts** (a tiny slice is merged into its neighbour); a child that fills short rolls into the next one (at most 2× its planned size); pause / cancel act between children, never during one at the exchange; the kill switch pauses every running algo; a restart leaves them paused; three children in a row rejected before reaching any exchange pause the algo with the reason.

**Benchmarks on every algo:** cost vs the **arrival price** (the mid when it started — implementation shortfall), and vs the **real market's VWAP** over the same time (from the exchanges' public 1-minute candles, summed across exchanges). `+` = cost (paid more / received less).

### 2.1 TWAP — even slices

```
slice i (1..N) is due at start + (i−1)·D/N and targets a cumulative Q·i/N
```
Optional jitter (±20 % of the interval) makes the pattern harder to spot. Simplest and predictable.

### 2.2 VWAP — follow the market's usual rhythm

**Idea:** trade more when the market usually trades more, so our footprint stays proportional and the average price tracks the market's VWAP.

```
for each of the last D days (default 5): take the same clock window, split it into N buckets,
   and measure each bucket's share of that day's volume (1-minute candles, all exchanges summed;
   a minute is spread evenly over its 60 s when a bucket covers only part of it)
w_j      = average share of bucket j over the days       (each day normalised first)
target_i = Q × (w_1 + ... + w_i)
```
No history reachable → even slices, and the algo says so. Honest limit: slices shorter than a minute can't be told apart by 1-minute candles. **Live:** a 6-minute, 3-slice VWAP planned slice 1 at 38.7 % (vs 33.3 % even) from the last 5 days.

### 2.3 Implementation shortfall — Almgren–Chriss

**Idea:** trading fast costs impact; trading slowly leaves you exposed to price moves. Almgren & Chriss (2000) give the schedule that minimises `expected cost + λ · variance`:

```
holdings x_k = Q · sinh(K·(1 − k/N)) / sinh(K)        k = 0..N   (K = κT; K → 0 is a TWAP)
trades   n_k = x_{k−1} − x_k                          → front-loaded
expected impact cost  E = ε·Σ|n_k| + (η/τ)·Σ n_k²
price risk (1 σ)        = σ · √(τ · Σ x_k²)
```
* **Urgency → K**: LOW 0.5, MEDIUM 2, HIGH 5 (HIGH trades ~70 % in the first fifth). K is the dimensionless form of the risk aversion λ.
* **ε** = half the spread (crossing it), **η** = measured on the live consolidated book: walking it for one average slice, `η = (extra price paid) × τ / slice`.
* **σ** = live volatility from our own mid prices (or the last 2 h of candles).

The page shows expected cost ± risk for the chosen schedule **and for an even TWAP**, so the trade-off is visible. **Live:** HIGH urgency, 0.0004 BTC: risk ±$0.0022 vs ±$0.0070 for TWAP at the same expected cost (the book was deep enough that impact was just the spread).

### 2.4 POV — percentage of volume

**Idea:** be a fixed share of the real market's volume as it happens — the smallest footprint, no fixed end.

```
every check (default 10 s):  V = real-market volume since the start (candles, all exchanges)
                             want = p·V − filled
                             cap  = max(2 · p · (volume in the last interval), one minimum order)
                             send min(want, cap, remaining, per-order limit) once it reaches a tradable size
stops when filled (COMPLETED) or at its deadline (EXPIRED); time and volume while paused don't count
```
Honest limit: on testnet our fills are not part of the measured (mainnet) volume, so the share is of the market we *watch*.

### 2.5 Iceberg (IOC version)

**Idea:** never show the whole order. A classic iceberg leaves a small visible order resting on the book; **our rule is "no resting orders"** (every child is IOC), so ours shows *nothing* on the book — it waits until the best price on an exchange we can trade is at or better than your limit, then takes **one piece** of about the display size, at the limit.

```
every refresh (± 20 %):  if best executable price ≤ limit (buy) / ≥ limit (sell):
                             piece = display × (1 ± variance)   (never below one minimum order;
                                                                 never leaves an untradable remainder)
                             send it as an IOC LIMIT at your limit
```

### 2.6 Auto — which algorithm, and why

`POST /api/algos/recommend` (the **✦ Auto** button) looks at the order and the live market, and recommends:

1. **One order now** — if the visible books absorb it for at most 2 bps beyond crossing the spread and it fits the per-order limit. Splitting would only add price risk.
2. Otherwise **work it over time** at a participation rate set by urgency (LOW 5 %, MEDIUM 10 %, HIGH 20 % of the market's recent volume per minute) within the longest time you allow: **HIGH → implementation shortfall**, **MEDIUM → VWAP** (TWAP without volume history), **LOW → POV**. Slices ≈ every 15 s, each at least two minimum orders and within the per-order limit.

It returns the reasons, warnings (e.g. "even over the longest time this is 30 % of the market's volume", "above the algo limit") and the numbers it used. Nothing is sent: **Use this** fills the form; for one order, **Place as one order**.

---

## Panel Q&A

* **"Are these real algorithms or names on a menu?"** — Real: each has its own maths (above), unit tests, and was run live on testnet (journal 2026-10-07). The one thing listed before that wasn't real ("vwap" as a routing choice) was removed from routing and built as a real execution algorithm.
* **"Can an algorithm bypass risk?"** — No. Every child is an ordinary order through `OrderEntry.submit`; the engine has no exchange access.
* **"Why IOC-only — isn't a resting order cheaper (maker fee)?"** — It is, and it's a possible future mode. We chose IOC because a resting order can be filled at a moment the system isn't watching (kill switch, restart); IOC means the system always knows its exposure.
* **"How do you know VWAP beat the market?"** — We measure it: every algo reports its average price against the real market VWAP over the same minutes, from the exchanges' own candles.
* **"Is Almgren–Chriss realistic here?"** — It assumes linear temporary impact and a random-walk price; we measure η from the live book and σ from live prices, and show the cost/risk trade-off rather than claiming optimality in practice.

## Honest limits

1-minute candle resolution (VWAP buckets, POV volume); η from one snapshot of the book; testnet fills don't move the mainnet volume POV measures; fees are base-tier (no VIP/discount); OKX excluded where its DNS is blocked.
