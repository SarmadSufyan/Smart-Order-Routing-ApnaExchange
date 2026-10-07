"""The forecast model — features, logistic regression, walk-forward testing, honest scoring. Pure numpy.

What it predicts: the probability that the NEXT candle closes higher than this one
(next minute / hour / day, by the candle size). Nothing else: no price targets.

    features at candle t use ONLY candles 0..t (no look-ahead):
        momentum   log returns over 1, 3, 6, 12, 24 candles                ("the trend is your friend")
        RSI(14)    (RSI - 50) / 50                                          (overbought / oversold)
        MACD       histogram / price                                       (trend acceleration)
        Bollinger  %B - 0.5                                                 (stretched from the mean)
        distance   close / SMA20 - 1, close / SMA50 - 1
        volatility std of returns over 24 candles, and its ratio to 96     (regime)
        volume     z-score of volume over 24 candles, and z x last return  (volume confirms a move)
        ATR(14) / price
        clock      hour of day (minute and hour candles) or day of week (daily), as sin / cos
    label y_t = 1 if close_{t+1} > close_t else 0

    model       logistic regression on standardised features, L2 (ridge) penalty, fitted by IRLS; the
                penalty is chosen inside each training window (fit on its first 75 %, judge on the last 25 %)
    testing     walk-forward: train on everything before a block, predict the block, move on —
                every scored prediction was made without seeing its outcome
    scoring     accuracy vs always guessing the more common side; Brier score and log loss vs the
                base rate; calibration (when it says 60 %, does it happen 60 % of the time?)
                "edge" only if it beats the baseline on BOTH accuracy and log loss out of sample, AND the
                accuracy gain is unlikely to be luck (one-sided p < 0.05)
"""

import math
from dataclasses import dataclass, field

import numpy as np

from .indicators import atr, bollinger, log_returns, macd, rolling_std, rsi, sma, zscore

FEATURE_NAMES = [
    "return_1", "return_3", "return_6", "return_12", "return_24",
    "rsi_14", "macd_hist", "bollinger_b", "dist_sma20", "dist_sma50",
    "vol_24", "vol_ratio", "volume_z", "volume_x_return", "atr_pct",
    "clock_sin", "clock_cos",
]
WARMUP = 100  # candles before features are complete (SMA50, vol 96, ...)


def build_features(open_, high, low, close, volume, ts_ms, timeframe: str) -> np.ndarray:
    """Feature matrix [n x len(FEATURE_NAMES)], NaN rows during warm-up."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    r1 = log_returns(c)

    def ret(k: int) -> np.ndarray:
        out = np.full(n, np.nan)
        out[k:] = np.log(c[k:] / c[:-k])
        return out

    _, _, hist = macd(c)
    _, _, _, pct_b = bollinger(c)
    vol24, vol96 = rolling_std(r1, 24), rolling_std(r1, 96)
    vz = zscore(np.asarray(volume, dtype=float), 24)
    ts = np.asarray(ts_ms, dtype=np.int64) // 1000
    if timeframe == "1d":
        angle = 2 * np.pi * (((ts // 86_400) + 3) % 7) / 7  # 1970-01-01 was a Thursday -> Monday = 0
    else:
        angle = 2 * np.pi * ((ts % 86_400) / 86_400)
    cols = [
        r1, ret(3), ret(6), ret(12), ret(24),
        (rsi(c) - 50) / 50, hist / c, pct_b - 0.5, c / sma(c, 20) - 1, c / sma(c, 50) - 1,
        vol24, np.where(vol96 > 0, vol24 / np.where(vol96 > 0, vol96, 1) - 1, 0.0), vz, vz * r1,
        atr(high, low, c) / c, np.sin(angle), np.cos(angle),
    ]
    X = np.column_stack(cols)
    X[:WARMUP] = np.nan
    return X


def next_up_labels(close) -> np.ndarray:
    """y_t = 1 if the next close is higher, else 0; the last candle has no label (NaN)."""
    c = np.asarray(close, dtype=float)
    y = np.full(len(c), np.nan)
    y[:-1] = (c[1:] > c[:-1]).astype(float)
    return y


# ── logistic regression ─────────────────────────────────────────────
@dataclass
class LogisticModel:
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray  # [intercept, w_1 .. w_k] on standardised features
    l2: float = 1.0


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> LogisticModel:
    """Ridge logistic regression by Newton / IRLS (the intercept is not penalised)."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mean) / std])
    w = np.zeros(Z.shape[1])
    penalty = np.full(Z.shape[1], l2)
    penalty[0] = 0.0
    for _ in range(iters):
        p = _sigmoid(Z @ w)
        grad = Z.T @ (p - y) + penalty * w
        hess = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(penalty) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return LogisticModel(mean=mean, std=std, coef=w, l2=l2)


