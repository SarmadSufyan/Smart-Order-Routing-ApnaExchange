"""Pure maths for every execution algorithm (no I/O, no clock) — unit-tested directly.

    TWAP    equal slices:            target_i = Q * i / N
    VWAP    the market's own rhythm:  target_i = Q * (w_1 + ... + w_i), w = average share of the day's
                                      volume that traded in each slice's time bucket over the last days
    IS      Almgren-Chriss:          holdings x_k = Q * sinh(K * (1 - k/N)) / sinh(K)   (K = kappa*T, by urgency)
            expected cost E = eps * sum|n_k| + (eta / tau) * sum n_k^2
            risk (1 st.dev.) = sigma * sqrt(tau * sum x_k^2)
    POV     follow live volume:      target = p * (market volume since start)
    ICEBERG show little, take at the limit: piece = display * (1 +/- variance)

Volumes come from 1-minute candles (``MinuteBar``: volume and price*volume summed across
the exchanges we watch). A candle's volume is spread evenly over its minute when a
bucket covers only part of it.
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.exchanges.precision import floor_to_step
from backend.shared.models.market_data import MINUTE_MS, MinuteBar
from backend.shared.models.order import OrderSide
from backend.shared.models.strategy import AlgoCreate, AlgoKind, AlgoState, TwapSlice, Urgency

MAX_CATCHUP = 2.0  # a slice may be at most this many planned slices


# ── schedules ───────────────────────────────────────────────────────
def plan_schedule(
    params: AlgoCreate, quantity: float, start: datetime, lot_step: float, rng: random.Random | None = None,
) -> list[TwapSlice]:
    """TWAP: due times and cumulative targets for every slice (equal sizes)."""
    return weighted_schedule([1.0] * (params.slices or 2), quantity, start, params.duration_s, lot_step,
                             params.jitter_pct, rng)


def weighted_schedule(
    weights: list[float], quantity: float, start: datetime, duration_s: float, lot_step: float,
    jitter_pct: float = 0.0, rng: random.Random | None = None,
) -> list[TwapSlice]:
    """Evenly spaced slices whose cumulative targets follow ``weights`` (any positive numbers)."""
    n = len(weights)
    interval = duration_s / n
    total = sum(weights) or 1.0
    slices, prefix = [], 0.0
    for i, w in enumerate(weights, start=1):
        prefix += w  # exact for equal weights: i / n, as before
        cum = prefix / total
        offset = (i - 1) * interval
        if jitter_pct and i > 1 and rng is not None:
            # +/- jitter% of HALF an interval keeps slices in order.
            offset += rng.uniform(-1, 1) * jitter_pct / 100 * interval / 2
        target = quantity if i == n else floor_to_step(quantity * cum, lot_step)
        slices.append(TwapSlice(index=i, scheduled_at=start + timedelta(seconds=offset), target_cumulative=target))
    return slices


def enforce_min_slice(targets: list[float], quantity: float, min_size: float, lot_step: float) -> list[float]:
    """Cumulative targets where every non-empty slice is at least ``min_size`` (what an exchange accepts).

    A slice smaller than that is deferred into the next one (its target stays at
    the previous); a remainder still too small at the end joins the last slice
    that trades. So no planned slice is one no exchange would take.
    """
    if min_size <= 0:
        return list(targets)
    out, done = [], 0.0
    for t in targets:
        size = t - done
        if 0 < size < min_size - 1e-12:
            out.append(done)  # defer
        else:
            out.append(t)
            done = t
    out[-1] = quantity
    final = quantity - (out[-2] if len(out) > 1 else 0.0)
    if 0 < final < min_size - 1e-12 and len(out) > 1:
        last_trading = max((i for i in range(len(out) - 1) if out[i] > (out[i - 1] if i else 0.0)), default=None)
        if last_trading is not None:
            for i in range(last_trading, len(out)):
                out[i] = quantity
    return [quantity if abs(x - quantity) < 1e-12 else floor_to_step(x, lot_step) for x in out]


def targets_from_holdings(holdings: list[float]) -> list[float]:
    """Cumulative traded after each trade, from holdings x_0 = Q ... x_N = 0."""
    q = holdings[0]
    return [q - x for x in holdings[1:]]


def slice_quantity(target_cumulative: float, filled: float, nominal: float, lot_step: float,
                   max_child: float | None = None) -> float:
    """What the next slice should ask for: catch up to the schedule, within caps."""
    want = min(target_cumulative - filled, MAX_CATCHUP * nominal)
    if max_child is not None:
        want = min(want, max_child)
    return max(0.0, floor_to_step(want, lot_step))


def cost_bps(side: OrderSide, price: float, benchmark: float) -> float:
    """Execution cost vs a benchmark, in bps. Positive = worse than the benchmark."""
    sign = 1 if side == OrderSide.BUY else -1
    return (price - benchmark) / benchmark * 10_000 * sign


# ── VWAP ────────────────────────────────────────────────────────────
def bucket_volumes(bars: list[MinuteBar], window_start_ms: int, duration_s: float, buckets: int,
                   now_ms: int | None = None) -> list[float]:
    """Volume in each of ``buckets`` equal time buckets of a window, from minute bars.

    A bar's volume is spread evenly over its minute (or over the part of the
    minute that has happened, for the current one) and split by overlap.
    """
    width = duration_s * 1000 / buckets
    out = [0.0] * buckets
    for bar in bars:
        span_end = _bar_span_end(bar, now_ms)
        span = span_end - bar.ts_ms
        if bar.volume <= 0 or span <= 0:
            continue
        a = bar.ts_ms - window_start_ms
        b = a + span
        first = max(0, int(a // width))
        last = min(buckets - 1, int((b - 1e-9) // width))
        for j in range(first, last + 1):
            lo, hi = j * width, (j + 1) * width
            overlap = min(b, hi) - max(a, lo)
            if overlap > 0:
                out[j] += bar.volume * overlap / span
    return out


def volume_profile(days: list[list[MinuteBar]], day_starts_ms: list[int], duration_s: float,
                   buckets: int) -> list[float] | None:
    """Average share of the window's volume per bucket over past days (sums to 1), or None if no data.

    Each day is normalised first, so one very busy day doesn't dominate the shape.
    """
    shares = []
    for bars, start in zip(days, day_starts_ms, strict=True):
        vols = bucket_volumes(bars, start, duration_s, buckets)
        total = sum(vols)
        if total > 0:
            shares.append([v / total for v in vols])
    if not shares:
        return None
    return [sum(day[j] for day in shares) / len(shares) for j in range(buckets)]


def _bar_span_end(bar: MinuteBar, now_ms: int | None) -> int:
    """When a bar's volume stops: the end of its minute, or ``now`` for the minute in progress
    (whose candle holds only the volume traded so far)."""
    end = bar.ts_ms + MINUTE_MS
    if now_ms is not None and bar.ts_ms <= now_ms < end:
        return max(now_ms, bar.ts_ms + 1)
    return end


def volume_between(bars: list[MinuteBar], start_ms: int, end_ms: int, now_ms: int | None = None) -> float:
    """Market volume traded between two instants (partial minutes pro rata)."""
    total = 0.0
    for bar in bars:
        span_end = _bar_span_end(bar, now_ms)
        span = span_end - bar.ts_ms
        overlap = min(span_end, end_ms) - max(bar.ts_ms, start_ms)
        if bar.volume > 0 and span > 0 and overlap > 0:
            total += bar.volume * overlap / span
    return total


def market_vwap(bars: list[MinuteBar], start_ms: int, end_ms: int, now_ms: int | None = None) -> float | None:
    """The market's VWAP between two instants (partial minutes pro rata), or None without volume."""
    vol = pv = 0.0
    for bar in bars:
        span_end = _bar_span_end(bar, now_ms)
        span = span_end - bar.ts_ms
        overlap = min(span_end, end_ms) - max(bar.ts_ms, start_ms)
        if bar.volume > 0 and span > 0 and overlap > 0:
            frac = overlap / span
            vol += bar.volume * frac
            pv += bar.pv * frac
    return pv / vol if vol > 0 else None


