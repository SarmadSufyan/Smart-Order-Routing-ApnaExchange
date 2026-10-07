"""Indicators against hand-computed values; the model finds a real pattern, admits noise, never looks ahead."""

import math
import unittest

import numpy as np

from backend.services.research.indicators import (
    atr,
    bollinger,
    ema,
    ewma_vol,
    log_returns,
    macd,
    max_drawdown,
    obv,
    rsi,
    sma,
)
from backend.services.research.model import (
    WARMUP,
    build_features,
    fit_logistic,
    next_up_labels,
    predict_proba,
    range_coverage,
    range_quantiles,
    score,
    standardized_next_returns,
    walk_forward,
)


def candles(close, spread: float = 0.5):
    c = np.asarray(close, dtype=float)
    o = np.concatenate(([c[0]], c[:-1]))
    return o, np.maximum(o, c) + spread, np.minimum(o, c) - spread, c


class IndicatorTest(unittest.TestCase):
    def test_sma_and_ema(self) -> None:
        x = [1, 2, 3, 4, 5]
        np.testing.assert_allclose(sma(x, 3), [np.nan, np.nan, 2, 3, 4])
        # EMA(3): alpha 0.5, seeded with the SMA of the first 3 (=2): 2, 3, 4
        np.testing.assert_allclose(ema(x, 3), [np.nan, np.nan, 2, 3, 4])

    def test_rsi_extremes_and_balance(self) -> None:
        self.assertEqual(rsi(list(range(1, 30)), 14)[-1], 100.0)  # only gains
        self.assertEqual(rsi(list(range(30, 1, -1)), 14)[-1], 0.0)  # only losses
        zigzag = [100 + (i % 2) for i in range(40)]  # equal gains and losses
        self.assertAlmostEqual(rsi(zigzag, 14)[-1], 50.0, delta=4)
        self.assertTrue(np.isnan(rsi([1, 2, 3], 14)).all())

    def test_macd_histogram_is_line_minus_signal(self) -> None:
        c = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200))
        line, sig, hist = macd(c)
        ok = ~np.isnan(sig)
        np.testing.assert_allclose(hist[ok], line[ok] - sig[ok])
        self.assertTrue(np.isnan(sig[:33]).all())  # 26 + 9 - 2 candles of warm-up

    def test_bollinger_percent_b(self) -> None:
        c = [10.0] * 19 + [10.0]  # flat: zero width -> %B is 0.5
        self.assertEqual(bollinger(c)[3][-1], 0.5)
        c = list(np.linspace(10, 11, 20))
        mid, upper, lower, pct_b = bollinger(c)
        self.assertAlmostEqual(mid[-1], 10.5)
        self.assertAlmostEqual(pct_b[-1], (11 - lower[-1]) / (upper[-1] - lower[-1]))

    def test_atr_is_the_average_true_range(self) -> None:
        h, lo, c = [11, 12, 13], [9, 10, 11], [10, 11, 12]  # every true range is 2
        self.assertAlmostEqual(atr(h, lo, c, 2)[-1], 2.0)
        # a gap: true range uses the previous close: max(20-19, |20-10|, |19-10|) = 10
        self.assertAlmostEqual(atr([10, 20], [9, 19], [10, 20], 1)[-1], 10.0)

    def test_obv_ewma_and_drawdown(self) -> None:
        np.testing.assert_allclose(obv([1, 2, 1, 1], [5, 3, 2, 7]), [0, 3, 1, 1])
        vol = ewma_vol([np.nan, 0.01, 0.01, 0.01])
        self.assertAlmostEqual(vol[-1], 0.01)
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 130]), 0.25)
        np.testing.assert_allclose(log_returns([1, math.e])[1:], [1.0])


