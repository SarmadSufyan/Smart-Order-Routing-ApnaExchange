"""
server/config.py

Pydantic Settings that load from environment variables.
The profile module is dynamically imported based on VENUE_PROFILE.
"""

from __future__ import annotations

import importlib
from functools import lru_cache

from pydantic_settings import BaseSettings

from server.profiles.base import VenueProfile


class VenueSettings(BaseSettings):
    venue_id: str = "V1"
    venue_name: str = "AlphaExchange"
    venue_port: int = 8001
    venue_profile: str = "alpha_exchange"  # module name under server.profiles
    venue_degraded: bool = False           # V3 ships with this = True on its EC2
    symbol: str = "AAPL"
    tick_interval_ms: int = 100            # GBM ticks every 100ms

    class Config:
        env_prefix = ""                    # no prefix — read VENUE_ID directly


@lru_cache(maxsize=1)
def get_settings() -> VenueSettings:
    return VenueSettings()


@lru_cache(maxsize=1)
def get_profile() -> VenueProfile:
    """Dynamically import the profile module and return its `profile` object."""
    settings = get_settings()
    module = importlib.import_module(f"server.profiles.{settings.venue_profile}")
    return module.profile