def minute_sigma(bars: list[MinuteBar]) -> float | None:
    """Per-minute volatility (std of log returns of each minute's VWAP), or None with too little data."""
    prices = [b.pv / b.volume for b in sorted(bars, key=lambda b: b.ts_ms) if b.volume > 0]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False) if a > 0 and b > 0]
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    return math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))


# ── Implementation shortfall (Almgren-Chriss) ───────────────────────
def almgren_chriss_holdings(quantity: float, slices: int, kappa_t: float) -> list[float]:
    """Optimal holdings x_0 = Q, ..., x_N = 0 for urgency K = kappa*T (K -> 0 is TWAP)."""
    if kappa_t < 1e-6:
        return [quantity * (1 - k / slices) for k in range(slices + 1)]
    denom = math.sinh(kappa_t)
    return [quantity * math.sinh(kappa_t * (1 - k / slices)) / denom for k in range(slices + 1)]


def impact_coefficients(levels: list[tuple[float, float]], quantity: float, mid: float,
                        tau_s: float) -> tuple[float, float]:
    """(eps, eta) of a linear temporary-impact model, measured on the live book.

    ``levels`` is the consolidated side we would take (best first). Trading one
    slice of ``quantity`` in ``tau_s`` costs eps (half the spread: the price of
    crossing) plus eta * quantity / tau_s (walking deeper levels), per unit.
    """
    if not levels or quantity <= 0 or mid <= 0:
        return 0.0, 0.0
    best = levels[0][0]
    eps = abs(best - mid)
    remaining, cost = quantity, 0.0
    for price, size in levels:
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining <= 1e-12:
            break
    filled = quantity - max(remaining, 0.0)
    if filled <= 0:
        return eps, 0.0
    walk = abs(cost / filled - best)
    if remaining > 1e-12:  # the visible book ran out: assume the last level's slope continues
        walk *= quantity / filled
    return eps, walk * tau_s / quantity


