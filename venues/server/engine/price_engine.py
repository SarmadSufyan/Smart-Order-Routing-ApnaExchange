"""
server/engine/price_engine.py

Geometric Brownian Motion price engine with regime-switching.
Ticks every 100ms (configurable). Uses numpy for random draws.

Regimes:
  NORMAL   — standard mu/sigma
  TRENDING — doubled drift
  VOLATILE — tripled sigma
  MEAN_REV — drift pulls toward initial price
"""

from __future__ import annotations

import asyncio
import math
import time
from enum import Enum

import numpy as np
import structlog

from server.profiles.base import VenueProfile

logger = structlog.get_logger()


class Regime(str, Enum):
    NORMAL = "NORMAL"
    TRENDING = "TRENDING"
    VOLATILE = "VOLATILE"
    MEAN_REVERTING = "MEAN_REVERTING"


# Markov transition matrix: P(next_regime | current_regime)
# Rows = current, cols = [NORMAL, TRENDING, VOLATILE, MEAN_REV]
_TRANSITION = {
    Regime.NORMAL:         [0.92, 0.03, 0.03, 0.02],
    Regime.TRENDING:       [0.15, 0.80, 0.03, 0.02],
    Regime.VOLATILE:       [0.10, 0.02, 0.83, 0.05],
    Regime.MEAN_REVERTING: [0.20, 0.02, 0.03, 0.75],
}
_REGIMES = [Regime.NORMAL, Regime.TRENDING, Regime.VOLATILE, Regime.MEAN_REVERTING]


class PriceEngine:
    """
    Per-venue GBM price engine. Each venue instance owns one.
    All state is in-memory; nothing persists across restarts.
    """

    def __init__(
        self,
        profile: VenueProfile,
        initial_price: float | None = None,
        volatility_mult: float = 1.0,
    ) -> None:
        self._profile = profile
        price = initial_price if initial_price is not None else profile.initial_price
        self._price: float = price
        self._initial_price: float = price
        self._volatility_mult: float = volatility_mult
        self._regime: Regime = Regime.NORMAL
        self._rng = np.random.default_rng()
        self._tick_count: int = 0
        self._last_price: float = price
        self._volume: int = 0
        self._running: bool = False
        self._task: asyncio.Task | None = None

    # ── Public reads (no lock needed — single writer via asyncio) ─────────

    @property
    def current_price(self) -> float:
        return self._price

    @property
    def last_price(self) -> float:
        return self._last_price

    @property
    def current_regime(self) -> Regime:
        return self._regime

    @property
    def volume(self) -> int:
        return self._volume

    def record_trade(self, qty: int, price: float) -> None:
        """Called by fill_simulator when a fill happens."""
        self._last_price = price
        self._volume += qty

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        interval_s = self._profile.regime_transition_prob  # unused; use config
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("price_engine.started",
                     venue_id=self._profile.venue_id,
                     initial_price=self._price,
                     tick_ms=100)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Core loop ─────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        interval_s = 0.1  # 100ms
        while self._running:
            start = asyncio.get_event_loop().time()
            self._tick()
            elapsed = asyncio.get_event_loop().time() - start
            await asyncio.sleep(max(0.0, interval_s - elapsed))

    def _tick(self) -> None:
        self._tick_count += 1
        self._maybe_switch_regime()

        mu, sigma = self._regime_params()
        Z = self._rng.standard_normal()
        log_return = mu + sigma * Z
        self._price *= math.exp(log_return)

        # Belt-and-suspenders: never go negative or unreasonably far
        self._price = max(0.01, self._price)

    def _regime_params(self) -> tuple[float, float]:
        """Return (drift_per_tick, vol_per_tick) adjusted for current regime."""
        base_mu = self._profile.drift
        base_sigma = self._profile.volatility * self._volatility_mult

        if self._regime == Regime.NORMAL:
            return base_mu, base_sigma
        elif self._regime == Regime.TRENDING:
            return base_mu * 3.0, base_sigma
        elif self._regime == Regime.VOLATILE:
            return base_mu, base_sigma * 3.0
        elif self._regime == Regime.MEAN_REVERTING:
            # Pull toward initial price
            reversion = 0.001 * (self._initial_price - self._price) / self._initial_price
            return base_mu + reversion, base_sigma * 0.7
        return base_mu, base_sigma

    def _maybe_switch_regime(self) -> None:
        """Markov chain regime transition."""
        if self._rng.random() >= self._profile.regime_transition_prob:
            return  # No transition this tick
        probs = _TRANSITION[self._regime]
        # numpy.choice returns numpy.str_, so index manually instead
        cumulative = 0.0
        roll = float(self._rng.random())
        for i, p in enumerate(probs):
            cumulative += p
            if roll < cumulative:
                self._regime = _REGIMES[i]
                return
        self._regime = _REGIMES[-1]
