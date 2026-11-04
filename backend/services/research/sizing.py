"""Trade plan — where to stop, where to take profit, how much to buy, and whether it's worth it. Pure.

The principles the big trading books agree on, turned into numbers measured on OUR candles:

    stop and target from volatility (Wilder's ATR):  stop = entry - k x ATR,  target = entry + RR x k x ATR
    position size from the risk you accept (fixed fractional, Van Tharp):
                                                     quantity = equity x risk% / (entry - stop)
    is the bracket worth it? (expectancy):           replay this exact bracket at every past candle —
                                                     which was hit first, stop or target? — with fees
    break-even win rate:                             p* = (1 + c) / (RR + 1)   (c = round-trip costs in R)
    how much at most (Kelly, Thorp):                 f* = p - (1 - p) / b,  b = (RR - c) / (1 + c);
                                                     we suggest half of it, never more than the fixed risk

If the bracket's historical win rate is not above its break-even, the verdict is "no edge — skip".
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class BracketStats:
    samples: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float | None  # wins / (wins + losses)
    expectancy_r: float | None  # average result per trade in R (risk units), after costs
    break_even_win_rate: float
    kelly_fraction: float | None


def bracket_stats(high, low, close, atr_series, k_atr: float, rr: float, max_bars: int,
                  cost_bps_round_trip: float, start: int = 0) -> BracketStats:
    """Replay a long bracket (stop k*ATR below, target rr*k*ATR above) from every past candle's close.

    A candle that touches both stop and target counts as a loss (we can't know the order — be
    conservative). Neither within ``max_bars`` = a timeout, closed at the last close.
    """
    h, lo, c, a = (np.asarray(x, dtype=float) for x in (high, low, close, atr_series))
    n = len(c)
    wins = losses = timeouts = 0
    results = []
    for t in range(start, n - 1):
        if np.isnan(a[t]) or a[t] <= 0:
            continue
        entry = c[t]
        risk = k_atr * a[t]
        stop, target = entry - risk, entry + rr * risk
        cost_r = entry * cost_bps_round_trip / 1e4 / risk
        end = min(t + max_bars, n - 1)
        outcome = None
        for j in range(t + 1, end + 1):
            if lo[j] <= stop:
                outcome = -1.0
                break
            if h[j] >= target:
                outcome = rr
                break
        if outcome is None:
            if end - t < max_bars:  # ran out of history: not a real sample
                continue
            outcome = (c[end] - entry) / risk
            timeouts += 1
        elif outcome > 0:
            wins += 1
        else:
            losses += 1
        results.append(outcome - cost_r)
    samples = len(results)
    if samples == 0:
        return BracketStats(0, 0, 0, 0, None, None, round((1 + 0.0) / (rr + 1), 4), None)
    # costs in R vary with the ATR; use the average for the break-even line
    entry_risk_ratio = []
    for t in range(start, n - 1):
        if not np.isnan(a[t]) and a[t] > 0:
            entry_risk_ratio.append(c[t] * cost_bps_round_trip / 1e4 / (k_atr * a[t]))
    mean_cost_r = float(np.mean(entry_risk_ratio)) if entry_risk_ratio else 0.0
    decided = wins + losses
    p = wins / decided if decided else None
    b = (rr - mean_cost_r) / (1 + mean_cost_r)
    kelly = (p - (1 - p) / b) if p is not None and b > 0 else None
    return BracketStats(
        samples=samples, wins=wins, losses=losses, timeouts=timeouts,
        win_rate=round(p, 4) if p is not None else None,
        expectancy_r=round(float(np.mean(results)), 4),
        break_even_win_rate=round((1 + mean_cost_r) / (rr + 1), 4),
        kelly_fraction=round(kelly, 4) if kelly is not None else None,
    )


@dataclass
class TradePlan:
    entry: float
    stop: float
    target: float
    quantity: float
    notional: float
    risk_amount: float  # what hitting the stop would lose, before fees
    reward_amount: float
    fees_round_trip: float
    verdict: str  # "positive" (historical edge) or "skip"
    reasons: list[str]


def plan_trade(entry: float, atr_now: float, equity: float, risk_pct: float, k_atr: float, rr: float,
               cost_bps_round_trip: float, stats: BracketStats, max_notional: float | None = None,
               stop: float | None = None) -> TradePlan:
    """Size a long trade by fixed-fractional risk, capped by half-Kelly and by the money available.

    ``stop`` given = a stop chosen elsewhere (the trade desk's opening-range midpoint) instead of k_atr x ATR.
    """
    risk_per_unit = (entry - stop) if stop is not None else k_atr * atr_now
    stop, target = entry - risk_per_unit, entry + rr * risk_per_unit
    risk_amount = equity * risk_pct / 100
    qty = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
    where = (f"Stop {risk_per_unit:,.6g} below the entry" if atr_now <= 0 or abs(risk_per_unit - k_atr * atr_now) > 1e-12
             else f"Stop {k_atr:g} × ATR below the entry")
    reasons = [f"{where}, target {rr:g} × the risk above it (reward : risk {rr:g} : 1)",
               f"Risking {risk_pct:g}% of {equity:,.2f} = {risk_amount:,.2f} if the stop is hit"]
    caps = []
    if stats.kelly_fraction is not None and stats.kelly_fraction > 0:
        half_kelly_risk = equity * stats.kelly_fraction / 2
        if half_kelly_risk < risk_amount:
            qty = half_kelly_risk / risk_per_unit
            caps.append(f"half-Kelly ({stats.kelly_fraction / 2 * 100:.2f}% risk)")
    if qty * entry > equity:
        qty = equity / entry
        caps.append("the money available (no leverage on spot)")
    if max_notional is not None and qty * entry > max_notional:
        qty = max_notional / entry
        caps.append(f"the per-order limit ({max_notional:,.2f})")
    if caps:
        reasons.append("Size capped by " + " and ".join(caps))
    edge = stats.win_rate is not None and stats.win_rate > stats.break_even_win_rate and (stats.expectancy_r or 0) > 0
    if stats.samples == 0:
        reasons.append("Not enough history to test this bracket")
    else:
        reasons.append(
            f"This exact bracket, replayed at {stats.samples} past candles: won {stats.wins}, lost {stats.losses}, "
            f"timed out {stats.timeouts} — win rate {stats.win_rate * 100 if stats.win_rate is not None else 0:.1f}% "
            f"vs {stats.break_even_win_rate * 100:.1f}% needed to break even after fees; "
            f"average {stats.expectancy_r:+.3f} R per trade")
    reasons.append("Historically positive — but past results don't guarantee future ones" if edge
                   else "No edge after fees on this history — skip, or change the bracket")
    return TradePlan(
        entry=entry, stop=stop, target=target, quantity=qty, notional=qty * entry,
        risk_amount=qty * risk_per_unit, reward_amount=qty * rr * risk_per_unit,
        fees_round_trip=qty * entry * cost_bps_round_trip / 1e4,
        verdict="positive" if edge else "skip", reasons=reasons,
    )
