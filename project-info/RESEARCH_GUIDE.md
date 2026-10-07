# Research & Forecasts Guide — what the prediction tools do, how, and how well

> Current as of 2026-10-08 (Phase B). Code: `backend/services/research/`; page: **Research & Forecasts**;
> API: `/api/research/*` (Swagger). Every formula is a pure function with its own unit tests (`backend/tests/test_research/`).

**One sentence for the panel:** the tools forecast the probability that the next minute, hour or day closes higher, give a likely price range, and **show their own track record on data they had not seen** — and on real crypto data most forecasts honestly say "no proven edge".

---

## 1. The data

* **One reference market per coin:** Binance's real (mainnet) market, the deepest; Bybit, then Gate, if Binance can't be reached. Charts, backtests and forecasts all read the same series.
* **Kept on this machine** in `data/market-history.db` (gitignored): 5,000 one-minute candles (~3.5 days), 5,000 one-hour (~7 months), 1,500 one-day (~4 years), for BTC, ETH, SOL, BNB, XRP — about 85,000 candles. Only **closed** candles are stored. Downloaded at the first start (seconds), then kept current (1m every 20 s, 1h every minute, 1d every 10 min).
* The execution algorithms use a different view of the market (all exchanges' 1-minute volume summed — `ALGORITHMS_GUIDE.md`).

## 2. Indicators — the traders' toolkit

| Indicator | What it says | Who made it standard |
|---|---|---|
| SMA 20 / 50 | the trend: price above a rising average is an uptrend | classic trend following ("the trend is your friend") |
| RSI (14) | momentum: > 70 overbought, < 30 oversold | J. Welles Wilder (1978) |
| MACD (12, 26, 9) | trend acceleration: MACD line vs its signal line | Gerald Appel |
| Bollinger bands (20, 2σ) | how stretched the price is from its mean | John Bollinger |
| ATR (14) | how far a candle typically moves — sets stops | Wilder |
| Volume z-score | is volume unusual? volume should confirm a move | Dow theory |
| Order-book imbalance, microprice | who is bigger near the price right now | market-microstructure research |

The page turns each into one plain sentence ("RSI 29: oversold — bounce potential"). **They explain the picture; they don't make the forecast** — the probability comes from the tested model.

## 3. The forecast

**What:** P(the next candle closes higher) — next minute, next hour, next day.

**Features** (at candle *t*, using only candles up to *t* — tested): returns over 1/3/6/12/24 candles, RSI, MACD histogram, Bollinger %B, distance from SMA 20 and 50, recent volatility and its ratio to normal, volume z-score and volume × last move, ATR, and the time of day (or day of week).

**Model:** logistic regression with an L2 (ridge) penalty, fitted by Newton/IRLS in numpy. The penalty is **chosen inside each training window** (fit on its first 75 %, judge on the last 25 %): a strong penalty keeps probabilities near 50 % unless the evidence is strong, and choosing it on training data only keeps the test honest.

**Testing — walk-forward:** train on the first 60 % of the history, predict the next block, add it to the training data, predict the next … (8 blocks). Every scored forecast was made without seeing its outcome.

**Scoring:**
* **accuracy** vs **the baseline** — always guessing whichever side was more common (not 50 %: markets often rise more often than they fall);
* **Brier score** and **log loss** vs simply quoting the base rate (is the *probability* good, not just the direction?);
* **p-value** — how likely the accuracy gain over the baseline is by pure luck (one-sided binomial test).

**"Edge"** only if it beats the baseline on accuracy **and** log loss **and** p < 0.05. With 15 models (5 coins × 3 horizons), about one false "edge" is expected by luck — so the page also keeps a **live record**.

**The range:** volatility from an EWMA (RiskMetrics, λ = 0.94); the next move measured in units of that volatility; its **empirical** 10/25/75/90 % quantiles (fat tails included, no bell-curve assumption) → "80 % of the time the close lands between X and Y".

## 4. Results on real data (2026-10-08)

| | Next minute | Next hour | Next day |
|---|---|---|---|
| Direction: proven edge? | none of 5 | **SOL only** (52.5 % vs 50.0 %, p = 0.015) | none of 5 |
| 80 % range held | 79–81 % | 79–81 % | 76–83 % |

* **The ranges are reliable** — that part of the forecast can be trusted for planning (stops, sizing, risk).
* **The direction mostly isn't predictable** with these features — the honest, expected result for liquid markets; the tool says "treat as 50/50". SOL next-hour's small edge may still be luck (see above); the live record will tell.
* **Live record:** every time a candle closes, the service forecasts the next one, writes it down (`forecasts` table), and scores it when that candle closes. Never edited afterwards.

## 5. Backtester

Tests a rule on past candles: decides at each close, trades at the **next open**, pays **fee + slippage** on every trade, long only (spot). Rules: buy & hold, moving-average cross, RSI reversion, Bollinger (reversion / breakout), MACD cross, and the forecast model (out-of-sample forecasts only). Reports return vs buy & hold, max drawdown, Sharpe (after costs), trades, win rate, fees, the equity curve.

**On 7 months of BTC hours (2026-10-08):** buy & hold +15 %; MA cross −0.8 % (55 trades, $126 fees on $1,000); RSI reversion +6.3 %; Bollinger reversion −24 %; MACD cross −18 % ($368 fees). The lesson for the panel: **fees decide** — frequent trading on weak signals loses.

## 6. Trade planner

For a long trade: **stop** k × ATR below the entry (the live ask), **target** RR × the risk above it, **size** from the risk you accept (fixed fractional: equity × risk % ÷ distance to the stop), capped by half-Kelly and by the money available. Then it **replays this exact bracket at every past candle** — which was hit first, stop or target (both in one candle counts as a loss) — after fees, and compares the win rate with the **break-even win rate** `(1 + c) / (RR + 1)` (c = round-trip costs in R). Below break-even → "skip".

**BTC, 1-hour, stop 1.5 ATR, 2 : 1 (2026-10-08):** won 31.6 % of 4,894 past candles, needs 44.6 % after fees → skip. Tight stops on hourly candles are mostly eaten by fees.

## Panel Q&A

* **"Can your system predict prices?"** — It forecasts probabilities and shows how often they were right on data it had not seen. Mostly it can't beat a simple baseline — and it says so on the screen. The ranges it gives are reliable.
* **"Isn't 52 % useless?"** — A small edge can matter at scale, but only after fees; our backtests and trade planner measure exactly that, and usually the answer is no.
* **"How do you avoid overfitting / look-ahead?"** — Features use only past candles (a unit test checks that appending future candles never changes past features); walk-forward testing; the penalty is chosen inside each training window; a significance test; and a live record that can't be edited.
* **"Why not deep learning?"** — More parameters fit noise faster on ~5,000 candles. A transparent model that admits "no edge" is more useful — and defensible — than an opaque one that claims one.

## Honest limits

Candle data only (no news, no on-chain data, no order-book history); one reference exchange; 5,000 candles per horizon; the live record starts when this machine starts logging; past results don't predict future ones. **Not financial advice.**
