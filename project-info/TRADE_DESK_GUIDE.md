# Trade Desk Guide

> What the **Trade Desk** page does, how each number is made, what it showed on the first live run, and what it is
> not. Research R1 (plan of 2026-10-25); built 2026-11-04. Read-only: nothing on this page can place an order.

The idea comes from an MIT-licensed US-stock day-trading toolkit (a scanner, an entry timer, an exit planner and a
"Time Machine" of 10,000 replayed sessions) that Sarmad shared. Only the ideas were taken; every rule, number and
replay here runs on our own crypto candles, with our own fees and the slippage the practice lab measured.

---

## 1. Why it exists

The research page answers "what does the model think of BTC?" one coin at a time. A day trader's first question is
the opposite: **of everything that trades, where is something starting right now, and did that pattern ever pay on
that coin?** The trade desk answers it every five minutes, for every coin the exchanges share, and attaches the
evidence to each answer so that "no" is as visible as "yes".

## 2. What it does, in order

1. **The universe.** At start and once an hour it reads each enabled exchange's public market list and keeps every
   USDT spot coin that **all** of them list (234 on 2026-11-04 for Binance, Bybit and Gate; 147 on 2026-10-25 when OKX
   and HTX were counted too), the 20 traded coins first, capped at `SCANNER_MAX_COINS` (200). A sandbox's list is
   not the market, so only exchanges whose market data comes from the real market count. `SCANNER_UNIVERSE=symbols`
   scans the trading universe only.
2. **The candles.** For each coin it keeps small histories in the research store (`data/market-history.db`): 400
   daily bars, 1,500 hourly bars and two days of 5-minute bars; the traded coins keep 30 days of 5-minute bars for the
   opening-range time machine, and their hourly and daily years belong to the research sync and are never trimmed.
   Candles come from the first exchange that lists the coin (Binance, then Bybit, OKX, HTX, Gate), three coins at a
   time with a pause between requests. The first collection for 200 coins took 130 s; later scans fetch only the
   new bars.
