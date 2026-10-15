"""Technique study — the classic trading rules side by side on one coin's history. Pure numpy.

For every technique: its backtest over the whole stored history and over the recent period, both after
fees and slippage, compared with simply holding the coin over the same span; and what the rule says
NOW (in the market or out, and whether that just changed).

    worked        beat holding on risk-adjusted return (Sharpe after costs) over the whole history
                  AND in the recent period, and made money in the recent period
    mixed         one of the two, not both
    didn't work   neither
    too few trades  fewer than 3 trades: no basis for a verdict

The rules have fixed textbook parameters (nothing is fitted to this coin), so the whole history is
out-of-sample for each of them. Picking the best of several rules by their past still flatters the
winner (multiple testing), so "worked" is evidence, not proof — the study says so in its output.
"""

from dataclasses import dataclass

import numpy as np

from . import backtest as bt

WARMUP = 200  # bars before any rule may trade: the longest indicator needs this much history
# The recent period: about the last three months of hourly data, the last year of daily data.
RECENT_BARS = {"1m": 1_440, "1h": 2_160, "1d": 365}
MIN_TRADES = 3


@dataclass(frozen=True)
class Technique:
    key: str
    strategy: str  # a rule of backtest.STRATEGIES
    family: str
    name: str
    idea: str
    params: dict  # timeframe -> parameters

    def params_for(self, tf: str) -> dict:
        return dict(self.params.get(tf) or self.params.get("1h") or {})


TECHNIQUES: tuple[Technique, ...] = (
    Technique("trend", "sma_cross", "trend following", "Moving-average trend",
              "Hold while the 20-bar average is above the 50-bar average",
              {"1m": {"fast": 20, "slow": 50}, "1h": {"fast": 20, "slow": 50}, "1d": {"fast": 20, "slow": 50}}),
    Technique("momentum", "momentum", "momentum", "Time-series momentum",
              "Hold while the price is above where it was one lookback ago",
              {"1m": {"lookback": 60}, "1h": {"lookback": 168}, "1d": {"lookback": 30}}),
    Technique("macd", "macd_cross", "momentum", "MACD crossover",
              "Hold while the MACD line is above its signal line", {"1h": {}}),
    Technique("breakout", "donchian_breakout", "breakout", "Channel breakout (Turtle)",
              "Buy a close above the recent high, sell a close below the shorter recent low",
              {"1m": {"period": 60, "exit_period": 30}, "1h": {"period": 48, "exit_period": 24},
               "1d": {"period": 20, "exit_period": 10}}),
    Technique("rsi", "rsi_reversion", "mean reversion", "RSI reversion",
              "Buy when RSI drops under 30, sell when it rises over 70",
              {"1h": {"period": 14, "buy_below": 30, "sell_above": 70}}),
    Technique("bollinger", "bollinger", "mean reversion", "Bollinger reversion",
              "Buy below the lower band, sell back at the middle band",
              {"1h": {"period": 20, "k": 2.0, "mode": "reversion"}}),
)

NOTE = ("Each rule uses fixed textbook settings, after fees and slippage, long only (spot). Choosing the best of "
        "several rules by their past flatters the winner, so treat 'worked' as evidence, not proof — and past "
        "results don't predict future ones.")


def _stats(r: bt.BacktestResult) -> dict:
    return {"total_return": r.total_return, "buy_hold_return": r.buy_hold_return, "sharpe": r.sharpe,
            "max_drawdown": r.max_drawdown, "trades": r.trades, "win_rate": r.win_rate, "exposure": r.exposure,
            "start_ts": r.start_ts, "end_ts": r.end_ts}


def _sharpe_beats(a: bt.BacktestResult, b: bt.BacktestResult) -> bool:
    return a.sharpe is not None and (b.sharpe is None or a.sharpe > b.sharpe)


def judge(full: bt.BacktestResult, recent: bt.BacktestResult, hold_full: bt.BacktestResult,
          hold_recent: bt.BacktestResult) -> tuple[str, str]:
    """(verdict, why in plain words) for one technique against holding the coin."""
    if full.trades < MIN_TRADES:
        return "too few trades", f"only {full.trades} trade(s) in the whole history: no basis for a verdict"
    beat_full, beat_recent = _sharpe_beats(full, hold_full), _sharpe_beats(recent, hold_recent)
    made_money = recent.total_return > 0
    parts = [("beat" if beat_full else "did not beat") + " holding over the whole history",
             ("beat" if beat_recent else "did not beat") + " it recently",
             ("made" if made_money else "lost") + f" {abs(recent.total_return) * 100:.1f}% recently"]
    if beat_full and beat_recent and made_money:
        return "worked", "; ".join(parts) + " (after costs)"
    if beat_full or (beat_recent and made_money):
        return "mixed", "; ".join(parts) + " (after costs)"
    return "didn't work", "; ".join(parts) + " (after costs)"


def study(ts, open_, close, tf: str, fee_bps: float, slippage_bps: float, capital: float = 1000.0) -> dict:
    """Every technique on this history: whole period, recent period, verdict, and what it says now."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    if n < WARMUP + 100:
        raise ValueError(f"need at least {WARMUP + 100} candles, have {n}")
    recent_start = max(WARMUP, n - RECENT_BARS.get(tf, 2_160))

    def run(desired, start):
        return bt.run_backtest(ts, open_, close, desired, fee_bps, slippage_bps, capital, tf, start)

    hold = np.ones(n)
    hold_full, hold_recent = run(hold, WARMUP), run(hold, recent_start)
    rows = []
    for t in TECHNIQUES:
        params = t.params_for(tf)
        pos = bt.positions(t.strategy, params, c)
        full, recent = run(pos, WARMUP), run(pos, recent_start)
        verdict, why = judge(full, recent, hold_full, hold_recent)
        rows.append({
            "key": t.key, "name": t.name, "family": t.family, "idea": t.idea, "strategy": t.strategy,
            "params": params, "signal": "in" if pos[-1] == 1 else "out",
            "fresh": bool(n > 1 and pos[-1] != pos[-2]), "verdict": verdict, "why": why,
            "full": _stats(full), "recent": _stats(recent),
        })
    return {
        "bars": n, "from_ts": int(ts[WARMUP]), "to_ts": int(ts[-1]), "recent_from_ts": int(ts[recent_start]),
        "hold": {"full": _stats(hold_full), "recent": _stats(hold_recent)},
        "techniques": rows,
        "worked_in": [r["key"] for r in rows if r["verdict"] == "worked" and r["signal"] == "in"],
        "worked_out": [r["key"] for r in rows if r["verdict"] == "worked" and r["signal"] == "out"],
        "note": NOTE,
    }
