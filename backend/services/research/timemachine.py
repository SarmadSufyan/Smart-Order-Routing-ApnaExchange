"""The time machine: how could the next bars go, and what happened every past time this setup fired? Pure numpy.

Two honest answers to "what are the odds of this bracket?", both from the coin's own candles:

1. ``simulate`` — a bootstrap replay. Every past bar is kept as ratios to the close before it (high, low, close);
   the pool is grouped by time of day (one slot per bar of the day, so 2 a.m. draws from past 2 a.m. bars, when
   crypto is quiet, and 14:30 UTC from past 14:30s, when New York is open). A replay starts at the entry price,
   draws one past bar per slot (jittered by up to two slots, with replacement, independent per bar) and scales it
   onto the path so far, then checks the stop BEFORE the target: a bar that touches both is a loss (we can't know
   the order; be conservative). Ten thousand replays, a fixed seed, so the same question gives the same answer.
   This is the day-trader kit's Time Machine, moved from 78 five-minute stock bars to crypto's 24-hour day.

2. ``replay_history`` — the real bars. At every past bar where the setup fired, the same bracket is placed and
   the following bars are read: which came first, stop or target, or neither within the time limit. No sampling,
   no model: what actually happened, counted.

Neither is a prediction. The simulation knows nothing about tomorrow's news, and a history of 40 fires is a small
sample. Both are compared with the break-even share: with reward R per unit of risk, a bracket pays only if the
target comes first more than 1 / (1 + R) of the decided times (more once fees are counted).
"""

from dataclasses import dataclass, field

import numpy as np

from .scanner import DAY_MS, TF_MS

DEFAULT_REPLAYS = 10_000
DEFAULT_SEED = 19
DEFAULT_JITTER = 2
BAND_PATHS = 2_000
BAND_POINTS = 48  # at most this many bars in the fan chart
MIN_POOL_BARS = 50


def slots_per_day(tf: str) -> int:
    return 1 if tf == "1d" else DAY_MS // TF_MS[tf]


