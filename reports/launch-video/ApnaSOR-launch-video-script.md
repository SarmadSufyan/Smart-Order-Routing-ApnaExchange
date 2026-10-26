# ApnaSOR launch video — narration script

Length 7:45 · 1920×1080 · voice: en-US-AndrewNeural (edge-tts) · subtitles burned in (also ApnaSOR-launch-video.srt)

Every claim follows README.md and the guides in project-info/ (sandbox = the exchanges' own test systems with play money; the arbitrage module detects only; forecasts are probabilities with a track record).

## 0:00  00_open

ApnaSOR. A smart order router for crypto exchanges, built by group CS14 at Usman Institute of Technology.

## 0:10  01_who

Who is it for? People who place crypto orders and care about the price they get: a trader working an order, the risk manager who must be sure nothing goes wrong, and the administrator who runs the desk. ApnaSOR is the order router of Apna Exchange: its users' orders go to the best real exchange behind it. The problem: the same coin trades on Binance, Bybit, OKX and HTX at once, each with its own price, its own fee and its own order book, and a person with four tabs open is guessing.

## 0:42  02_example

An example. A trader wants to buy one Bitcoin. Binance asks eighty thousand seven hundred dollars, Bybit six hundred ninety, OKX six hundred eighty-five, Gate six hundred ninety-five. OKX looks cheapest. But each exchange charges a taker fee: a tenth of a percent on Binance and Bybit, more on OKX, double on Gate. After fees, Bybit is cheapest, and the one that looked cheapest is third, about thirty-five dollars apart on one Bitcoin. ApnaSOR does this on every order, on live books, in milliseconds, and takes the rest from the next cheapest level anywhere when one exchange runs thin. And before anything is sent, every order passes seven risk checks and a funding check, so a fat finger never reaches an exchange.

## 1:32  03_what

In one system: live order books from the real exchanges; the best price after fees, with a proof for every decision; seven risk checks and a kill switch; algorithms that work large orders over time; research and a copilot that proposes while a person confirms; a wallet reconciled against the exchanges; an audit log of everything. All of it runs against the exchanges' own test systems with play money, and the same code runs on a real-money server behind extra rails. Let's see it.

## 2:03  04_login_dashboard

This is the trader's screen. Log in; every login is written to the audit log. The Dashboard: four exchanges connected, a local copy of each order book kept in sync from its live feed, and on the right the best bid and the best ask across all of them for twenty coins, with the exchange that holds each price. On the left, every order the router has sent.

## 2:28  05_order

A real order, in the sandbox: buy a small amount of Bitcoin, routed by best price after fees. Submit. Seven risk checks, a funding check, then the router walks every book cheapest level first and sends a price-protected immediate-or-cancel child order. Filled. And here is the proof: every exchange ranked by all-in cost, price plus fee; the levels walked; the cap no child may cross; expected versus actual; and what it saved against the worst exchange. Every order keeps this proof.

## 3:02  06_refused

An order that breaks a rule: a fifth of a Bitcoin, about sixteen thousand dollars, above this account's per-order limit. Submit. Refused by pre-trade risk, with the check that failed, and nothing reached an exchange.

## 3:18  07_sweep

A large order should not hit one exchange. The liquidity sweep sends to every exchange that can fund it, at the same moment, each part sized by that exchange's depth, all under one price cap. A hundredth of a Bitcoin. Submit. The checks run, the children leave together, each capped at the same price. One parent order, two exchanges, and the proof shows the split and each exchange's fill.

## 3:45  08_algos

Large orders can be worked over time with five execution algorithms: TWAP, VWAP, implementation shortfall, percentage of volume, and iceberg. Or Auto reads the live books and this order, picks one, and explains why; nothing is sent. Every slice is pre-flight checked against risk. Let's start a TWAP: six slices over one minute, each one through the same order path. We'll come back for the result.

## 4:17  09_research

Research runs on the real market's candles: five years of hourly and eight years of daily history per coin. It forecasts the chance that the next hour closes higher, with a likely range and its own track record on data it had not seen — and mostly it says no proven edge, because that is the honest answer on crypto data. A backtester and a study of classic techniques pay fees on every trade. Nothing here is advice, and nothing here places an order.

## 4:47  10_copilot

The copilot takes plain English. Buy twenty dollars of SOL with smart routing. A language model only fills a form; the code validates every field, gathers the live facts and builds the proposal. The model never holds keys, never submits, never sizes risk. Here it is. It waits for a person. Confirm, and the order takes the same path as the button, risk checks included.

## 5:15  11_twap_wallet

The TWAP is done: six slices, each with its own fill, and the chart against the arrival price and the real market's volume-weighted price over the same minute, the two benchmarks every algorithm reports. The Wallet reads real balances from every exchange, keeps a P and L ledger from every fill, and reconciles it against what each exchange reports. A mismatch is shown, never edited away.

## 5:44  12_safety

Safety. The kill switch stops every new order, algorithm and bot at once. Engage it, with a reason. The order button locks, and the first of the seven checks refuses anything else. The switch survives restarts, and is replicated by a three-node RAFT cluster with signed messages: engaging works without a majority, releasing needs one. Let's kill the leader process. A new leader within seconds, nothing lost. Two of three is a majority, so we release, and trading resumes. The limits, a loss limit that engages the switch by itself, and concentration alerts live on the Risk Manager.

## 6:28  13_more

Also in the box: bots that trade from a fenced automation fund, with a practice lab that measures every one of their trades; an arbitrage detector that watches every exchange pair after fees and, by design, cannot trade; venue health; and the audit log, the append-only record of every step with who did it.

## 6:50  14_real_money

Everything so far ran in the exchanges' sandboxes, with play money. The same code runs on a separate real-money server with a fixed IP, reached only through an SSH tunnel. An exchange gets orders only after it confirms that our key can trade but cannot withdraw. Hard caps live in the server's settings where no screen can loosen them, going live is typed, alerts reach a private Discord channel, and a stop from the phone engages the kill switch; nothing releases it from the phone.

## 7:21  15_close

The contribution is the integration: real exchanges, fee-aware routing, deterministic risk, algorithms, research, a copilot a person confirms, and an audit trail, one system, verified live. ApnaSOR, group CS14, Usman Institute of Technology, supervised by Sir Shamaail Jafri.
