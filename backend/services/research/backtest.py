"""Backtester — test a trading rule on past candles, with fees and slippage. Pure numpy.

    the rule decides at the CLOSE of candle t (using data up to t only)
    the trade happens at the OPEN of candle t+1, paying fee + slippage on the notional
    long only (spot: you can own a coin or not; you can't sell what you don't have)
    equity moves with the open-to-open return while in the market

Rules (each a classic, with the trader who made it famous in the guide):
    buy_hold                          the benchmark everything is compared with
    sma_cross   (fast, slow)          in while SMA(fast) > SMA(slow)                     trend following
    rsi_reversion (period, buy_below, sell_above)  buy when RSI < buy_below, sell when > sell_above  mean reversion
    bollinger   (period, k, mode)     reversion: buy below the lower band, sell at the middle;
                                      breakout: buy above the upper band, sell below the middle
    macd_cross                        in while MACD > its signal line                      momentum
    momentum    (lookback)            in while the close is above the close `lookback` bars ago
                                      (time-series momentum, Moskowitz, Ooi & Pedersen 2012)
    donchian_breakout (period, exit_period)  buy a close above the highest close of the last `period`
                                      bars, sell a close below the lowest of the last `exit_period`
                                      (the Turtle Traders' channel breakout)                breakout
    model       (threshold)           in while the walk-forward forecast says P(up) > threshold
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .indicators import bollinger, macd, max_drawdown, rsi, sma

STRATEGIES = ("buy_hold", "sma_cross", "rsi_reversion", "bollinger", "macd_cross", "momentum", "donchian_breakout",
              "model")
BARS_PER_YEAR = {"1m": 525_600, "1h": 8_760, "1d": 365}


def positions(strategy: str, params: dict, close, probs=None) -> np.ndarray:
    """Desired position (1 = hold the coin, 0 = hold USDT) decided at each close."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    pos = np.zeros(n)
    if strategy == "buy_hold":
        pos[:] = 1.0
    elif strategy == "sma_cross":
        fast, slow = sma(c, int(params.get("fast", 20))), sma(c, int(params.get("slow", 50)))
        pos = np.where(~np.isnan(slow) & (fast > slow), 1.0, 0.0)
    elif strategy == "macd_cross":
        line, sig, _ = macd(c)
        pos = np.where(~np.isnan(sig) & (line > sig), 1.0, 0.0)
    elif strategy == "rsi_reversion":
        r = rsi(c, int(params.get("period", 14)))
        buy, sell = float(params.get("buy_below", 30)), float(params.get("sell_above", 70))
        state = 0.0
        for i in range(n):
            if not np.isnan(r[i]):
                if state == 0 and r[i] < buy:
                    state = 1.0
                elif state == 1 and r[i] > sell:
                    state = 0.0
            pos[i] = state
    elif strategy == "bollinger":
        mid, upper, lower, _ = bollinger(c, int(params.get("period", 20)), float(params.get("k", 2.0)))
        breakout = params.get("mode", "reversion") == "breakout"
        state = 0.0
        for i in range(n):
            if not np.isnan(mid[i]):
                if breakout:
                    state = 1.0 if c[i] > upper[i] else 0.0 if c[i] < mid[i] else state
                else:
                    state = 1.0 if c[i] < lower[i] else 0.0 if c[i] > mid[i] else state
            pos[i] = state
    elif strategy == "momentum":
        lb = int(params.get("lookback", 24))
        if 0 < lb < n:
            pos[lb:] = np.where(c[lb:] > c[:-lb], 1.0, 0.0)
    elif strategy == "donchian_breakout":
        n_in = int(params.get("period", 20))
        n_out = int(params.get("exit_period", max(2, n_in // 2)))
        hi, lo = np.full(n, np.nan), np.full(n, np.nan)
        if n > n_in:  # hi[i] = highest close of the n_in bars BEFORE i (the channel the close breaks out of)
            hi[n_in:] = sliding_window_view(c, n_in)[: n - n_in].max(axis=1)
        if n > n_out:
            lo[n_out:] = sliding_window_view(c, n_out)[: n - n_out].min(axis=1)
        state = 0.0
        for i in range(n):
            if not np.isnan(hi[i]):
                if state == 0 and c[i] > hi[i]:
                    state = 1.0
                elif state == 1 and not np.isnan(lo[i]) and c[i] < lo[i]:
                    state = 0.0
            pos[i] = state
    elif strategy == "model":
        if probs is None:
            raise ValueError("the model rule needs forecast probabilities")
        p = np.asarray(probs, dtype=float)
        pos = np.where(~np.isnan(p) & (p > float(params.get("threshold", 0.55))), 1.0, 0.0)
    else:
        raise ValueError(f"unknown strategy {strategy!r}; one of {', '.join(STRATEGIES)}")
    return pos


@dataclass
class BacktestResult:
    bars: int
    start_ts: int
    end_ts: int
    total_return: float
    buy_hold_return: float
    cagr: float | None
    max_drawdown: float
    buy_hold_max_drawdown: float
    sharpe: float | None
    exposure: float  # share of time in the market
    trades: int
    win_rate: float | None
    avg_trade_return: float | None
    fees_paid: float
    final_equity: float
    equity_curve: list[dict] = field(default_factory=list)  # [{ts, equity, buy_hold}] (<= 400 points)
    trade_list: list[dict] = field(default_factory=list)  # last <= 100 trades


def run_backtest(ts_ms, open_, close, desired, fee_bps: float = 10.0, slippage_bps: float = 2.0,
                 capital: float = 1000.0, timeframe: str = "1h", start: int = 0) -> BacktestResult:
    """Simulate ``desired`` positions from candle ``start`` on (see the module docstring)."""
    ts, o = np.asarray(ts_ms), np.asarray(open_, dtype=float)
    d = np.asarray(desired, dtype=float)
    n = len(o)
    cost = (fee_bps + slippage_bps) / 1e4
    equity, held = capital, 0.0
    curve = [capital]
    trades, fees, in_market = [], 0.0, 0
    entry_price = entry_ts = None
    interval_returns = []
    for k in range(max(start, 1), n - 1):
        want = d[k - 1]  # decided at the previous close, executed at this open
        before = equity
        if want != held:
            fee = equity * cost
            equity -= fee
            fees += fee
            if want == 1:
                entry_price, entry_ts = o[k], int(ts[k])
            elif entry_price is not None:
                gross = o[k] / entry_price
                trades.append({"entry_ts": entry_ts, "exit_ts": int(ts[k]), "entry": float(entry_price),
                               "exit": float(o[k]), "return": float(gross * (1 - cost) / (1 + cost) - 1)})
                entry_price = None
            held = want
        r = o[k + 1] / o[k] - 1 if held else 0.0
        equity *= 1 + r
        interval_returns.append(equity / before - 1)  # includes the fee paid in this step (Sharpe after costs)
        in_market += int(held == 1)
        curve.append(equity)
    if entry_price is not None:  # still in at the end: mark it at the last open, as if sold there
        trades.append({"entry_ts": entry_ts, "exit_ts": int(ts[n - 1]), "entry": float(entry_price),
                       "exit": float(o[n - 1]), "return": float(o[n - 1] / entry_price * (1 - cost) / (1 + cost) - 1),
                       "open": True})
    first = max(start, 1)
    steps = max(len(interval_returns), 1)
    bh_curve = capital * o[first:n] / o[first] * (1 - cost)
    rets = np.asarray(interval_returns)
    per_year = BARS_PER_YEAR.get(timeframe, 8_760)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(per_year)) if len(rets) > 1 and rets.std() > 0 else None
    years = steps / per_year
    cagr = float((equity / capital) ** (1 / years) - 1) if years >= 30 / 365 and equity > 0 else None
    wins = [t["return"] for t in trades]
    stride = max(1, len(curve) // 400)
    pts = [{"ts": int(ts[first + i]), "equity": round(float(curve[i]), 4),
            "buy_hold": round(float(bh_curve[i]), 4) if i < len(bh_curve) else None}
           for i in range(0, len(curve), stride) if first + i < n]
    return BacktestResult(
        bars=steps, start_ts=int(ts[first]), end_ts=int(ts[n - 1]),
        total_return=round(equity / capital - 1, 6), buy_hold_return=round(float(bh_curve[-1] / capital - 1), 6),
        cagr=round(cagr, 6) if cagr is not None else None,
        max_drawdown=round(max_drawdown(curve), 6), buy_hold_max_drawdown=round(max_drawdown(bh_curve), 6),
        sharpe=round(sharpe, 4) if sharpe is not None else None,
        exposure=round(in_market / steps, 4), trades=len(trades),
        win_rate=round(sum(1 for w in wins if w > 0) / len(wins), 4) if wins else None,
        avg_trade_return=round(float(np.mean(wins)), 6) if wins else None,
        fees_paid=round(fees, 4), final_equity=round(equity, 4), equity_curve=pts, trade_list=trades[-100:],
    )