def ac_cost_risk(holdings: list[float], eps: float, eta: float, sigma_abs_per_s: float,
                 tau_s: float) -> tuple[float, float]:
    """Expected impact cost and 1-st.dev. price risk (both in quote currency) of a schedule."""
    trades = [a - b for a, b in zip(holdings, holdings[1:], strict=False)]
    cost = eps * sum(abs(n) for n in trades) + (eta / tau_s) * sum(n * n for n in trades)
    risk = sigma_abs_per_s * math.sqrt(tau_s * sum(x * x for x in holdings[1:]))
    return cost, risk


# ── POV and iceberg ─────────────────────────────────────────────────
def pov_quantity(participation_pct: float, market_volume: float, filled: float, last_interval_volume: float,
                 remaining: float, lot_step: float, min_tradable: float, max_child: float | None = None) -> float:
    """Next POV order: catch up to p% of the volume so far; 0 while below the smallest tradable size.

    A catch-up is capped at twice our share of the last interval's volume (or
    one minimum order), so a stall never turns into one big burst.
    """
    p = participation_pct / 100
    want = p * market_volume - filled
    want = min(want, remaining, max(MAX_CATCHUP * p * last_interval_volume, min_tradable))
    if max_child is not None:
        want = min(want, max_child)
    want = floor_to_step(max(want, 0.0), lot_step)
    if want < min_tradable - 1e-12:
        return 0.0
    return want