3. **The setups** (`backend/services/research/scanner.py`, pure numpy), at the close of the latest bar:

   | Setup | Kind | Bars | Rule |
   |---|---|---|---|
   | Channel breakout | entry | 1h, 1d | close above the highest close of the previous 20 bars (the Turtles' rule) |
   | Reclaimed the 20-bar average | entry | 1h, 1d | previous close below the 20-bar average, this close above it |
   | Oversold bounce | entry | 1h, 1d | RSI(14) under 30 at the previous bar, and this bar closed higher |
   | Volume surge | entry | 1h, 1d | volume more than 2 σ above the previous 24 bars (σ at least a tenth of their mean), on a rising bar |
   | Pull-back in an uptrend | entry | 1h, 1d | 20-average above the 50, price above the 50, price within 1 ATR of the 20-average and below its 10-bar high |
   | Bull flag | watch | 1h, 1d | a run of more than 10 % over bars −15..−5, then five bars within a 5 % range; trigger = the flag's high |
   | Volatility squeeze | watch | 1h, 1d | Bollinger band width in the narrowest tenth of the last 100 bars; trigger = the upper band |
   | Opening-range breakout | entry | 5m | the first 5-minute close above the first 30 minutes' range after 00:00, 08:00 or 13:30 UTC, within 4 hours of the open; once per session |

   An **entry** setup is itself the trigger (the bar's close) and counts while it is at most one closed bar old
   (three 5-minute bars for the opening range). A **watch** setup names the level still to break and stays listed
   while its condition holds. A setup's history counts **fresh** fires only, the first bar of a run, as a trader takes
   one trade per signal.
4. **The evidence per hit** (`timemachine.replay_history`): the desk's bracket, **stop 1 ATR below the entry, target
   1.5 × that above it, a time limit** of a day of hours, ten days, or four hours of 5-minute bars, is placed at every
   past fire of the same setup on the same coin, and the real bars that followed are read: stop first, target first
   (the stop is checked first within a bar), or neither. Costs are the taker fee both ways plus slippage, in R.
   The verdict needs at least 20 fires: **paid before** (win rate above the break-even rate and a positive average R),
   **no edge**, or **too few fires**. The table ranks paid-before first, then the unproven, then the ones history
   argues against, and within each by dollars traded in the last day, like the stock scanner.
5. **The entry timer:** *triggered* (an entry setup's bar, or a watch setup whose live price crossed its trigger),
   *waiting* (below the trigger), *expired* (too old, or the session's entry window has closed). The live price is the
   consolidated mid for the traded coins and the last close for the others.
6. **The plan** (`POST /api/trade-desk/plan`): entry = the live mid (or the last close); stop by the rule (ATR, or
   the opening range's midpoint for a range breakout); target by reward : risk; **size by the research planner's
   rules** (fixed-fractional risk, capped by half-Kelly from the history, by the money available and by the per-order
   limit); the setup's history as in step 4; and the **time machine** (below). One verdict: *favourable* only when the
   real history and the replays agree; *mixed*; *coin flip*; *skip* when either argues against the bracket.

## 3. The time machine

`timemachine.simulate`, pure numpy. Every past bar of the chosen timeframe over the last `TIME_MACHINE_DAYS` (60; daily
bars get at least a year) is kept as three ratios to the close before it (high, low, close) and grouped by **time of
day**: hourly bars have 24 slots, 5-minute bars 288, daily bars one. A replay starts at the entry price and, for each
of the next bars, draws one past bar from that bar's slot (jittered by up to two slots, with replacement,
independent per bar) and scales it onto the path so far, so a quiet 02:00 UTC draws from past quiet 02:00s and a
14:30 from past New York opens. The stop is checked before the target; a bar that touches both is a loss. Ten thousand
replays with a fixed seed, so the same question gives the same answer; 2,000 of the paths draw the fan chart.

It reports: target first, stop first, neither (still open at the time limit), "green" (target first or still open above
the entry), the average R after costs, the median closing price with its 5th and 95th percentiles, the median bars to
an outcome, and a verdict against the **break-even share**: with reward R per unit of risk and costs c in R, the
target must come first in more than (1 + c) / (1 + R) of the decided replays. Five points above that is favourable,
five below unfavourable, between is a coin flip.

Costs: `ARB_TAKER_FEES_BPS`'s reference fee both ways plus the slippage the practice lab measured on our own orders
(the assumption of 2 bps until it has enough), the same numbers the research backtester and planner use.

## 4. Verified live (2026-11-04, the laptop sandbox, 14:07–14:11 UTC)

- Universe: "every USDT spot coin listed by Binance, Bybit, Gate (234 coins)", capped at 200. OKX's list did not count:
  its domain does not resolve on this network (the known DNS problem), so the universe was wider than with OKX.
- First scan: 200 of 200 coins had candles, 0 collection errors, 130.5 s for the first collection (about 1,000 requests),
  then 164 live setups: pull-back 70, oversold bounce 33, reclaimed average 23, squeeze 18, volume surge 8, channel
  breakout 7, opening-range breakout 4 (the 13:30 UTC session, 30 minutes after its open), bull flag 1; by bars: hourly
  94, daily 66, 5-minute 4; entry timer: 145 triggered, 19 waiting.
- **History says**: 12 paid before, 58 too few fires, 94 no edge. Most patterns, on most coins, did not pay after
  costs on their own history, and the page says so in the same font as the ones that did.
- The top hit: SOL/USDT, oversold bounce, daily: 12 won, 9 lost, 6 timed out of 27 past fires, 57.1 % vs 40.8 % needed,
  +0.354 R per fire. Its plan: entry 92.815 (the live mid), stop 88.59 (1 ATR = 4.22 below), target 99.15, size 2.3676
  SOL = $219.75 for $10 at risk (1 % of $1,000). The time machine (10,000 replays of the next 10 daily bars) said the
  target comes first 29.7 %, the stop 62.4 %, neither 8.0 %, average −0.221 R: 32.2 % of decided replays vs 42.1 %
  needed, unfavourable. **Verdict: skip** — the history favours it, the replays do not, and the desk does not average
  two opinions into a yes.
- Speed: a plan with the 10,000-replay time machine in 135 ms; the time machine alone on BTC/USDT hourly (a
  1,440-bar pool, 60 sessions per slot) in 201 ms; a scan does its numpy in worker threads, so order books and orders
  never wait for it.
- The research's own histories were untouched by the desk's trimming: BTC/USDT still had 44,288 hourly and 3,020 daily
  bars afterwards. The store grew by about 500,000 rows for the 180 extra coins (about 25 MB).
- 33 tests (`test_research/test_trade_desk_maths.py`, `test_trade_desk_service.py`): every setup on a designed tape,
  the opening range (one fire per session, incomplete ranges never fire, no entries after the window), fresh fires,
  the time machine (a flat pool decides nothing, a rising pool always hits the target, a bar touching both is the
  stop, the same seed gives the same answer, the bands are ordered), the history check against hand-counted outcomes,
  costs raising the break-even bar, the universe intersection with the traded coins first and a sandbox's list ignored,
  a scan end to end on fake exchanges, the plan, the range-mid stop, the entry timer.

## 5. Honest limits (say these first)

- **A setup is a pattern, not a forecast**, and a history of 20–90 fires is a small sample. The desk ranks by what
  paid before; it does not claim it will pay again. Twelve "paid before" out of 164 is the honest ratio.
- **The time machine knows nothing about tomorrow.** It resamples the coin's own past bars by time of day; a listing,
  a hack or a rate decision is not in the pool. Its odds are for a calm continuation of the last 60 days.
- **Independent draws:** each bar is drawn on its own, so the replays carry no momentum or volatility clustering
  beyond the time-of-day structure. That is the same simplification the source kit makes; it is stated, not hidden.
- **Within a bar the order of high and low is unknown**, so a bar touching both counts as the stop, in the replays and
  in the history. Odds are therefore a little pessimistic.
- **The wider universe has short histories** (400 daily, 1,500 hourly bars), so daily setups on those coins are mostly
  "too few fires". The 20 traded coins have years.
- **Long only.** Spot, no shorts, like everything else here.
- **Nothing is sent.** A plan is a proposal for a person. R3 chains it through the copilot's confirm step; the advisor
  bot may read it in the lab first.

## 6. Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TRADE_DESK_ENABLED` | `true` | off = no scans, the page says so |
| `SCANNER_UNIVERSE` | `common` | `common` = every USDT spot coin all the enabled real markets list; `symbols` = the trading universe |
| `SCANNER_MAX_COINS` | `200` | the cap, traded coins first |
| `SCANNER_INTERVAL_S` | `300` | seconds between scans |
| `SCANNER_SESSION_OPENS_UTC` | `00:00,08:00,13:30` | the opening ranges' session opens |
| `SCANNER_RANGE_MINUTES` | `30` | the opening range's length |
| `SCANNER_ENTRY_WINDOW_MINUTES` | `240` | no opening-range entries later than this after the open |
| `TIME_MACHINE_REPLAYS` | `10000` | replays per time machine |
| `TIME_MACHINE_DAYS` | `60` | the pool: days of the coin's own bars (daily bars: at least a year) |

API: `GET /api/trade-desk/status`, `GET /scan`, `POST /rescan`, `GET /entry?symbol&setup&timeframe`,
`POST /time-machine`, `POST /plan` (all logged-in users; Swagger at `/docs`).

## 7. Panel Q&A

- *Is this a prediction engine?* No. It finds patterns and measures what followed them on that coin, and replays the
  coin's own bars. The page shows the losing majority next to the winners.
- *Why time of day?* Crypto trades 24 hours but not evenly: turnover restarts at the UTC day, London and New York.
  Drawing a 02:00 bar from past 02:00s keeps the replay's volatility honest for the hour it is simulating.
- *Why check the stop first?* Because within one candle nobody knows which came first. Counting the loss is the
  conservative reading, and the same rule is applied to the history and the replays, so they stay comparable.
- *Where do the fees come from?* The same reference fee and lab-measured slippage as the backtester and the planner,
  so a bracket that looks good here is judged by the costs we actually pay.
- *Why 1 ATR and 1.5 R?* A common day-trading bracket; the plan lets the person change both and the history and
  replays are recomputed for the new bracket.
- *What would make it trade?* R3: the copilot turns a favourable plan into a proposal with its reasons and a person
  confirms; the same order path, risk checks and incident guard as every other order.
