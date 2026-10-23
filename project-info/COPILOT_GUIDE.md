# Copilot — guide

> **Status (2026-10-15):** built, tested (534 backend tests) and **checked live with Gemini** (`gemini-3.1-flash-lite`,
> free tier): prices, portfolio, risk, techniques, arbitrage, coin analyses and follow-ups answered from the live
> facts; orders, TWAP, VWAP, implementation shortfall, POV, iceberg and a profit-cycle bot proposed correctly
> (also from Roman Urdu); an "ignore your rules" request still waited for Confirm. The chat needs an AI key in
> `.env` (Gemini free, or Claude paid). Claude is verified with unit tests only (no key on the laptop).

## 1. What it does, in one line each

Open **✦ COPILOT** in the top bar on any page and type in plain English:

| You type | It does |
|---|---|
| "Should I buy SOL?" · "Any good trade right now?" | runs the **analyst** on the coin (or all coins) and answers BUY / NO TRADE / REDUCE **with its reasons for and against**; a BUY comes with a ready order, stop and target — **you confirm** |
| "How is my portfolio doing?" · "What's BTC at?" · "Is the kill switch on?" | answers from the **live numbers** (wallet, prices, risk, bots, exchanges) |
| "Which techniques worked on ETH?" · "What does the forecast say?" | answers from the **research** (technique study, forecasts and their track record) |
| "Buy $20 of ETH over 2 minutes" · "Sell 0.01 BTC with VWAP over an hour" · "Iceberg 0.05 ETH, show 0.01, limit 2500" | proposes the **order or execution algorithm** (TWAP, VWAP, implementation shortfall, POV, iceberg) — **you confirm** |
| "Start a profit-cycle bot on BTC with $40" · "Start the advisor" | proposes the **bot** with its settings — **you confirm** |
| "What is RSI?" | explains the concept (general knowledge, labelled as such) |

Follow-ups work ("and ETH?"): the last few turns are passed along as context.

## 2. How it decides — the model never does

```
your message ──► language model fills a strict form (what you asked: action, coin, amount, topic…)   untrusted
              ──► code checks every field; builds the order / algorithm / bot, or gathers the facts:
                      the ANALYST: technique study (years of candles), forecasts + track record,
                      trade plan (ATR stop/target replayed on history), order book, your holdings
              ──► for answers: the model words the facts — it may not add numbers of its own;
                      if the model can't be reached, code words them plainly
              ──► a proposal waits for Confirm ──► fresh price check ──► OrderEntry / AlgoEngine / bots
                      (pre-trade risk, kill switch, funding gate, price protection — like the order button)
```

**The analyst's rules** (`backend/services/agents/analyst.py`, pure and tested):

| Evidence (real-market history, after fees) | Counts |
|---|---|
| a technique that **worked** on ~5 years of hourly candles | +1 if it says "in" now, −1 if "out" (at most ±2) |
| a technique that worked on the daily history (~8 years) | ±1 |
| next-hour / next-day forecast — **only with a proven edge** | ±1 each (P(up) > 55% / < 45%); without an edge: nothing |
| the stop/target bracket replayed on past hours | +1 made money, −1 lost |

**BUY** needs score ≥ 2, at least one working technique saying "in", and a bracket that isn't a loser.
**REDUCE** needs you to hold the coin and score ≤ −2. Everything else is **NO TRADE** — the honest default.
Confidence is "low" or "medium", never "high".

