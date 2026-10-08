# Bots & Lab (Phase C) — explained, decided, verified

> The automation fund, the profit-cycle bot, the advisor agent and the practice lab: what each one does,
> the choices behind them, how to check them yourself, and the questions a panel (or a teammate) will ask.
> Code: `backend/services/automation/`, page **Bots & Lab** (`frontend/src/pages/Automation.tsx`).
> Built 2026-10-08 (Sarmad). Decisions taken with Sarmad the same day.

## In five sentences

1. You move money from the wallet into an **automation fund**; each bot gets its own **pocket** from it and can
   never spend more, and manual orders can't use what the fund holds.
2. The **profit-cycle bot** buys a small lot and sells it only when the price beats what it cost + both fees +
   your threshold (0.3 % by default), then repeats — every *completed* cycle earns at least the threshold.
3. The **advisor** reads ApnaSOR's own research (next-hour forecast, the trade planner's ATR bracket, how that
   bracket did on ~4,900 past hours) and proposes a bracketed buy **with its reasons**; you approve it — or, on
   testnet only, it approves itself within its limits.
4. The **practice lab** writes down every bot order and every lot (the market conditions at the buy, what
   happened after real-market fees) and learns from them: measured trading costs, a scoreboard of variants with
   confidence intervals, and outcome models trained on our own trades.
5. Every bot order goes through `OrderEntry.submit` — pre-trade risk, the kill switch, routing, price-protected
   IOC children — the kill switch pauses every bot, and a restart leaves them paused.

## Words used below

| Word | Meaning |
|---|---|
| Fund | Money set aside for bots (`AutomationFund`, kept in `data/apnasor-<mode>.db`, key `automation_fund`) |
| Pocket | One bot's slice of the fund: budget, cash, the coins it holds and what they cost, its profit |
| Lot | One buy a bot made, and how it ended (sold at target / stop / time limit, sold by you, released) |
| Real-market fee | Each exchange's base-tier taker fee (`ARB_TAKER_FEES_BPS`: Binance and Bybit 0.10 %) |
| R | One unit of risk: the distance from entry to stop. A 2 R target is twice as far as the stop |
| Lab sample | One row the lab learns from: an order (`EXECUTION`), a cycle lot or an advisor trade |

## 1. The automation fund — money kept apart

**What you see:** *Bots & Lab → Automation fund* (put in, unassigned, bots' cash, bots' coins, value, P&L at
real-market fees, what manual trading may still use) and the same summary on the Wallet page.

**How it is kept apart — two rules at the one order entry** (`fund.check_order`, called by `OrderEntry`):
- a **bot order** may only use its own pocket: cash for a buy, the coins it holds for a sale;
- a **manual order** (the order button, an algo, the copilot) may only use what the fund doesn't hold:
  free on the exchanges minus the fund's cash and the bots' coins.

With nothing in the fund the gate never refuses anything — manual trading is exactly as before.

**Options considered**

| Option | Verdict |
|---|---|
| F1 — ApnaSOR's own book + a gate at the one order entry | **Chosen.** Works on every exchange and on testnet today; every number is ours and audited |
| F2 — A separate exchange **sub-account** with its own API keys for bots | **For real money (Phase D).** Physically separate; needs new keys and a second adapter per exchange. The fund's book maps onto it unchanged |
| F3 — Move the money to a separate exchange account by transfer | No: transfers between accounts are withdrawals — our keys never have withdraw rights |

**Honest limit (say it):** on one exchange account the money is physically pooled; the separation is ApnaSOR's
book and gate. A teammate on the shared testnet keys (or a testnet reset) can still move the real balance — the
fund card warns when it holds more than the exchanges show free.

## 2. The profit-cycle bot

**Defaults (decided with Sarmad, 2026-10-08):** threshold **0.3 %** over both fees, budget **$50**, **$10** per
buy (so up to 5 lots), a new lot only **0.5 %** under the cheapest open one and 60 s after the last buy, no new
buys while open lots are **10 %** under cost.

**The sell target of a lot** (`cycle.target_price`):

    target = cost incl. the buy fee x (1 + threshold) / (coins held x (1 - sell fee))

