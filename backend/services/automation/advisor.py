"""Advisor agent: when to propose a trade, and the reasons it gives — as a pure function.

For each coin it reads (all from ApnaSOR itself, nothing from the internet):
    * the research forecast for the next hour (P(up), and whether that model has a proven edge),
    * the trade planner's ATR bracket (stop, target) and how that exact bracket did on past
      hourly candles after costs (win rate vs break-even, average result in R),
    * the practice lab: how the advisor's own earlier trades did in similar conditions,
      and the trading costs measured on our own orders.

It proposes a BUY (spot, long only) when P(up) >= ``min_probability``, the bracket's past
average after costs is above ``min_expectancy_r`` and (optionally) the model has a proven
edge. Every reason is shown with the proposal; nothing is hidden behind a score. The
research found no proven edge for most coins, so most proposals say so — the advisor is
honest about that rather than confident.
"""

from dataclasses import dataclass, field
from typing import Any

from backend.shared.models.automation import AdvisorParams


@dataclass
class CoinVerdict:
    symbol: str
    propose: bool
    why: str  # one line: what it decided and why
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    p_up: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def evaluate_coin(p: AdvisorParams, symbol: str, forecast: dict | None, plan: dict | None, ask: float | None,
                  lab: dict | None = None, costs: dict | None = None) -> CoinVerdict:
    """Propose a buy of ``symbol`` now? ``forecast`` = the research 1h forecast, ``plan`` = its trade plan."""
    v = CoinVerdict(symbol=symbol, propose=False, why="")
    if not forecast or forecast.get("unavailable"):
        v.why = f"no forecast yet ({(forecast or {}).get('unavailable') or 'research still loading'})"
        return v
    if not ask or ask <= 0:
        v.why = "no live price on an exchange we can trade on"
        return v
    p_up, edge = float(forecast["p_up"]), bool(forecast.get("edge"))
    v.p_up = p_up
    v.evidence = {"p_up": p_up, "edge": edge, "ask": ask}
    if p_up < p.min_probability:
        v.why = f"next-hour P(up) {_pct(p_up)} is under {_pct(p.min_probability, 0)}"
        return v
    if p.require_edge and not edge:
        v.why = f"P(up) {_pct(p_up)}, but the next-hour model has no proven edge for {symbol}"
        return v
    if not plan or not plan.get("plan"):
        v.why = "no trade plan (not enough hourly candles yet)"
        return v
    hist = plan.get("history") or {}
    exp_r, samples = hist.get("expectancy_r"), hist.get("samples") or 0
    if exp_r is None or samples < 30:
        v.why = f"too few past brackets to judge this one ({samples} on hourly candles)"
        return v
    if exp_r <= p.min_expectancy_r:
        v.why = (f"P(up) {_pct(p_up)}, but this bracket averaged {exp_r:+.3f} R after costs on {samples} past hours "
                 f"(needs above {p.min_expectancy_r:+g} R)")
        return v

    pl = plan["plan"]
    stop, target = float(pl["stop"]), float(pl["target"])
    # The plan was made at the ask of its moment; keep the same distances from this ask.
    entry0 = float(pl["entry"]) or ask
    stop, target = ask * stop / entry0, ask * target / entry0
    v.entry, v.stop, v.target = ask, stop, target
    v.propose = True
    v.why = f"P(up) {_pct(p_up)} and the bracket averaged {exp_r:+.3f} R after costs on {samples} past hours"
    v.reasons = [
        f"Forecast: {_pct(p_up)} chance {symbol.split('/')[0]} closes the next hour higher — {forecast.get('call', '')}",
        (f"Bracket: stop {stop:,.6g} ({(stop / ask - 1) * 100:+.2f}%), target {target:,.6g} "
         f"({(target / ask - 1) * 100:+.2f}%) = {p.k_atr:g} × hourly ATR, {p.rr:g} R; sold anyway after "
         f"{p.max_hold_hours:g} h"),
        (f"History: on {samples} past hours this bracket won {_pct(hist.get('win_rate'))} of decided trades "
         f"(break-even {_pct(hist.get('break_even_win_rate'))}), average {exp_r:+.3f} R after "
         f"{plan.get('cost_bps_round_trip', 0):.0f} bps of round-trip costs"),
    ]
    signals = [s.get("text") for s in (forecast.get("signals") or []) if s.get("text")]
    if signals:
        v.reasons.append("Signals: " + "; ".join(signals[:3]))
    if lab and lab.get("n"):
        v.reasons.append(
            f"Our own practice: {lab['n']} earlier advisor trade(s) with P(up) in {lab.get('bucket', 'this range')} — "
            f"{_pct(lab.get('win_rate'), 0)} won, average {lab.get('avg_return_pct', 0):+.2f}% after real-market fees"
            + (" (too few to trust yet)" if lab["n"] < 30 else ""))
    else:
        v.reasons.append("Our own practice: no earlier advisor trades in similar conditions yet")
    if costs and costs.get("n"):
        v.reasons.append(f"Costs: measured slippage {costs['slippage_bps']:+.2f} bps on {costs['n']} of our own orders, "
                         "plus each exchange's taker fee")
    if not edge:
        v.reasons.append("Caution: the next-hour model has no proven edge for this coin — treat it as close to 50/50")
    v.evidence.update({"stop": stop, "target": target, "expectancy_r": exp_r, "bracket_samples": samples,
                       "win_rate": hist.get("win_rate"), "break_even_win_rate": hist.get("break_even_win_rate"),
                       "lab": lab or {}, "costs": costs or {}})
    return v