class ModelTest(unittest.TestCase):
    def series(self, n: int, signal: float, seed: int = 7):
        """Random walk where a strong last-move (|r| large) tends to continue with strength ``signal``."""
        rng = np.random.default_rng(seed)
        r = rng.normal(0, 0.01, n)
        for t in range(1, n):
            r[t] += signal * r[t - 1]
        close = 100 * np.exp(np.cumsum(r))
        o, h, lo, c = candles(close, spread=0.05)
        ts = np.arange(n) * 3_600_000
        return o, h, lo, c, np.full(n, 10.0) + rng.random(n), ts

    def test_finds_a_real_pattern_out_of_sample(self) -> None:
        o, h, lo, c, v, ts = self.series(3000, signal=0.45)
        X, y = build_features(o, h, lo, c, v, ts, "1h"), next_up_labels(c)
        s = score(walk_forward(X, y), y)
        self.assertTrue(s.edge)
        self.assertGreater(s.accuracy, s.baseline_accuracy + 0.05)
        self.assertGreater(s.brier_skill, 0)

    def test_admits_pure_noise_has_no_edge(self) -> None:
        o, h, lo, c, v, ts = self.series(3000, signal=0.0, seed=3)
        X, y = build_features(o, h, lo, c, v, ts, "1h"), next_up_labels(c)
        s = score(walk_forward(X, y), y)
        self.assertLess(abs(s.accuracy - s.baseline_accuracy), 0.04)
        self.assertLess(s.brier_skill, 0.01)

    def test_features_never_use_future_candles(self) -> None:
        o, h, lo, c, v, ts = self.series(600, signal=0.2)
        full = build_features(o, h, lo, c, v, ts, "1h")
        cut = build_features(o[:400], h[:400], lo[:400], c[:400], v[:400], ts[:400], "1h")
        np.testing.assert_allclose(full[:400], cut, equal_nan=True)
        self.assertTrue(np.isnan(full[:WARMUP]).all())

    def test_walk_forward_only_predicts_after_its_first_training_window(self) -> None:
        o, h, lo, c, v, ts = self.series(1500, signal=0.2)
        X, y = build_features(o, h, lo, c, v, ts, "1h"), next_up_labels(c)
        p = walk_forward(X, y, first_train_frac=0.6)
        rows = np.where(~np.isnan(X).any(axis=1) & ~np.isnan(y))[0]
        first_pred = rows[int(len(rows) * 0.6)]
        self.assertTrue(np.isnan(p[:first_pred]).all())
        self.assertFalse(np.isnan(p[first_pred:rows[-1] + 1][~np.isnan(y[first_pred:rows[-1] + 1])]).any())

    def test_logistic_regression_recovers_a_simple_rule(self) -> None:
        rng = np.random.default_rng(0)
        X = rng.normal(size=(2000, 2))
        y = (X[:, 0] + 0.3 * rng.normal(size=2000) > 0).astype(float)
        m = fit_logistic(X, y)
        self.assertGreater(m.coef[1], 2)  # the informative feature
        self.assertLess(abs(m.coef[2]), 0.3)  # the noise feature
        self.assertGreater(predict_proba(m, np.array([[1.0, 0.0]]))[0], 0.9)

    def test_score_baselines(self) -> None:
        y = np.array([1.0, 0.0] * 50)
        s = score(np.full(100, 0.5), y)
        self.assertEqual((s.accuracy, s.baseline_accuracy), (0.5, 0.5))
        self.assertAlmostEqual(s.brier_skill, 0.0)
        self.assertFalse(s.edge)
        self.assertIsNone(score(np.full(10, 0.5), y[:10]))

    def test_range_from_normal_moves_covers_about_80_percent(self) -> None:
        rng = np.random.default_rng(5)
        r = np.concatenate(([np.nan], rng.normal(0, 0.01, 4000)))
        z = standardized_next_returns(r, ewma_vol(r))
        q = range_quantiles(z)
        self.assertAlmostEqual(q[0.10], -1.28, delta=0.15)
        self.assertAlmostEqual(q[0.90], 1.28, delta=0.15)
        self.assertAlmostEqual(range_coverage(z), 0.8, delta=0.04)


if __name__ == "__main__":
    unittest.main()
