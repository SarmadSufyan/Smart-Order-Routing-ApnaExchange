"""Setups the trade desk scans for, on closed candles. Pure numpy.

A setup is a pattern a day trader looks for before acting: the chart says "something may start here". It is not
a forecast. Before anyone acts, the trade desk measures what happened every past time the same setup fired on
the same coin (``timemachine.replay_history``) and how the next bars could go (``timemachine.simulate``).

    setup             kind    bars      rule, at the close of the latest bar
    breakout          entry   1h, 1d    close above the highest close of the previous 20 bars (Donchian / Turtle rule)
    reclaim_ma20      entry   1h, 1d    previous close below the 20-bar average, this close above it
    oversold_bounce   entry   1h, 1d    RSI(14) under 30 at the previous bar, and this bar closed higher
    volume_surge      entry   1h, 1d    volume more than 2 sigma above the previous 24 bars (sigma at least a
                                        tenth of their mean, so a flat tape can still surge), on a rising bar
    pullback          entry   1h, 1d    uptrend (20-average above the 50, price above the 50), the price back
                                        within 1 ATR of the 20-average and below its 10-bar high
    bull_flag         watch   1h, 1d    a run of more than 10% over bars -15..-5, then five bars within a 5% range;
                                        the trigger is a close above the flag's high
    squeeze           watch   1h, 1d    Bollinger band width in the narrowest tenth of the last 100 bars
                                        (Bollinger's squeeze); the trigger is a close above the upper band
    range_breakout    entry   5m        the first 5-minute close above the opening range (the first 30 minutes
                                        after 00:00, 08:00 or 13:30 UTC), within 4 hours of that open

The first three and the flag are the day-trader scanner's rules (breakout_20d, reclaim_ma20, oversold_bounce,
bull_flag); volume, the squeeze and the pull-back are the ones the plan asked for; the opening-range breakout
is the stock market's 9:30 rule moved to the hours where crypto's turnover actually restarts (the UTC day, London,
New York). An "entry" setup is itself the trigger (the bar's close); a "watch" setup names the level to break.

A FRESH fire is the first bar of a run of true bars: a setup's history counts each episode once, as a trader
takes one trade per signal, not one per bar.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .indicators import atr, bollinger, rsi, sma

TF_MS = {"5m": 300_000, "1h": 3_600_000, "1d": 86_400_000}
DAY_MS = 86_400_000
DEFAULT_SESSION_OPENS_MIN = (0, 8 * 60, 13 * 60 + 30)  # minutes after midnight UTC
DEFAULT_RANGE_MINUTES = 30
DEFAULT_WINDOW_MINUTES = 240
MIN_BARS = {"5m": 12, "1h": 120, "1d": 120}  # fewer than this and the setups can't be judged
BARS_PER_DAY = {"5m": 288, "1h": 24, "1d": 1}


@dataclass(frozen=True)
class Setup:
    name: str
    label: str
    kind: str  # "entry": the bar is the trigger; "watch": the trigger is a level still to break
    timeframes: tuple[str, ...]
    idea: str


SETUPS: dict[str, Setup] = {
    "breakout": Setup("breakout", "Channel breakout", "entry", ("1h", "1d"),
                      "Closed above the highest close of the previous 20 bars: the Turtles' rule"),
    "reclaim_ma20": Setup("reclaim_ma20", "Reclaimed the 20-bar average", "entry", ("1h", "1d"),
                          "Previous close below the 20-bar average, this close above it: a trend resuming"),
    "oversold_bounce": Setup("oversold_bounce", "Oversold bounce", "entry", ("1h", "1d"),
                             "RSI(14) under 30 at the previous bar, then a higher close: the first up bar after a washout"),
    "volume_surge": Setup("volume_surge", "Volume surge", "entry", ("1h", "1d"),
                          "Volume more than 2 sigma above the previous 24 bars on a rising bar: something happened"),
    "pullback": Setup("pullback", "Pull-back in an uptrend", "entry", ("1h", "1d"),
                      "20-average above the 50 and price above the 50, now back within 1 ATR of the 20-average"),
    "bull_flag": Setup("bull_flag", "Bull flag", "watch", ("1h", "1d"),
                       "A run of more than 10% then five bars in a 5% range; the trigger is a close above the flag's high"),
    "squeeze": Setup("squeeze", "Volatility squeeze", "watch", ("1h", "1d"),
                     "Bollinger band width in the narrowest tenth of the last 100 bars; the trigger is a close above "
                     "the upper band"),
    "range_breakout": Setup("range_breakout", "Opening-range breakout", "entry", ("5m",),
                            "First 5-minute close above the first 30 minutes' range after a session open (UTC day, "
                            "London, New York), within 4 hours of it"),
}

RSI_OVERSOLD = 30.0
BREAKOUT_BARS = 20
VOLUME_BARS = 24
VOLUME_SIGMA = 2.0
VOLUME_SIGMA_FLOOR = 0.10  # sigma is at least this share of the mean
FLAG_RUN_PCT = 0.10
FLAG_RANGE_PCT = 0.05
SQUEEZE_BARS = 100
SQUEEZE_PERCENTILE = 10.0  # band width in the narrowest tenth of the last 100 bars
PULLBACK_HIGH_BARS = 10


@dataclass
class SessionRange:
    """One session's opening range, as the scanner saw it."""

    open_ms: int
    range_end_ms: int
    window_end_ms: int
    high: float | None  # None: the range's bars were not all there
    low: float | None
    fired_at: int | None  # bar index of the first close above the range, if any


