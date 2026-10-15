"""Trade analyst — collects the evidence on one coin and turns it into a suggestion with its reasons.

The copilot's language model never decides a trade: it reads what this module computed and explains it.

    evidence (all measured on real-market history, after fees)         counts
    a technique that WORKED on ~5 years of hourly candles               +1 if it says "in" now,
                                                                        -1 if "out" (at most ±2)
    a technique that worked on the daily history                        ±1 (at most ±1)
    the next-hour / next-day forecast, only where the model has a       ±1 each (P(up) above 55% /
      proven edge (without one it counts for nothing)                   below 45%)
    the stop/target bracket replayed on past hours                      +1 made money, -1 lost

    BUY       score >= 2, at least one technique that worked says "in", and the bracket isn't a loser
    REDUCE    you hold the coin and score <= -2
    NO TRADE  everything else: the honest default when the evidence is thin or mixed

Confidence is "low" or "medium", never "high": everything here is a probability measured on the past.
``decide`` is pure (tested directly); ``Analyst`` only gathers its inputs from the running services.
"""

from backend.services.research.service import ResearchUnavailableError
from backend.shared.models.market_data import split_symbol
from backend.shared.utils.logger import get_logger

logger = get_logger("analyst")

EDGE_UP, EDGE_DOWN = 0.55, 0.45
MIN_ORDER_USDT = 6.0  # under this, no exchange takes the order (Binance / Bybit minimums ~$5, plus room for fees)
DISCLAIMER = ("A suggestion from rules measured on past data, not a promise or financial advice: prices can move "
              "against any of these signals.")
TOPICS = ("market", "portfolio", "risk", "research", "techniques", "arbitrage", "bots", "system")


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def decide(symbol: str, forecast: dict | None, tech_1h: dict | None, tech_1d: dict | None, plan: dict | None,
           holding_value_usdt: float = 0.0, max_notional: float | None = None) -> dict:
    """The verdict on one coin from the gathered evidence (see the module docstring for the rules)."""
    base = split_symbol(symbol)[0]
    score, pros, cons, notes = 0, [], [], []

    def techniques(study: dict | None, span: str, cap: int) -> int:
        if not study:
            notes.append(f"No {span} technique study yet (history still downloading)")
            return 0
        rows = {r["key"]: r for r in study.get("techniques", [])}
        got = 0
        for key in study.get("worked_in", [])[:cap]:
            pros.append(f"{rows[key]['name']} worked on the {span} history and says 'in' now ({rows[key]['why']})")
            got += 1
        for key in study.get("worked_out", [])[:cap]:
            cons.append(f"{rows[key]['name']} worked on the {span} history and says 'stay out' now")
            got -= 1
        if not study.get("worked_in") and not study.get("worked_out"):
            notes.append(f"No technique beat simply holding {base} on the {span} history after costs")
        return max(-cap, min(cap, got))

    score += techniques(tech_1h, "hourly", 2)
    score += techniques(tech_1d, "daily", 1)

    for h in (forecast or {}).get("horizons", []):
        if h.get("horizon") not in ("1h", "1d") or h.get("unavailable"):
            continue
        p, label = float(h["p_up"]), h["label"]
        if not h.get("edge"):
            notes.append(f"{label.capitalize()} forecast {_pct(p)} up — no proven edge, so it counts for nothing")
        elif p > EDGE_UP:
            pros.append(f"{label.capitalize()} forecast {_pct(p)} up, from a model with a tested edge")
            score += 1
        elif p < EDGE_DOWN:
            cons.append(f"{label.capitalize()} forecast {_pct(p)} up (leans down), from a model with a tested edge")
            score -= 1

    bracket_ok = None
    if plan and plan.get("history", {}).get("samples"):
        hist, pl = plan["history"], plan["plan"]
        bracket_ok = pl.get("verdict") == "positive"
        line = (f"The stop/target bracket, replayed at {hist['samples']} past hours: win rate "
                f"{_pct(hist.get('win_rate'))} vs {_pct(hist.get('break_even_win_rate'))} needed after fees, "
                f"average {hist.get('expectancy_r') or 0:+.2f} R per trade")
        (pros if bracket_ok else cons).append(line)
        score += 1 if bracket_ok else -1

    worked_in = bool(tech_1h and tech_1h.get("worked_in")) or bool(tech_1d and tech_1d.get("worked_in"))
    if score >= 2 and worked_in and bracket_ok is not False:
        verdict = "BUY"
    elif holding_value_usdt >= MIN_ORDER_USDT and score <= -2:
        verdict = "REDUCE"
    else:
        verdict = "NO_TRADE"
    # "medium" needs strong agreement AND a forecast with a tested edge pointing the same way as the verdict.
    same_side = pros if verdict == "BUY" else cons
    edge_agrees = any("tested edge" in reason for reason in same_side)
    confidence = "medium" if verdict != "NO_TRADE" and abs(score) >= 3 and edge_agrees else "low"

    trade = None
    if verdict == "BUY" and plan:
        pl = plan["plan"]
        qty, entry = float(pl["quantity"]), float(pl["entry"])
        if max_notional is not None and qty * entry > max_notional:
            qty = max_notional / entry
            notes.append(f"Size capped at ${max_notional:,.2f}")
        notional = qty * entry
        if notional < MIN_ORDER_USDT:
            notes.append(f"The planned size (~${notional:,.2f}) is below the exchanges' minimum order")
            verdict, trade = "NO_TRADE", None
        else:
            trade = {"side": "BUY", "symbol": symbol, "quantity": qty, "entry": entry, "stop": pl["stop"],
                     "target": pl["target"], "notional_usdt": round(notional, 2),
                     "risk_usdt": round(qty * (entry - pl["stop"]), 2), "rr": plan.get("rr")}
    elif verdict == "REDUCE":
        trade = {"side": "SELL", "symbol": symbol, "holding_value_usdt": round(holding_value_usdt, 2)}

    summary = {
        "BUY": f"BUY {base}: {len(pros)} pieces of evidence for, {len(cons)} against (score {score:+d})",
        "REDUCE": f"REDUCE {base}: the evidence leans against holding it (score {score:+d})",
        "NO_TRADE": f"No trade in {base} now: the evidence is too thin or mixed (score {score:+d})",
    }[verdict]
    return {"symbol": symbol, "verdict": verdict, "confidence": confidence, "score": score, "summary": summary,
            "for": pros, "against": cons, "notes": notes, "trade": trade, "disclaimer": DISCLAIMER}