def slot_of(ts_ms: int, tf: str) -> int:
    """Which bar of the day a timestamp is (0 at midnight UTC); daily bars have one slot."""
    return 0 if tf == "1d" else int((int(ts_ms) % DAY_MS) // TF_MS[tf])


@dataclass
class Pool:
    tf: str
    slots: int
    ratios: list[np.ndarray]  # per slot: (k, 3) rows of (high, low, close) / previous close
    bars: int  # how many bars went into the pool
    first_ts: int
    last_ts: int

    @property
    def sessions(self) -> int:
        """Past days (or past bars, for daily) behind a typical slot."""
        sizes = [len(r) for r in self.ratios if len(r)]
        return int(np.median(sizes)) if sizes else 0


def build_pool(ts_ms, high, low, close, tf: str, bars: int | None = None) -> Pool:
    """Ratios of the last ``bars`` bars, grouped by time of day.

    A slot with no bars (a gap in the history) borrows the whole day's bars, so a replay never stalls there.
    """
    ts = np.asarray(ts_ms, dtype=np.int64)
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    if bars is not None and len(c) > bars + 1:
        ts, h, lo, c = ts[-(bars + 1):], h[-(bars + 1):], lo[-(bars + 1):], c[-(bars + 1):]
    if len(c) < 2:
        raise ValueError("the time machine needs at least two candles")
    prev = c[:-1]
    ok = prev > 0
    rows = np.column_stack([h[1:] / prev, lo[1:] / prev, c[1:] / prev])[ok]
    row_ts = ts[1:][ok]
    n_slots = slots_per_day(tf)
    slots = np.zeros(len(rows), dtype=int) if tf == "1d" else ((row_ts % DAY_MS) // TF_MS[tf]).astype(int)
    ratios: list[np.ndarray] = [rows[slots == s] for s in range(n_slots)]
    everything = rows
    ratios = [r if len(r) else everything for r in ratios]
    return Pool(tf=tf, slots=n_slots, ratios=ratios, bars=len(rows), first_ts=int(row_ts[0]), last_ts=int(row_ts[-1]))


@dataclass
class Odds:
    replays: int
    bars: int
    entry: float
    stop: float
    target: float
    rr: float
    p_target: float  # the target came first
    p_stop: float  # the stop came first
    p_neither: float  # still open after ``bars``
    p_green: float  # target first, or still open above the entry
    avg_r: float  # average result per replay in R, after costs
    median_close: float
    close_p5: float
    close_p95: float
    median_bars_to_outcome: float | None
    break_even_share: float  # target / (target + stop) needed to break even, after costs
    share: float | None  # target / (target + stop) in the replays
    verdict: str  # favourable / coin_flip / unfavourable
    verdict_text: str
    bands: list[dict] = field(default_factory=list)  # [{bar, p5, p25, p50, p75, p95}]
    histogram: dict = field(default_factory=dict)  # {"edges": [...], "counts": [...]} of the closing price
    pool_bars: int = 0
    pool_sessions: int = 0
    seed: int = DEFAULT_SEED


def break_even_share(rr: float, cost_r: float = 0.0) -> float:
    """The share of decided outcomes the target must win for zero expectancy: (1 + c) / (1 + R)."""
    return (1 + cost_r) / (rr + 1) if rr > -1 else 1.0


def verdict_for(share: float | None, be: float) -> tuple[str, str]:
    if share is None:
        return "coin_flip", "no replay was decided within the time limit, so there is nothing to judge"
    gap = share - be
    if gap >= 0.15:
        return "favourable", f"the target came first in {share * 100:.1f}% of decided replays, well above the {be * 100:.1f}% needed"
    if gap >= 0.05:
        return "favourable", f"the target came first in {share * 100:.1f}% of decided replays, above the {be * 100:.1f}% needed"
    if gap <= -0.15:
        return "unfavourable", f"the target came first in only {share * 100:.1f}% of decided replays, well below the {be * 100:.1f}% needed"
    if gap <= -0.05:
        return "unfavourable", f"the target came first in {share * 100:.1f}% of decided replays, below the {be * 100:.1f}% needed"
    return "coin_flip", f"the target came first in {share * 100:.1f}% of decided replays, about the {be * 100:.1f}% needed: a coin flip"


def simulate(pool: Pool, start_slot: int, bars: int, entry: float, stop: float, target: float,
             replays: int = DEFAULT_REPLAYS, seed: int = DEFAULT_SEED, jitter: int = DEFAULT_JITTER,
             cost_r: float = 0.0, band_paths: int = BAND_PATHS) -> Odds:
    """Replay ``bars`` bars after an entry at ``entry`` with a long bracket, ``replays`` times."""
    if not (stop < entry < target):
        raise ValueError("a long bracket needs stop < entry < target")
    if bars < 1 or replays < 1:
        raise ValueError("bars and replays must be positive")
    risk = entry - stop
    rr = (target - entry) / risk
    rng = np.random.default_rng(seed)
    p = np.full(replays, float(entry))
    outcome = np.zeros(replays)
    decided = np.zeros(replays, dtype=bool)
    decided_bar = np.full(replays, -1)
    band_paths = min(band_paths, replays)
    paths = np.empty((bars, band_paths))
    use_jitter = jitter > 0 and pool.slots > 1
    for i in range(bars):
        base = (start_slot + i) % pool.slots
        slots = ((base + rng.integers(-jitter, jitter + 1, size=replays)) % pool.slots if use_jitter
                 else np.full(replays, base))
        h = np.empty(replays)
        lo = np.empty(replays)
        c = np.empty(replays)
        for s in np.unique(slots):
            where = slots == s
            arr = pool.ratios[s]
            pick = arr[rng.integers(len(arr), size=int(where.sum()))]
            h[where], lo[where], c[where] = pick[:, 0], pick[:, 1], pick[:, 2]
        hi, low = p * h, p * lo
        p = p * c
        open_ = ~decided
        hit_stop = open_ & (low <= stop)
        hit_target = open_ & ~hit_stop & (hi >= target)
        outcome[hit_stop] = -1.0
        outcome[hit_target] = rr
        decided_bar[hit_stop | hit_target] = i
        decided |= hit_stop | hit_target
        paths[i] = p[:band_paths]
    still_open = ~decided
    outcome[still_open] = (p[still_open] - entry) / risk
    n_target = int(np.sum(decided & (outcome > 0)))
    n_stop = int(np.sum(decided & (outcome < 0)))
    p_target, p_stop = n_target / replays, n_stop / replays
    p_neither = float(still_open.mean())
    share = n_target / (n_target + n_stop) if n_target + n_stop else None
    be = break_even_share(rr, cost_r)
    verdict, text = verdict_for(share, be)
    step = max(1, int(np.ceil(bars / BAND_POINTS)))
    idx = sorted(set(range(0, bars, step)) | {bars - 1})
    pct = np.percentile(paths[idx], [5, 25, 50, 75, 95], axis=1)
    bands = [{"bar": int(i) + 1, "p5": float(pct[0][k]), "p25": float(pct[1][k]), "p50": float(pct[2][k]),
              "p75": float(pct[3][k]), "p95": float(pct[4][k])} for k, i in enumerate(idx)]
    lo_e, hi_e = (float(x) for x in np.percentile(p, [1, 99]))
    if hi_e - lo_e > 1e-9 * max(abs(hi_e), 1.0):
        counts, edges = np.histogram(p, bins=24, range=(lo_e, hi_e))
    else:  # every replay ended at the same price (a flat or deterministic pool)
        counts, edges = np.array([replays]), np.array([lo_e, hi_e])
    decided_bars = decided_bar[decided]
    return Odds(
        replays=replays, bars=bars, entry=float(entry), stop=float(stop), target=float(target), rr=round(float(rr), 4),
        p_target=round(p_target, 4), p_stop=round(p_stop, 4), p_neither=round(p_neither, 4),
        p_green=round(float(np.mean(outcome > 0)), 4), avg_r=round(float(np.mean(outcome - cost_r)), 4),
        median_close=float(np.median(p)), close_p5=float(np.percentile(p, 5)), close_p95=float(np.percentile(p, 95)),
        median_bars_to_outcome=float(np.median(decided_bars)) + 1 if len(decided_bars) else None,
        break_even_share=round(be, 4), share=round(share, 4) if share is not None else None,
        verdict=verdict, verdict_text=text, bands=bands,
        histogram={"edges": [float(e) for e in edges], "counts": [int(x) for x in counts]},
        pool_bars=pool.bars, pool_sessions=pool.sessions, seed=seed,
    )


@dataclass
class HistoryCheck:
    samples: int  # fires with enough bars after them to judge
    wins: int
    losses: int
    timeouts: int
    win_rate: float | None  # wins / (wins + losses)
    expectancy_r: float | None  # average result per fire in R, after costs
    break_even_win_rate: float
    median_bars_to_outcome: float | None
    first_ts: int | None  # the oldest fire counted
    last_ts: int | None
    recent: list[dict] = field(default_factory=list)  # the last few fires: {ts, outcome, r, bars}


def replay_history(ts_ms, high, low, close, entries, stop_dist, target_dist, max_bars: int,
                   cost_r: float | np.ndarray = 0.0, recent: int = 8) -> HistoryCheck:
    """Place the bracket at every past fire (entry at that bar's close) and read the real bars that followed.

    ``stop_dist`` / ``target_dist`` are per-bar arrays (an ATR-based stop varies with the bar) or scalars.
    The stop is checked before the target within a bar. A fire too close to the end of the history to run its
    full ``max_bars`` is not a sample.
    """
    ts = np.asarray(ts_ms, dtype=np.int64)
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(c)
    sd = np.broadcast_to(np.asarray(stop_dist, dtype=float), (n,))
    td = np.broadcast_to(np.asarray(target_dist, dtype=float), (n,))
    cr = np.broadcast_to(np.asarray(cost_r, dtype=float), (n,))
    wins = losses = timeouts = 0
    results: list[float] = []
    bars_used: list[int] = []
    rows: list[dict] = []
    counted: list[int] = []
    first_ts = last_ts = None
    for t in (int(x) for x in np.asarray(entries, dtype=int)):
        if t < 0 or t >= n - 1 or np.isnan(sd[t]) or sd[t] <= 0 or np.isnan(td[t]) or td[t] <= 0:
            continue
        end = min(t + max_bars, n - 1)
        if end - t < max_bars:
            continue  # not enough history after it
        counted.append(t)
        entry, risk = c[t], sd[t]
        stop, target = entry - risk, entry + td[t]
        rr = td[t] / risk
        outcome, word, used = None, "timeout", max_bars
        for j in range(t + 1, end + 1):
            if lo[j] <= stop:
                outcome, word, used = -1.0, "stop", j - t
                break
            if h[j] >= target:
                outcome, word, used = rr, "target", j - t
                break
        if outcome is None:
            outcome = (c[end] - entry) / risk
            timeouts += 1
        elif outcome > 0:
            wins += 1
        else:
            losses += 1
        r_net = float(outcome - cr[t])
        results.append(r_net)
        bars_used.append(used)
        rows.append({"ts": int(ts[t]), "outcome": word, "r": round(r_net, 3), "bars": used, "entry": float(entry)})
        first_ts = int(ts[t]) if first_ts is None else first_ts
        last_ts = int(ts[t])
    samples = len(results)
    decided = wins + losses
    idx = np.asarray(counted, dtype=int)
    mean_cost = float(np.mean(cr[idx])) if samples else 0.0
    rr_mean = float(np.mean(td[idx] / sd[idx])) if samples else 1.0
    return HistoryCheck(
        samples=samples, wins=wins, losses=losses, timeouts=timeouts,
        win_rate=round(wins / decided, 4) if decided else None,
        expectancy_r=round(float(np.mean(results)), 4) if samples else None,
        break_even_win_rate=round(break_even_share(rr_mean, mean_cost), 4),
        median_bars_to_outcome=float(np.median(bars_used)) if bars_used else None,
        first_ts=first_ts, last_ts=last_ts, recent=rows[-recent:],
    )


def history_verdict(check: HistoryCheck, min_samples: int = 20) -> tuple[str, str]:
    """What the real history says about this setup's bracket on this coin."""
    if check.samples < min_samples:
        return "too_few", (f"only {check.samples} past fires with enough bars after them (need {min_samples}); "
                           "not enough to judge")
    if check.win_rate is None:
        return "too_few", "no past fire reached its stop or target within the time limit"
    edge = check.win_rate > check.break_even_win_rate and (check.expectancy_r or 0.0) > 0
    if edge:
        return "positive", (f"won {check.wins}, lost {check.losses}, timed out {check.timeouts} of {check.samples} past fires: "
                            f"{check.win_rate * 100:.1f}% vs {check.break_even_win_rate * 100:.1f}% needed, "
                            f"{check.expectancy_r:+.3f} R per fire after costs")
    return "negative", (f"won {check.wins}, lost {check.losses}, timed out {check.timeouts} of {check.samples} past fires: "
                        f"{check.win_rate * 100:.1f}% vs {check.break_even_win_rate * 100:.1f}% needed, "
                        f"{(check.expectancy_r or 0.0):+.3f} R per fire after costs: no edge")