@dataclass
class Hit:
    symbol: str
    setup: str
    label: str
    kind: str
    timeframe: str
    bar_ts: int  # start of the bar the setup fired on (ms)
    age_bars: int  # 0 = the latest closed bar
    price: float  # that bar's close
    trigger: float  # entry setups: the close; watch setups: the level to break
    dollar_volume: float  # USDT traded over the last day of bars (ranking, like the stock scanner)
    details: dict = field(default_factory=dict)
    idea: str = ""


# ── rolling helpers (NaN where the window isn't full) ─────────────────
def _prev_max(x: np.ndarray, n: int) -> np.ndarray:
    """out[t] = max of x[t-n:t] — the n values BEFORE t."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = sliding_window_view(x, n)[: len(x) - n].max(axis=1)
    return out


def _prev_mean_std(x: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    mean, std = np.full(len(x), np.nan), np.full(len(x), np.nan)
    if len(x) > n:
        w = sliding_window_view(x, n)[: len(x) - n]
        mean[n:], std[n:] = w.mean(axis=1), w.std(axis=1)
    return mean, std


def _window_max(x: np.ndarray, n: int) -> np.ndarray:
    """out[t] = max of x[t-n+1:t+1] — the n values up to and including t."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def _window_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def _window_percentile(x: np.ndarray, n: int, q: float) -> np.ndarray:
    """out[t] = the q-th percentile of x[t-n+1:t+1]; NaN while the window isn't full or holds a NaN."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        w = sliding_window_view(x, n)
        with np.errstate(invalid="ignore"):
            out[n - 1:] = np.percentile(w, q, axis=1)
    return out


def fresh(mask: np.ndarray) -> np.ndarray:
    """The first bar of every run of true bars."""
    m = np.asarray(mask, dtype=bool)
    if len(m) == 0:
        return m
    return m & ~np.r_[False, m[:-1]]


# ── the opening range ─────────────────────────────────────────────────
def session_open_of(ts_ms: np.ndarray, opens_min=DEFAULT_SESSION_OPENS_MIN) -> np.ndarray:
    """For each bar, the most recent session open (ms) at or before its start."""
    ts = np.asarray(ts_ms, dtype=np.int64)
    best = np.full(len(ts), np.iinfo(np.int64).min, dtype=np.int64)
    for m in opens_min:
        off = int(m) * 60_000
        best = np.maximum(best, (ts - off) // DAY_MS * DAY_MS + off)
    return best


def opening_ranges(ts_ms, high, low, close, tf: str = "5m", opens_min=DEFAULT_SESSION_OPENS_MIN,
                   range_minutes: int = DEFAULT_RANGE_MINUTES,
                   window_minutes: int = DEFAULT_WINDOW_MINUTES) -> tuple[np.ndarray, list[SessionRange]]:
    """Every session's opening range and the first close above it; one fire per session at most."""
    ts, h, lo, c = (np.asarray(x) for x in (ts_ms, high, low, close))
    ts = ts.astype(np.int64)
    tf_ms = TF_MS[tf]
    n_range = max(1, range_minutes * 60_000 // tf_ms)
    mask = np.zeros(len(ts), dtype=bool)
    ranges: list[SessionRange] = []
    if len(ts) == 0:
        return mask, ranges
    opens = session_open_of(ts, opens_min)
    for open_ms in np.unique(opens):
        idx = np.where(opens == open_ms)[0]
        rel = ts[idx] - open_ms
        range_end, window_end = int(open_ms + range_minutes * 60_000), int(open_ms + window_minutes * 60_000)
        in_range = idx[rel < range_minutes * 60_000]
        complete = len(in_range) == n_range and bool((ts[in_range] == open_ms + tf_ms * np.arange(n_range)).all())
        if not complete:
            ranges.append(SessionRange(int(open_ms), range_end, window_end, None, None, None))
            continue
        hi_r, lo_r = float(h[in_range].max()), float(lo[in_range].min())
        window = idx[(rel >= range_minutes * 60_000) & (rel < window_minutes * 60_000)]
        above = window[c[window] > hi_r]
        fired = int(above[0]) if len(above) else None
        if fired is not None:
            mask[fired] = True
        ranges.append(SessionRange(int(open_ms), range_end, window_end, hi_r, lo_r, fired))
    return mask, ranges


# ── the setups ────────────────────────────────────────────────────────
def raw_mask(setup: str, bars: dict, tf: str, opens_min=DEFAULT_SESSION_OPENS_MIN,
             range_minutes: int = DEFAULT_RANGE_MINUTES, window_minutes: int = DEFAULT_WINDOW_MINUTES) -> np.ndarray:
    """Where the setup's condition holds, bar by bar (not yet de-duplicated)."""
    o, h, lo, c, v = (np.asarray(bars[k], dtype=float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out
    if setup == "breakout":
        prev_hi = _prev_max(c, BREAKOUT_BARS)
        out = ~np.isnan(prev_hi) & (c > prev_hi)
    elif setup == "reclaim_ma20":
        s20 = sma(c, 20)
        prev_c, prev_s = np.r_[np.nan, c[:-1]], np.r_[np.nan, s20[:-1]]
        out = ~np.isnan(s20) & ~np.isnan(prev_s) & (prev_c < prev_s) & (c > s20)
    elif setup == "oversold_bounce":
        r = rsi(c)
        prev_r, prev_c = np.r_[np.nan, r[:-1]], np.r_[np.nan, c[:-1]]
        out = ~np.isnan(prev_r) & (prev_r < RSI_OVERSOLD) & (c > prev_c)
    elif setup == "volume_surge":
        mean, std = _prev_mean_std(v, VOLUME_BARS)
        with np.errstate(invalid="ignore"):
            sigma = np.maximum(std, VOLUME_SIGMA_FLOOR * mean)  # a flat tape can still surge
            out = ~np.isnan(mean) & (mean > 0) & (v > mean + VOLUME_SIGMA * sigma) & (c > o)
    elif setup == "pullback":
        s20, s50, a = sma(c, 20), sma(c, 50), atr(h, lo, c)
        hi10 = _prev_max(c, PULLBACK_HIGH_BARS)
        with np.errstate(invalid="ignore"):
            out = (~np.isnan(s50) & ~np.isnan(a) & ~np.isnan(hi10) & (s20 > s50) & (c > s50)
                   & (np.abs(c - s20) <= a) & (c < hi10))
    elif setup == "bull_flag":
        c15, c5 = np.r_[np.full(15, np.nan), c[:-15]], np.r_[np.full(5, np.nan), c[:-5]]
        with np.errstate(invalid="ignore", divide="ignore"):
            run = c5 / c15 - 1
            rng5 = (_window_max(c, 5) - _window_min(c, 5)) / c
            out = ~np.isnan(run) & ~np.isnan(rng5) & (run > FLAG_RUN_PCT) & (rng5 < FLAG_RANGE_PCT)
    elif setup == "squeeze":
        mid, upper, lower, _ = bollinger(c)
        with np.errstate(invalid="ignore", divide="ignore"):
            width = (upper - lower) / mid
        floor = _window_percentile(width, SQUEEZE_BARS, SQUEEZE_PERCENTILE)
        with np.errstate(invalid="ignore"):
            out = ~np.isnan(floor) & (width <= floor) & (width > 0)  # a dead tape is not a squeeze
    elif setup == "range_breakout":
        out, _ = opening_ranges(bars["ts"], h, lo, c, tf, opens_min, range_minutes, window_minutes)
    else:
        raise ValueError(f"unknown setup {setup!r}; one of {', '.join(SETUPS)}")
    return np.asarray(out, dtype=bool)


def fires(setup: str, bars: dict, tf: str, **session) -> np.ndarray:
    """Fresh fires of the setup: one per episode (the opening range already fires once per session)."""
    m = raw_mask(setup, bars, tf, **session)
    return m if setup == "range_breakout" else fresh(m)


def _last(x: np.ndarray, t: int) -> float | None:
    v = x[t]
    return None if np.isnan(v) else float(v)


def details(setup: str, bars: dict, t: int, tf: str, opens_min=DEFAULT_SESSION_OPENS_MIN,
            range_minutes: int = DEFAULT_RANGE_MINUTES, window_minutes: int = DEFAULT_WINDOW_MINUTES) -> dict:
    """The numbers behind a fire at bar ``t``, and the trigger price."""
    o, h, lo, c, v = (np.asarray(bars[k], dtype=float) for k in ("open", "high", "low", "close", "volume"))
    price = float(c[t])
    d: dict = {"trigger": price}
    if setup == "breakout":
        level = float(_prev_max(c, BREAKOUT_BARS)[t])
        d.update(level=level, above_pct=round((price / level - 1) * 100, 3))
    elif setup == "reclaim_ma20":
        s20 = sma(c, 20)[t]
        d.update(average=float(s20), above_pct=round((price / s20 - 1) * 100, 3))
    elif setup == "oversold_bounce":
        r = rsi(c)
        d.update(rsi_previous=round(float(r[t - 1]), 1), rsi_now=round(float(r[t]), 1),
                 bounce_pct=round((price / c[t - 1] - 1) * 100, 3))
    elif setup == "volume_surge":
        mean, std = _prev_mean_std(v, VOLUME_BARS)
        d.update(volume=float(v[t]), volume_sigma=round(float((v[t] - mean[t]) / std[t]), 2),
                 bar_move_pct=round((c[t] / o[t] - 1) * 100, 3))
    elif setup == "pullback":
        s20, a = sma(c, 20)[t], atr(h, lo, c)[t]
        d.update(average=float(s20), distance_atr=round(float((price - s20) / a), 3), atr=float(a),
                 recent_high=float(_prev_max(c, PULLBACK_HIGH_BARS)[t]))
    elif setup == "bull_flag":
        flag_high = float(h[t - 4:t + 1].max())
        d.update(run_pct=round(float(c[t - 5] / c[t - 15] - 1) * 100, 2),
                 flag_range_pct=round(float((c[t - 4:t + 1].max() - c[t - 4:t + 1].min()) / price) * 100, 2),
                 trigger=flag_high)
    elif setup == "squeeze":
        mid, upper, lower, _ = bollinger(c)
        d.update(band_width_pct=round(float((upper[t] - lower[t]) / mid[t]) * 100, 3), trigger=float(upper[t]),
                 lower_band=float(lower[t]))
    elif setup == "range_breakout":
        _, ranges = opening_ranges(bars["ts"], h, lo, c, tf, opens_min, range_minutes, window_minutes)
        for r in ranges:
            if r.fired_at == t:
                d.update(range_high=r.high, range_low=r.low, range_mid=(r.high + r.low) / 2,
                         session_open_ts=r.open_ms, window_end_ts=r.window_end_ms, trigger=r.high,
                         above_pct=round((price / r.high - 1) * 100, 3))
                break
    return d


def dollar_volume(bars: dict, tf: str) -> float:
    """USDT traded over the last day of bars (the scanner's ranking, like the stock scanner's price x volume)."""
    c, v = np.asarray(bars["close"], dtype=float), np.asarray(bars["volume"], dtype=float)
    n = min(len(c), BARS_PER_DAY[tf])
    return float((c[-n:] * v[-n:]).sum()) if n else 0.0


def scan_symbol(symbol: str, bars_by_tf: dict[str, dict], setups: tuple[str, ...] | None = None,
                max_age_bars: int = 1, opens_min=DEFAULT_SESSION_OPENS_MIN,
                range_minutes: int = DEFAULT_RANGE_MINUTES, window_minutes: int = DEFAULT_WINDOW_MINUTES) -> list[Hit]:
    """Every setup that is live on this coin right now, across the timeframes we hold.

    An entry setup counts when its fresh fire is at most ``max_age_bars`` closed bars old; a watch setup counts
    while its condition still holds at the latest bar (its age is the start of the run).
    """
    hits: list[Hit] = []
    session = {"opens_min": opens_min, "range_minutes": range_minutes, "window_minutes": window_minutes}
    for tf, bars in bars_by_tf.items():
        if tf not in TF_MS or len(bars.get("close", ())) < MIN_BARS[tf]:
            continue
        n = len(bars["close"])
        vol = dollar_volume(bars, tf)
        for name in setups or tuple(SETUPS):
            setup = SETUPS[name]
            if tf not in setup.timeframes:
                continue
            raw = raw_mask(name, bars, tf, **session)
            fr = raw if name == "range_breakout" else fresh(raw)
            where = np.where(fr)[0]
            if len(where) == 0:
                continue
            t = int(where[-1])
            age = n - 1 - t
            if setup.kind == "entry":
                if age > max_age_bars:
                    continue
            elif not raw[-1]:
                continue
            d = details(name, bars, t, tf, **session)
            hits.append(Hit(symbol=symbol, setup=name, label=setup.label, kind=setup.kind, timeframe=tf,
                            bar_ts=int(bars["ts"][t]), age_bars=age, price=float(bars["close"][t]),
                            trigger=float(d.pop("trigger")), dollar_volume=vol, details=d, idea=setup.idea))
    return hits


def entry_state(kind: str, trigger: float, latest_price: float | None, age_bars: int, expire_after_bars: int,
                window_end_ms: int | None = None, now_ms: int | None = None) -> tuple[str, str]:
    """(state, text): waiting / triggered / expired, as the entry timer reads it.

    An entry setup is triggered by its own bar and expires with age; a watch setup waits for the latest price to
    cross its trigger. An opening-range breakout also expires with its session's entry window.
    """
    if window_end_ms is not None and now_ms is not None and now_ms >= window_end_ms:
        return "expired", "the session's entry window has closed"
    if age_bars > expire_after_bars:
        return "expired", f"fired {age_bars} bars ago, more than the {expire_after_bars} allowed"
    if kind == "entry":
        return "triggered", "the bar that fired is the trigger"
    if latest_price is None:
        return "waiting", "no live price for this coin yet"
    if latest_price > trigger:
        return "triggered", f"the price ({latest_price:,.6g}) is above the trigger ({trigger:,.6g})"
    return "waiting", f"waiting for a close above {trigger:,.6g} (now {latest_price:,.6g}, {(latest_price / trigger - 1) * 100:+.2f}%)"