def iceberg_piece(display_qty: float, variance_pct: float, u: float, remaining: float,
                  lot_step: float, min_tradable: float) -> float:
    """One iceberg piece: the display size varied by +/- variance (u in [-1, 1]), never below the minimum."""
    size = display_qty * (1 + variance_pct / 100 * max(-1.0, min(1.0, u)))
    size = floor_to_step(min(max(size, min_tradable), remaining), lot_step)
    if remaining - size < min_tradable - 1e-12:  # don't leave a remainder too small to ever trade
        size = floor_to_step(remaining, lot_step)
    return size


# ── execution quality ───────────────────────────────────────────────
def recompute_metrics(state: AlgoState) -> None:
    filled = sum(s.filled for s in state.slices)
    notional = sum(s.filled * s.avg_price for s in state.slices)
    state.filled_quantity = round(filled, 12)
    state.avg_fill_price = notional / filled if filled > 0 else 0.0
    side = state.params.side
    if filled > 0 and state.arrival_mid:
        state.arrival_slippage_bps = round(cost_bps(side, state.avg_fill_price, state.arrival_mid), 3)
    mids = [s.mid_at_send for s in state.slices if s.sent_at and s.mid_at_send]
    if mids:
        state.interval_twap = sum(mids) / len(mids)
        if filled > 0:
            state.vs_interval_twap_bps = round(cost_bps(side, state.avg_fill_price, state.interval_twap), 3)
    if filled > 0 and state.market_vwap:
        state.vs_market_vwap_bps = round(cost_bps(side, state.avg_fill_price, state.market_vwap), 3)


# ── auto: which algorithm, and why ──────────────────────────────────
TARGET_PARTICIPATION_PCT = {Urgency.LOW: 5.0, Urgency.MEDIUM: 10.0, Urgency.HIGH: 20.0}
SINGLE_MAX_EXTRA_BPS = 2.0  # do it now when walking the book costs at most this beyond half the spread
SLICE_EVERY_S = 15.0  # target spacing of slices for the scheduled algos
MAX_SANE_PARTICIPATION_PCT = 25.0


@dataclass
class AutoInputs:
    quantity: float
    mid: float
    spread_bps: float
    impact_now_bps: float | None  # cost vs mid of doing it all now on the visible books (None: they can't fill it)
    volume_per_min: float | None  # recent real-market volume per minute (base asset)
    urgency: Urgency
    max_duration_s: float
    min_tradable: float  # smallest order any venue accepts at this price
    max_order_qty: float  # the per-order risk limit, in base units
    has_volume_history: bool