**The techniques** (`backend/services/research/techniques.py`): moving-average trend, time-series momentum
(Moskowitz–Ooi–Pedersen), MACD crossover, channel breakout (the Turtles' rule), RSI reversion, Bollinger reversion —
textbook settings, nothing fitted to a coin. "Worked" = beat simply holding on risk-adjusted return (Sharpe after
costs) over the whole history **and** recently, and made money recently. The same table is on the **Research &
Forecasts** page.

## 3. What it found on real data (2026-10-15)

- **Hourly BTC, 5 years (43,799 candles):** with 0.12% per trade (fee + slippage), *every* technique lost — MACD
  −97.5% over 1,655 trades, moving-average trend −68.5% — while holding made +33.8%. Rules that trade this often
  are eaten by fees.
- **Daily BTC, ~8 years (3,000 candles):** the moving-average trend *worked* (beat holding, risk-adjusted, over the
  whole period and recently).
- **The verdict on BTC:** NO TRADE — the one working daily signal is cancelled by the hourly stop/target bracket,
  which lost on 4,898 past hours (32% wins vs 45% needed after fees); neither forecast has a proven edge.

That is the point of the design: it says "no" when the evidence says no.

**The live chat check (Gemini, 2026-10-15)** found wording problems, fixed in code rather than with more
instructions: the model quoted raw numbers ("4.9473690999999995 BNB", a return of "-0.493302" that was really
−49.3 %), internal exchange ids ("V4") and called real-market prices "sandbox data". Now the facts reach the model
**already formatted** (`readable_facts`: $ prices, % returns, exchange names) with the environment spelled out
("only orders and balances are sandbox"); "score" reaches it as "evidence_points" (it had written "a confidence
score of −2"). One request in about twenty timed out on the free tier: the client now retries once after a
timeout or a 5xx (not after a refused key). Answers take 2–20 s.

## 4. History from the internet

Research downloads the exchanges' public candle data (Binance first; Bybit, Gate as backups) into
`data/market-history.db`: **5 years of hourly** and **~8 years of daily** candles per coin (or since the coin was
listed), plus ~3.5 days of 1-minute candles. The newest candles come first (forecasts work within a minute); the
older years are fetched in the background — ~90 seconds for all five coins on 2026-10-15. Kept current
automatically. The forecast models still train on their newest windows (5,000 hours / 1,500 days), retrained as
new candles arrive; the technique study and backtests use the full history.

## 5. Choosing the model (`.env`)

| `LLM_PROVIDER` | Needs | Notes |
|---|---|---|
| `gemini` (default) | `GEMINI_API_KEY` — free at https://aistudio.google.com/apikey | free tier: fine for one person; slower and less precise |
| `claude` | `COPILOT_CLAUDE_API_KEY` — https://console.anthropic.com (billed per use) | `COPILOT_CLAUDE_MODEL=claude-opus-5-5`, `COPILOT_CLAUDE_EFFORT=low`; refusal fallback on (`fallbacks: "default"`) |
| `off` | — | the quick analysis still works |

Each person uses their **own** key, typed into their own `.env`; keys are never committed or shared. The settings
have their own names (`COPILOT_CLAUDE_*`) because tools on a developer machine already set `ANTHROPIC_*` /
`CLAUDE_*` in the environment (found 2026-10-15: `CLAUDE_EFFORT=xhigh`, `ANTHROPIC_BASE_URL`), and the gateway
must not pick those up; the Claude client also pins `https://api.anthropic.com`.

## 6. Auto (sandbox only)

With **Auto** ticked, a proposed **order or algorithm** up to `COPILOT_AUTO_MAX_USDT` ($50) **with no warnings**
runs without the click — through the same confirm path (fresh price check, risk, kill switch), audited with
`auto: true`. **Never** with real money (the switch is disabled and the server ignores it), **never** for bots
(they move money into the fund), never above the cap, never with a warning.

## 7. Safety, in one list

- The model fills a form; code validates every field and builds the exact action. It never holds keys, never
  submits, never sizes risk.
- Your message is data, not instructions (prompt-injection tests: a fully obedient model still can't skip the
  confirm or the risk check).
- Answers: numbers only from the facts; the verdict is never upgraded ("never upgrade it" is in the instructions).
- Every proposal, answer, confirm and refusal is audited with your original words.
- Real money: Auto off, bots refused unless `MAINNET_CYCLE_BOTS=true`, and the `.env` caps apply to everything.
- **Which AI on real money** (decision A5, 2026-10-23): **Claude**, never the free Gemini tier (it may use what it is
  sent - there, real balances and orders). The real-money server is set up with `LLM_PROVIDER=claude` but stays off
  until a key is typed with `python deploy/real-server/deploy.py ai-key` (hidden prompt → the server's root-only
  `keys.env`; Anthropic is asked with one free call and a refused key is removed again; `--remove` turns it off).
  Without a key the copilot says so and its quick analysis still works. The sandbox (laptop) keeps Gemini.

## 8. API

| Endpoint | What |
|---|---|
| `GET /api/copilot/status` | model, environment, Auto allowed, analysis available |
| `POST /api/copilot/propose` `{text, auto}` | ANSWERED / PROPOSED / CLARIFY / REFUSED (or CONFIRMED when Auto ran) |
| `POST /api/copilot/confirm` / `cancel` `{proposal_id}` | run or drop your own proposal |
| `GET /api/copilot/analysis?symbol=BTC/USDT` | the analyst's verdict and evidence, no model needed |
| `GET /api/research/techniques?symbol=…&timeframe=1h\|1d` | the technique study |

## 9. Panel Q&A

- *"Is it an AI that predicts prices?"* No. Our own statistical models and backtests measure the evidence on
  years of real data; the language model only reads your request and words the result. It says when there is no
  edge — which is most of the time.
- *"Did you train the AI on market data?"* We train our forecast models on market history (walk-forward tested,
  retrained as data arrives). The language model isn't trained on our data and doesn't need to be: it reads the
  live results every time you ask.
- *"Can it trade by itself?"* In the sandbox, small orders with Auto on; with real money, never — every action
  waits for a person.
- *"Why does it so often say no trade?"* Because on real data, after fees, most signals don't beat simply holding.
  A tool that always says "buy" would be the dishonest one.