Selling at the target returns the cost plus the threshold **after** the sell fee. The sale is a LIMIT order at the
target (rounded *up* to the price tick): it can fill only at the target or better — never below.

**Real-market fees:** Binance's spot testnet charges **no fees** (found 2026-10-08), Bybit's demo charges 0.10 %.
The bot counts the higher of what was charged and the real-market fee, on both sides, so a profit on a fee-free
testnet is a profit at real-market fees too. The sell fee is the one of the exchange that holds the lot's coins
(found live: taking the highest fee of every exchange put Gate's 0.20 % on a lot bought on Binance).

**Dust:** exchanges trade in steps (Binance BTC: 0.00001 BTC ≈ $1). When the buy fee is taken in the coin, a lot
holds a little less than a whole number of steps; what can't be sold is carried into the bot's next sale, so it
never grows past one step per coin (found while testing with Binance's step: without it ~10 % of a $10 lot stayed
behind every cycle).

**What it can't promise:** that the price comes back after a buy. A lot bought before a fall waits; the bot never
sells it below its target. You can (*Sell open lots now*) — your decision, audited.

## 3. The advisor agent

**Each scan (every 5 min), for every coin it watches** (`advisor.evaluate_coin`):
1. the research forecast for the next hour: P(up), and whether that model has a proven edge;
2. the trade planner's bracket: stop 1.5 × hourly ATR under the entry, target 2 R, sold anyway after 12 h — and how
   that exact bracket did on every past hour (win rate vs break-even, average result in R **after costs**);
3. the practice lab: how earlier advisor trades with a similar P(up) ended, and the slippage we measured.

It proposes a **buy** (spot, long only) when P(up) ≥ 55 % **and** the bracket's past average after costs is
above 0 R (optionally: only with a proven edge). Every reason is printed with the proposal.

**Ask or auto (decided 2026-10-08):** *Ask me* — nothing happens until you approve (the default). *Auto* — it
approves itself within its limits (2 open trades, 6 trades a day, its pocket) — **testnet only**: the code refuses
auto on real money. An approval is refused if the price moved more than 1 % since the proposal, or if it is
already outside the bracket.

**First live result (2026-10-08 08:10 UTC, honest):** P(up) for the next hour was 49.5–51.8 % on all five coins,
and the default bracket averaged **−0.20 to −0.30 R after costs** on ~4,900 past hours of each coin. So the
default advisor proposed **nothing** — the right answer for that bracket. The lab's *explore* variant (any bracket
history) trades anyway, to learn whether the forecasts help in practice.

## 4. The practice lab — learning from our own trades

Sarmad's idea (2026-10-08): on testnet we can trade any amount, so let bots trade continuously and learn the
patterns from **practice**, not only from what is written on the internet.

**What it writes down** (table `lab_samples`, same database as the audit log):

| Sample | When | What |
|---|---|---|
| `EXECUTION` | every child order of a bot | the mid when it left, the price we got, the fee, how much filled |
| `CYCLE_LOT` | every profit-cycle lot | market conditions at the buy (next-hour/next-day forecasts, RSI, ATR, volatility, spread, order-book imbalance, time) → how long until sold, profit at real-market fees |
| `ADVISOR_TRADE` | every advisor trade | the same conditions + the forecast it acted on → target / stop / time limit, return at real-market fees |

**What it learns from them** (`lab.py`, page section *Practice lab*):
- **Costs**: median slippage per exchange and coin. With 20+ filled orders on a coin, the research backtester
  and trade planner use **our measured slippage instead of the assumed 2 bps** (`ResearchService.set_cost_source`).