# ── compact facts for the language model ────────────────────────────
def compact_forecast(fc: dict | None) -> list[dict]:
    out = []
    for h in (fc or {}).get("horizons", []):
        if h.get("unavailable"):
            out.append({"horizon": h["horizon"], "unavailable": h["unavailable"]})
            continue
        live = (h.get("model") or {}).get("live") or {}
        out.append({"horizon": h["label"], "price": h.get("price"), "p_up": h.get("p_up"), "edge": h.get("edge"),
                    "call": h.get("call"), "range80": h.get("range80"), "typical_move_pct": h.get("typical_move_pct"),
                    "live_hit_rate": live.get("hit_rate"), "live_forecasts": live.get("n")})
    return out


def compact_study(st: dict | None) -> dict | None:
    if not st:
        return None
    return {"timeframe": st["timeframe"], "bars": st["bars"], "hold_return_recent": st["hold"]["recent"]["total_return"],
            "techniques": [{"name": r["name"], "family": r["family"], "verdict": r["verdict"], "now": r["signal"],
                            "fresh_signal": r["fresh"], "return_full": r["full"]["total_return"],
                            "return_recent": r["recent"]["total_return"], "sharpe_full": r["full"]["sharpe"],
                            "trades": r["full"]["trades"]} for r in st["techniques"]],
            "note": st["note"]}