@dataclass
class AutoChoice:
    kind: str  # "SINGLE" or an AlgoKind value
    duration_s: float | None = None
    slices: int | None = None
    participation_pct: float | None = None
    reasons: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def choose_algo(x: AutoInputs) -> AutoChoice:
    """Pick the execution for an order: one order now, or which algorithm over how long.

    1. If the visible books absorb it for at most ``SINGLE_MAX_EXTRA_BPS`` beyond
       half the spread and it is within the per-order limit: one order now —
       spreading it over time would only add price risk.
    2. Otherwise work it over time at a participation rate set by urgency
       (LOW 5 %, MEDIUM 10 %, HIGH 20 % of the market's volume), within the
       longest duration allowed: HIGH -> IS (front-loaded), MEDIUM -> VWAP
       (TWAP without volume history), LOW -> POV (follows the market).
    """
    half_spread = x.spread_bps / 2
    fits_one_order = x.quantity <= x.max_order_qty + 1e-12
    spread_note = (f"half the spread is {half_spread:.1f} bps" if x.spread_bps > 0
                   else "the exchanges' best prices overlap right now")
    if x.impact_now_bps is not None and fits_one_order and x.impact_now_bps - half_spread <= SINGLE_MAX_EXTRA_BPS:
        return AutoChoice(
            kind="SINGLE",
            reasons=[f"The books can take all {x.quantity:g} now for {x.impact_now_bps:.1f} bps vs the mid "
                     f"({spread_note}) — splitting it over time would only add price risk"],
            alternatives=["Any algorithm would cost about the same in impact and add exposure to price moves"],
        )

    part = TARGET_PARTICIPATION_PCT[x.urgency]
    warnings, reasons = [], []
    if x.impact_now_bps is None:
        reasons.append("The visible order books can't fill it at once")
    else:
        reasons.append(f"Doing it all now would cost {x.impact_now_bps:.1f} bps vs the mid "
                       f"({x.impact_now_bps - half_spread:.1f} bps beyond crossing the spread)")
    if not fits_one_order:
        reasons.append(f"It is above the per-order limit ({x.max_order_qty:g}), so it must be sliced")
    if x.volume_per_min and x.volume_per_min > 0:
        duration = x.quantity / (part / 100 * x.volume_per_min) * 60
        duration = min(max(duration, 60.0), x.max_duration_s)
        actual = x.quantity / (x.volume_per_min * duration / 60) * 100
        reasons.append(f"The market trades ~{x.volume_per_min:g} per minute: {duration / 60:.1f} min keeps us near "
                       f"{actual:.1f} % of the volume ({x.urgency.value.lower()} urgency aims for {part:g} %)")
        if actual > MAX_SANE_PARTICIPATION_PCT:
            warnings.append(f"Even over the longest allowed time this is {actual:.0f} % of the market's volume — "
                            "expect a visible price impact; consider a longer window or a smaller order")
    else:
        duration = min(600.0, x.max_duration_s)
        warnings.append("No recent volume data: duration chosen without it")

    n = max(2, round(duration / SLICE_EVERY_S))
    if x.min_tradable > 0:
        n = min(n, int(x.quantity // (2 * x.min_tradable)) or 1)  # every slice at least two minimum orders
    n = max(n, -(-x.quantity // x.max_order_qty) if x.max_order_qty > 0 else 2)  # and within the per-order limit
    n = int(min(max(n, 2), 500, duration // 3))
    if n < 2 or x.quantity / n < x.min_tradable:
        if fits_one_order:
            return AutoChoice(kind="SINGLE", reasons=reasons + [
                "It is too small to split into two orders that an exchange accepts — one order now"],
                warnings=warnings)
        return AutoChoice(kind="NONE", reasons=reasons, warnings=warnings + [
            "It can neither go as one order (per-order limit) nor be split into orders the exchanges accept"])

    if x.urgency == Urgency.HIGH:
        kind = AlgoKind.IS
        reasons.append("High urgency: implementation shortfall front-loads the order to cut exposure to price moves")
    elif x.urgency == Urgency.LOW:
        kind = AlgoKind.POV
        reasons.append(f"Low urgency: POV trades {part:g} % of the real volume as it happens — the smallest footprint")
    elif x.has_volume_history:
        kind = AlgoKind.VWAP
        reasons.append("Medium urgency: VWAP follows the market's usual volume over this window, to be judged "
                       "against the market's own VWAP")
    else:
        kind = AlgoKind.TWAP
        reasons.append("Medium urgency without volume history: TWAP, even slices")
    alternatives = {
        AlgoKind.IS: "IS: faster and front-loaded — lower price risk, more impact",
        AlgoKind.VWAP: "VWAP: follows the usual volume — the standard benchmark",
        AlgoKind.TWAP: "TWAP: even slices — simplest, predictable",
        AlgoKind.POV: "POV: a fixed share of live volume — smallest footprint, no fixed end time",
    }
    return AutoChoice(
        kind=kind.value,
        duration_s=round(duration * (1.5 if kind == AlgoKind.POV else 1.0), 1),
        slices=None if kind == AlgoKind.POV else n,
        participation_pct=part if kind == AlgoKind.POV else None,
        reasons=reasons,
        alternatives=[text for k, text in alternatives.items() if k != kind],
        warnings=warnings,
    )