- **Scoreboard** per variant (a bot's label): profit per day, per day on budget, per lot with a 95 % bootstrap
  interval, median hold, share sold within 24 h.
- **Comparisons** of variants on the same coin by profit per day — a permutation test, once each has 3+ days.
- **Outcome models**: "will a cycle lot sell within 24 h?" and "will an advisor trade hit its target?" —
  logistic regression on the samples, walk-forward tested against the baseline (the research module's method),
  reported once there are **200** labelled samples. Until then the page shows the progress.

**Experiments (testnet only, one button each):** *Profit-cycle threshold test* (BTC and ETH × 0.1 / 0.3 / 1 %,
$200 each, $20 per buy), *Advisor: explore* (auto, P(up) ≥ 50 %, any bracket history, $300), *Advisor: strict*
(auto, the normal rule, $200). Starting one moves its budget from the wallet into the fund.

**Statistics without assumptions:** bootstrap intervals and reshuffling tests with a fixed seed — easy to explain
("we reshuffled the days 2,000 times") and the same on every machine.

**Honest limits:** test fills are not real-market fills (Bybit's demo tracks the market closely; Binance's spot
testnet has its own book), so costs are indicative; a model needs hundreds of samples, i.e. days to weeks of
running; the lab runs only while the gateway runs (24/7 hosting is Phase E).

## Safety rules (the same as the algos)

- Every bot order goes through `OrderEntry.submit`: pre-trade risk, the kill switch, the fund gate, routing,
  price-protected IOC children. A bot never talks to an exchange.
- The kill switch pauses every running bot; a resume is refused while it is engaged.
- A restart leaves every bot **paused** (never auto-resumed) — verified live 2026-10-08.
- An order already at an exchange is never interrupted; pause / stop take effect between orders.
- Three orders in a row refused before reaching any exchange pause the bot with the reason.
- Auto-approve and lab experiments are testnet only (code-enforced).
- Everything is audited: `fund_*`, `bot_started / paused / resumed / stopped`, `bot_buy`, `bot_sell`,
  `advisor_proposal / approved / rejected / expired`, `lab_preset_started` (+ every order as usual).

## Verify it yourself (≈ 10 min, testnet)

1. `python start_all.py`, log in, open **Bots & Lab**.
2. Move $100 into the fund. Wallet → the purple strip shows it; *free for manual trading* drops by $100.
3. Start a profit cycle (defaults). Within seconds: one lot, its sell target (+~0.5 % with Binance/Bybit fees),
   "next buy at … or lower". Audit Log → `bot_buy`.
4. Restart the gateway: the bot is **PAUSED** ("Gateway restarted while this bot was running"); resume it.
5. Start an advisor in *Ask me*: after its first look, each coin says why it did or didn't propose. For a
   proposal: Approve → a lot with a stop and a target; Reject → audited.
6. Engage the kill switch: every bot pauses. Release it, resume.
7. Start a lab experiment; after an hour the lab shows measured costs and the first closed lots.

## Questions the panel may ask

- **"Does the bot make money?"** — Every *completed* cycle beats its fees plus the threshold, at real-market fees.
  What it can't do is make the price come back after a buy; the lab measures how often and how fast lots sell.
- **"Isn't the fund just a number in your database?"** — On one exchange account, yes, and we say so; it is
  enforced at the only order entry both ways. With real money a sub-account with its own keys makes it physical.
- **"Your advisor didn't trade — is it broken?"** — No: its rule needs a forecast leaning up *and* a bracket that
  made money on past candles after costs; on 2026-10-08 none did. Saying "no trade" is the honest output.
- **"What did the lab learn?"** — Show the costs table (measured slippage per exchange), the scoreboard with its
  intervals, and the model progress bar — with the number of samples each rests on.
- **"Is this AI?"** — The advisor is rules over measured statistics, and the outcome models are logistic
  regressions tested out of sample. The LLM copilot is separate and only fills order forms.

## Where things live

| What | Where |
|---|---|
| Fund (book, gate, moving money) | `backend/services/automation/fund.py` |
| Profit-cycle decisions (pure) | `backend/services/automation/cycle.py` |
| Advisor rule (pure) | `backend/services/automation/advisor.py` |
| Lab (samples, statistics, models, presets) | `backend/services/automation/lab.py` |
| Bots running (loops, safety, orders) | `backend/services/automation/engine.py` |
| API | `backend/gateway/routers/automation.py` (`/api/automation/...`) |
| Models | `backend/shared/models/automation.py` |
| Gate at the order entry | `backend/services/order_state/entry.py` (`set_guard`) |
| Page | `frontend/src/pages/Automation.tsx` (+ the fund strip on `Wallet.tsx`) |
| Tests | `backend/tests/test_automation/` (fund, rules, lab, engine against a stand-in exchange) |
