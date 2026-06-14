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


SYMBOLS = {
    "AAPL": {"initial_price": 150.00, "volatility_mult": 1.0},
    "GOOGL": {"initial_price": 175.00, "volatility_mult": 1.25},
    "MSFT": {"initial_price": 420.00, "volatility_mult": 0.9},
    "AMZN": {"initial_price": 185.00, "volatility_mult": 1.1},
    "TSLA": {"initial_price": 250.00, "volatility_mult": 2.0},
}


class VenueSettings(BaseSettings):
    venue_id: str = "V1"
    venue_name: str = "AlphaExchange"
    venue_port: int = 8001
    venue_profile: str = "alpha_exchange"
    venue_degraded: bool = False
    tick_interval_ms: int = 100

    class Config:
        env_prefix = ""


@lru_cache(maxsize=1)
def get_settings() -> VenueSettings:
    return VenueSettings()


@lru_cache(maxsize=1)
def get_profile() -> VenueProfile:
    """Dynamically import the profile module and return its `profile` object."""
    settings = get_settings()
    module = importlib.import_module(f"server.profiles.{settings.venue_profile}")
    return module.profile
