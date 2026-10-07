"""Technical indicators — pure functions on numpy arrays (no I/O), each the textbook definition.

Every function returns an array the same length as its input, with NaN where
there isn't enough history yet ("warm-up"), so values line up with candles.

    SMA(n)            simple moving average of the close
    EMA(n)            exponential moving average, alpha = 2 / (n + 1), seeded with the first SMA
    RSI(n)            Wilder's relative strength index: 100 - 100 / (1 + avg gain / avg loss)
    MACD(12, 26, 9)   EMA12 - EMA26, its 9-EMA signal line, and the histogram (difference)
    Bollinger(20, 2)  SMA20 +/- 2 standard deviations; %B = where the close sits in the band (0..1)
    ATR(n)            Wilder's average true range (true range = max(H-L, |H-prevC|, |L-prevC|))
    OBV               on-balance volume: +volume on an up close, -volume on a down close
    EWMA volatility   RiskMetrics: var_t = lambda * var_{t-1} + (1 - lambda) * r_t^2  (lambda 0.94)
"""

import numpy as np

NAN = np.nan


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def log_returns(close) -> np.ndarray:
    """ln(c_t / c_{t-1}); the first value is NaN."""
    c = _arr(close)
    out = np.full_like(c, NAN)
    out[1:] = np.log(c[1:] / c[:-1])
    return out


def sma(x, n: int) -> np.ndarray:
    x = _arr(x)
    out = np.full_like(x, NAN)
    if len(x) >= n:
        csum = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (csum[n:] - csum[:-n]) / n
    return out


def ema(x, n: int) -> np.ndarray:
    x = _arr(x)
    out = np.full_like(x, NAN)
    if len(x) < n:
        return out
    alpha = 2 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rolling_std(x, n: int) -> np.ndarray:
    """Sample standard deviation over the last n values."""
    x = _arr(x)
    out = np.full_like(x, NAN)
    if len(x) >= n:
        windows = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1:] = windows.std(axis=1, ddof=1)
    return out


def rsi(close, n: int = 14) -> np.ndarray:
    c = _arr(close)
    out = np.full_like(c, NAN)
    if len(c) <= n:
        return out
    delta = np.diff(c)
    gain, loss = np.clip(delta, 0, None), np.clip(-delta, 0, None)
    avg_gain, avg_loss = gain[:n].mean(), loss[:n].mean()

    def value(g: float, lo: float) -> float:
        if lo == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / lo)

    out[n] = value(avg_gain, avg_loss)
    for i in range(n + 1, len(c)):
        avg_gain = (avg_gain * (n - 1) + gain[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + loss[i - 1]) / n
        out[i] = value(avg_gain, avg_loss)
    return out


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(macd line, signal line, histogram)."""
    c = _arr(close)
    line = ema(c, fast) - ema(c, slow)
    sig = np.full_like(c, NAN)
    valid = ~np.isnan(line)
    if valid.sum() >= signal:
        first = int(np.argmax(valid))
        sig[first:] = ema(line[first:], signal)
    return line, sig, line - sig


def bollinger(close, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(middle, upper, lower, %B). %B = (close - lower) / (upper - lower)."""
    c = _arr(close)
    mid = sma(c, n)
    # Bollinger uses the population standard deviation over the window.
    sd = np.full_like(c, NAN)
    if len(c) >= n:
        sd[n - 1:] = np.lib.stride_tricks.sliding_window_view(c, n).std(axis=1, ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = upper - lower
    pct_b = np.where(width > 0, (c - lower) / np.where(width > 0, width, 1), 0.5)
    pct_b[np.isnan(mid)] = NAN
    return mid, upper, lower, pct_b


def true_range(high, low, close) -> np.ndarray:
    h, lo, c = _arr(high), _arr(low), _arr(close)
    prev = np.concatenate(([NAN], c[:-1]))
    tr = np.fmax(h - lo, np.fmax(np.abs(h - prev), np.abs(lo - prev)))
    tr[0] = h[0] - lo[0]
    return tr


def atr(high, low, close, n: int = 14) -> np.ndarray:
    tr = true_range(high, low, close)
    out = np.full_like(tr, NAN)
    if len(tr) < n:
        return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def obv(close, volume) -> np.ndarray:
    c, v = _arr(close), _arr(volume)
    direction = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(direction * v)


def ewma_vol(returns, lam: float = 0.94) -> np.ndarray:
    """RiskMetrics EWMA volatility of each return series point, using data up to and including it."""
    r = _arr(returns)
    out = np.full_like(r, NAN)
    var = None
    for i, x in enumerate(r):
        if np.isnan(x):
            continue
        var = x * x if var is None else lam * var + (1 - lam) * x * x
        out[i] = np.sqrt(var)
    return out


def zscore(x, n: int) -> np.ndarray:
    """(x - mean of the last n) / their standard deviation."""
    x = _arr(x)
    mean, sd = sma(x, n), rolling_std(x, n)
    return np.where(sd > 0, (x - mean) / np.where(sd > 0, sd, 1), 0.0) * np.where(np.isnan(mean), NAN, 1.0)


def max_drawdown(equity) -> float:
    """Largest peak-to-trough fall of an equity curve, as a positive fraction."""
    e = _arr(equity)
    if len(e) == 0:
        return 0.0
    peaks = np.maximum.accumulate(e)
    return float(np.max((peaks - e) / peaks))