def predict_proba(model: LogisticModel, X: np.ndarray) -> np.ndarray:
    Z = (np.atleast_2d(X) - model.mean) / model.std
    return _sigmoid(model.coef[0] + Z @ model.coef[1:])


def contributions(model: LogisticModel, x: np.ndarray) -> list[tuple[str, float]]:
    """How much each feature pushes this forecast (log-odds units), biggest first."""
    z = (x - model.mean) / model.std
    parts = [(name, float(wi * zi)) for name, wi, zi in zip(FEATURE_NAMES, model.coef[1:], z, strict=True)]
    return sorted(parts, key=lambda kv: abs(kv[1]), reverse=True)


def usable_rows(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Indices with complete features and a known outcome."""
    return np.where(~np.isnan(X).any(axis=1) & ~np.isnan(y))[0]


L2_GRID = (1.0, 10.0, 100.0, 1_000.0, 10_000.0)


def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
    eps = 1e-12
    return float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))


def select_l2(X: np.ndarray, y: np.ndarray, grid: tuple[float, ...] = L2_GRID, valid_frac: float = 0.25) -> float:
    """Choose the penalty using ONLY these (training) rows: fit on the earlier 75 %, judge on the later 25 %.

    A stronger penalty pulls every forecast toward 50 % unless the evidence is strong; on noisy data
    that is what keeps the probabilities honest. Choosing it inside the training window (never on
    the rows being tested) keeps the walk-forward score free of hindsight.
    """
    cut = int(len(X) * (1 - valid_frac))
    if cut < 100 or len(X) - cut < 50:
        return grid[len(grid) // 2]
    best, best_loss = grid[0], np.inf
    for l2 in grid:
        loss = _log_loss(predict_proba(fit_logistic(X[:cut], y[:cut], l2=l2), X[cut:]), y[cut:])
        if loss < best_loss:
            best, best_loss = l2, loss
    return best


def walk_forward(X: np.ndarray, y: np.ndarray, first_train_frac: float = 0.6, blocks: int = 8,
                 l2: float | None = None) -> np.ndarray:
    """Out-of-sample probabilities: each block is predicted by a model trained only on earlier rows.

    ``l2=None`` (default) chooses the penalty inside each training window (``select_l2``).
    """
    rows = usable_rows(X, y)
    probs = np.full(len(y), np.nan)
    if len(rows) < 200:
        return probs
    start = int(len(rows) * first_train_frac)
    edges = np.linspace(start, len(rows), blocks + 1).astype(int)
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        if b <= a:
            continue
        train, test = rows[:a], rows[a:b]
        penalty = l2 if l2 is not None else select_l2(X[train], y[train])
        model = fit_logistic(X[train], y[train], l2=penalty)
        probs[test] = predict_proba(model, X[test])
    return probs


# ── scoring ─────────────────────────────────────────────────────────
@dataclass
class Scores:
    n: int
    accuracy: float
    baseline_accuracy: float  # always guessing the more common side
    up_rate: float
    brier: float
    brier_baseline: float  # always forecasting the up rate
    brier_skill: float  # 1 - brier / baseline: > 0 = better than the baseline
    log_loss: float
    log_loss_baseline: float
    p_value: float  # chance of beating the baseline accuracy this much by luck (one-sided)
    edge: bool  # beats the baseline on accuracy AND log loss, and p_value < EDGE_P_VALUE
    calibration: list[dict] = field(default_factory=list)  # [{bucket, said, happened, n}]


EDGE_P_VALUE = 0.05  # one-sided; with 15 coin x horizon models, expect ~1 false "edge" by chance — said in the guide


def score(probs: np.ndarray, y: np.ndarray) -> Scores | None:
    """Compare forecasts with outcomes; None if fewer than 30 scored forecasts."""
    m = ~np.isnan(probs) & ~np.isnan(y)
    p, t = probs[m], y[m]
    if len(p) < 30:
        return None
    up = float(t.mean())
    eps = 1e-12
    brier = float(np.mean((p - t) ** 2))
    brier_base = up * (1 - up)
    ll = float(-np.mean(t * np.log(p + eps) + (1 - t) * np.log(1 - p + eps)))
    ll_base = float(-(up * math.log(up + eps) + (1 - up) * math.log(1 - up + eps)))
    acc = float(np.mean((p > 0.5) == (t == 1)))
    base_acc = max(up, 1 - up)
    # Is the accuracy above the baseline more than luck would give? (normal approximation to the binomial)
    se = math.sqrt(base_acc * (1 - base_acc) / len(p))
    z = (acc - base_acc) / se if se > 0 else 0.0
    p_value = 0.5 * math.erfc(z / math.sqrt(2))
    calib = []
    for lo in np.arange(0.3, 0.7, 0.05):
        sel = (p >= lo) & (p < lo + 0.05)
        if sel.sum() >= 10:
            calib.append({"bucket": f"{lo * 100:.0f}-{(lo + 0.05) * 100:.0f}%", "said": round(float(p[sel].mean()), 4),
                          "happened": round(float(t[sel].mean()), 4), "n": int(sel.sum())})
    return Scores(n=int(len(p)), accuracy=round(acc, 4), baseline_accuracy=round(base_acc, 4), up_rate=round(up, 4),
                  brier=round(brier, 5), brier_baseline=round(brier_base, 5),
                  brier_skill=round(1 - brier / brier_base, 5) if brier_base > 0 else 0.0,
                  log_loss=round(ll, 5), log_loss_baseline=round(ll_base, 5), p_value=round(p_value, 5),
                  edge=bool(acc > base_acc and ll < ll_base and p_value < EDGE_P_VALUE), calibration=calib)


# ── ranges: how far the price usually moves ─────────────────────────
def standardized_next_returns(returns: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """z_t = r_{t+1} / sigma_t: the next move measured in units of the volatility known at t."""
    z = np.full(len(returns), np.nan)
    z[:-1] = returns[1:] / np.where(sigma[:-1] > 0, sigma[:-1], np.nan)
    return z


RANGE_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def range_quantiles(z: np.ndarray) -> dict[float, float]:
    """Empirical quantiles of standardised moves (fat tails included, no normal-curve assumption)."""
    v = z[~np.isnan(z)]
    return {q: float(np.quantile(v, q)) for q in RANGE_QUANTILES} if len(v) >= 30 else {}


def range_coverage(z: np.ndarray, train_frac: float = 0.6) -> float | None:
    """Out of sample, how often the next move fell inside the 10-90 % band learned on earlier data (target 80 %)."""
    v = z[~np.isnan(z)]
    if len(v) < 100:
        return None
    cut = int(len(v) * train_frac)
    lo, hi = np.quantile(v[:cut], 0.10), np.quantile(v[:cut], 0.90)
    test = v[cut:]
    return round(float(np.mean((test >= lo) & (test <= hi))), 4)
