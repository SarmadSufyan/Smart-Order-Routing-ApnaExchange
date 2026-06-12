"""
server/engine/latency_model.py

Injects the venue's characteristic latency into every endpoint call
using asyncio.sleep() (NOT time.sleep — that would block the event loop).

When degraded, latency is multiplied by the profile's degraded_latency_multiplier.
"""

from __future__ import annotations

import asyncio
import random
import time

from server.profiles.base import VenueProfile


class LatencyModel:
    """
    Call `await inject()` at the start of every endpoint handler.
    Returns the actual injected latency in milliseconds.
    """

    def __init__(self, profile: VenueProfile) -> None:
        self._profile = profile
        self._is_degraded: bool = False

    def set_degraded(self, degraded: bool) -> None:
        self._is_degraded = degraded

    async def inject(self) -> float:
        """
        Sleep for a random duration drawn from the venue's latency range.
        Returns the actual latency in milliseconds.
        """
        lo, hi = self._profile.base_latency_ms_range
        if self._is_degraded:
            lo *= self._profile.degraded_latency_multiplier
            hi *= self._profile.degraded_latency_multiplier

        latency_ms = random.uniform(lo, hi)
        await asyncio.sleep(latency_ms / 1000.0)
        return round(latency_ms, 2)
