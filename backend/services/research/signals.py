"""Readable signals: what the indicators say right now, and what the live order book shows. Pure.

Each signal is one plain sentence with a direction (+1 bullish, -1 bearish, 0 neutral). They
explain the picture; the forecast's probability comes from the model, not from counting them.
"""

import math

import numpy as np

from .indicators import atr, bollinger, macd, rsi, sma


def _last(x: np.ndarray) -> float | None:
    v = x[-1] if len(x) else np.nan
    return None if np.isnan(v) else float(v)


def indicator_signals(high, low, close, volume, bar_label: str) -> list[dict]:
    """[{name, value, direction, text}] for the latest candle."""
    c = np.asarray(close, dtype=float)
    out: list[dict] = []
    s20, s50 = _last(sma(c, 20)), _last(sma(c, 50))
    price = float(c[-1])
    if s20 and s50:
        trend = (s20 / s50 - 1) * 100
        d = 1 if price > s20 > s50 else -1 if price < s20 < s50 else 0
        words = {1: "an uptrend", -1: "a downtrend", 0: "no clear trend"}[d]
        out.append({"name": "Trend", "value": round(trend, 3), "direction": d,
                    "text": f"Price {(price / s50 - 1) * 100:+.2f}% vs its 50-{bar_label} average, 20-avg "
                            f"{trend:+.2f}% vs the 50 — {words}"})
    r = _last(rsi(c))
    if r is not None:
        d = -1 if r > 70 else 1 if r < 30 else 0
        words = ("overbought — pullback risk" if r > 70 else "oversold — bounce potential" if r < 30
                 else "neutral")
        out.append({"name": "RSI(14)", "value": round(r, 1), "direction": d, "text": f"RSI {r:.0f}: {words}"})
    _, _, hist = macd(c)
    h_now, h_prev = _last(hist), _last(hist[:-1]) if len(hist) > 1 else None
    if h_now is not None and h_prev is not None:
        d = 1 if h_now > 0 and h_now > h_prev else -1 if h_now < 0 and h_now < h_prev else 0
        state = "above" if h_now > 0 else "below"
        move = "strengthening" if abs(h_now) > abs(h_prev) else "fading"
        out.append({"name": "MACD", "value": round(h_now / price * 1e4, 2), "direction": d,
                    "text": f"MACD {state} its signal line, momentum {move}"})
    _, upper, lower, pct_b = bollinger(c)
    b = _last(pct_b)
    if b is not None:
        d = -1 if b > 1 else 1 if b < 0 else 0
        where = ("above the upper band — stretched up" if b > 1 else "below the lower band — stretched down"
                 if b < 0 else f"inside the bands ({b * 100:.0f}% of the way up)")
        out.append({"name": "Bollinger", "value": round(b, 3), "direction": d, "text": f"Price {where}"})
    a = _last(atr(high, low, c))
    if a is not None:
        out.append({"name": "ATR(14)", "value": round(a, 8), "direction": 0,
                    "text": f"A typical {bar_label} moves {a / price * 100:.3f}% ({a:,.6g} in price)"})
    v = np.asarray(volume, dtype=float)
    if len(v) > 25 and v[-25:-1].std() > 0:
        vz = (v[-1] - v[-25:-1].mean()) / v[-25:-1].std()
        move = c[-1] - c[-2] if len(c) > 1 else 0
        d = (1 if move > 0 else -1) if vz > 1.5 else 0
        words = "unusually high" if vz > 1.5 else "unusually low" if vz < -1.5 else "normal"
        confirm = f" on a {'rising' if move > 0 else 'falling'} {bar_label} — volume confirms the move" if vz > 1.5 else ""
        out.append({"name": "Volume", "value": round(float(vz), 2), "direction": d,
                    "text": f"Volume {words} ({vz:+.1f} σ){confirm}"})
    return out


# ── live order book ─────────────────────────────────────────────────
def book_imbalance(bids: list[tuple[float, float]], asks: list[tuple[float, float]], mid: float,
                   within_bps: float = 10.0) -> float | None:
    """(bid volume - ask volume) / both, counting levels within ``within_bps`` of the mid. +1 = all buyers."""
    if mid <= 0:
        return None
    lo, hi = mid * (1 - within_bps / 1e4), mid * (1 + within_bps / 1e4)
    b = sum(q for p, q in bids if p >= lo)
    a = sum(q for p, q in asks if p <= hi)
    return (b - a) / (b + a) if b + a > 0 else None


def microprice(best_bid: float, bid_size: float, best_ask: float, ask_size: float) -> float | None:
    """Size-weighted mid: leans toward the side with LESS size (where the next trade is likelier to push)."""
    total = bid_size + ask_size
    if total <= 0 or best_bid <= 0 or best_ask <= 0:
        return None
    return (best_ask * bid_size + best_bid * ask_size) / total


def depth_within(levels: list[tuple[float, float]], mid: float, bps: float) -> float:
    """Quote-currency value resting within ``bps`` of the mid on one side."""
    lo, hi = mid * (1 - bps / 1e4), mid * (1 + bps / 1e4)
    return sum(p * q for p, q in levels if lo <= p <= hi)


def book_signal_text(imbalance: float | None, micro_vs_mid_bps: float | None) -> tuple[int, str]:
    if imbalance is None:
        return 0, "No order-book data right now"
    d = 1 if imbalance > 0.2 else -1 if imbalance < -0.2 else 0
    side, other = ("buyers", "sellers") if imbalance > 0 else ("sellers", "buyers")
    # imbalance = (b - a) / (b + a)  ->  the bigger side holds (1 + |i|) / (1 - |i|) times the other's size
    ratio = (1 + abs(imbalance)) / (1 - abs(imbalance)) if abs(imbalance) < 1 else float("inf")
    text = (f"Near the price, {side} have {ratio:.1f}× the {other}' size (imbalance {imbalance:+.2f})"
            if abs(imbalance) > 0.05 else "Buyers and sellers about balanced near the price")
    if micro_vs_mid_bps is not None and not math.isnan(micro_vs_mid_bps):
        text += f"; microprice {micro_vs_mid_bps:+.2f} bps from the mid"
    return d, text