class Analyst:
    """Gathers what the services know. Every number the copilot quotes comes from here."""

    def __init__(self, research, aggregator, wallet, risk_engine, symbols: list[str], *, venues=None,
                 risk_monitor=None, arbitrage=None, automation=None, mode: str = "testnet",
                 max_suggest_usdt: float = 100.0) -> None:
        self._research = research
        self._aggregator = aggregator
        self._wallet = wallet
        self._risk = risk_engine
        self._symbols = list(symbols)
        self._venues = venues
        self._risk_monitor = risk_monitor
        self._arbitrage = arbitrage
        self._automation = automation
        self.mode = mode
        self._max_suggest = max_suggest_usdt

    def _holding(self, base: str) -> tuple[float, float]:
        snap = self._wallet.get_snapshot()
        for a in snap.assets:
            if a.asset == base:
                return a.total, a.value_usdt or 0.0
        return 0.0, 0.0

    async def _safe(self, coro, what: str):
        try:
            return await coro
        except ResearchUnavailableError as exc:
            logger.info("analyst_input_unavailable", what=what, reason=str(exc)[:160])
            return None

    async def analyze(self, symbol: str) -> dict:
        """The evidence on one coin and the verdict. Read-only: nothing is sent anywhere."""
        base = split_symbol(symbol)[0]
        forecast = await self._safe(self._research.forecast(symbol), "forecast")
        tech_1h = await self._safe(self._research.techniques(symbol, "1h"), "techniques 1h")
        tech_1d = await self._safe(self._research.techniques(symbol, "1d"), "techniques 1d")
        equity = max(self._wallet.get_snapshot().total_value_usdt, 100.0)
        plan = await self._safe(self._research.trade_plan(symbol, "1h", equity, 1.0, 1.5, 2.0, 24), "trade plan")
        book = await self._safe(self._research.orderbook(symbol), "order book")
        held_qty, held_value = self._holding(base)
        cap = min(self._max_suggest, self._risk.limits.max_order_notional)
        decision = decide(symbol, forecast, tech_1h, tech_1d, plan, held_value, cap)
        facts = {
            "symbol": symbol, "environment": "sandbox" if self.mode == "testnet" else "real funds",
            "forecasts": compact_forecast(forecast),
            "techniques_hourly": compact_study(tech_1h), "techniques_daily": compact_study(tech_1d),
            "trade_plan": ({"entry": plan["plan"]["entry"], "stop": plan["plan"]["stop"],
                            "target": plan["plan"]["target"], "verdict": plan["plan"]["verdict"],
                            "reasons": plan["plan"]["reasons"]} if plan else None),
            "order_book": ({"text": book.get("text"), "spread_bps": book.get("spread_bps")}
                           if book and book.get("available") else None),
            "holding": {"quantity": held_qty, "value_usdt": round(held_value, 2)},
            "decision": decision,
        }
        return {"facts": facts, "decision": decision, "studies": {"1h": tech_1h, "1d": tech_1d}}

    async def topic_facts(self, topic: str, symbol: str | None = None) -> dict:
        """Live facts for a question about one part of the system (read-only)."""
        symbols = [symbol] if symbol else self._symbols
        if topic == "market":
            rows = []
            for s in symbols:
                nbbo = await self._aggregator.compute_nbbo(s)
                if nbbo and nbbo.best_bid and nbbo.best_ask:
                    mid = (nbbo.best_bid + nbbo.best_ask) / 2
                    rows.append({"symbol": s, "mid": round(mid, 6), "best_bid": nbbo.best_bid,
                                 "best_bid_venue": nbbo.best_bid_venue, "best_ask": nbbo.best_ask,
                                 "best_ask_venue": nbbo.best_ask_venue,
                                 "spread_bps": round((nbbo.best_ask - nbbo.best_bid) / mid * 1e4, 2)})
            out = {"quotes": rows}
            if symbol:
                book = await self._safe(self._research.orderbook(symbol), "order book")
                if book and book.get("available"):
                    out["order_book"] = {"text": book["text"], "imbalance_10bps": book.get("imbalance_10bps")}
            return out
        if topic == "portfolio":
            snap = self._wallet.get_snapshot()
            return {"environment": "sandbox" if self.mode == "testnet" else "real funds",
                    "total_value_usdt": round(snap.total_value_usdt, 2),
                    "assets": [{"asset": a.asset, "total": a.total, "value_usdt": a.value_usdt} for a in snap.assets
                               if a.total],
                    "exchanges": [{"name": v.name, "value_usdt": round(v.total_value_usdt, 2), "error": v.error}
                                  for v in snap.venues]}
        if topic == "risk":
            lim = self._risk.limits
            out = {"kill_switch_active": self._risk.kill_switch.is_active,
                   "limits": {"max_order_usdt": lim.max_order_notional, "max_algo_usdt": lim.max_algo_notional_usdt,
                              "max_exposure_usdt": lim.max_notional_exposure,
                              "trading_loss_limit_usdt": lim.max_trading_loss_usdt},
                   "real_money_caps": self._risk.ceilings.model_dump() if self._risk.ceilings else None}
            if self._risk_monitor is not None and (state := self._risk_monitor.state) is not None:
                out["portfolio"] = {"trading_pnl_usdt": state.trading_pnl,
                                    "alerts": [c.model_dump() for c in state.concentration if c.breached]}
            return out
        if topic == "research":
            return {"forecasts": {s: compact_forecast(await self._safe(self._research.forecast(s), "forecast"))
                                  for s in symbols}}
        if topic == "techniques":
            return {"studies": {s: compact_study(await self._safe(self._research.techniques(s, "1h"), "techniques"))
                                for s in symbols}}
        if topic == "arbitrage" and self._arbitrage is not None:
            st = self._arbitrage.status()
            return {"summary": st.summary, "note": st.note,
                    "symbols": [s.model_dump(mode="json") for s in st.symbols][:5]}
        if topic == "bots" and self._automation is not None:
            fund = self._automation.fund.view()
            return {"fund": {"value_usdt": fund.value_usdt, "unassigned_usdt": fund.unassigned_usdt,
                             "pnl_market_usdt": fund.pnl_market_usdt},
                    "bots": [{"id": b.id, "kind": b.kind.value, "status": b.status.value, "label": b.label,
                              "pnl_market_usdt": b.pnl_market_usdt} for b in self._automation.list()][:20]}
        if topic == "system" and self._venues is not None:
            return {"environment": "sandbox" if self.mode == "testnet" else "real funds",
                    "exchanges": [{"name": h.name, "status": h.status.value, "latency_ms": h.latency_ms,
                                   "can_trade": h.trading_enabled, "note": h.execution_note}
                                  for h in self._venues.get_all_health()]}
        return {}
