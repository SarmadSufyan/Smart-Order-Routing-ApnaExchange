"""
server/profiles/base.py

Schema for venue personality profiles.
Every venue provides an instance of VenueProfile with its own numbers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueProfile:
    venue_id: str
    name: str
    narrative_cloud: str              # "AWS" / "GCP" / "Azure" — for panel only
    narrative_region: str             # e.g. "us-east-1"

    # ── Price engine ─────────────────────────────────────────────────────
    initial_price: float              # Starting mid-price for the symbol
    volatility: float                 # Per-tick sigma for GBM (NOT annualized)
    drift: float                      # Per-tick mu for GBM
    regime_transition_prob: float     # P(regime switch) per tick

    # ── Spread & liquidity ───────────────────────────────────────────────
    spread_bps: float                 # Half-spread in basis points
    liquidity_depth: int              # Base top-of-book size (shares)
    depth_variance: float             # ±% random variation on sizes

    # ── Latency ──────────────────────────────────────────────────────────
    base_latency_ms_range: tuple[float, float]   # (min, max) for uniform draw
    degraded_latency_multiplier: float           # When degraded, multiply by this

    # ── Execution behavior ───────────────────────────────────────────────
    default_fill_rate: float          # Mean fraction filled (0.0–1.0)
    reject_rate: float                # P(order rejected)
    partial_fill_prob: float          # P(partial fill instead of full)
    degraded_reject_multiplier: float # When degraded, multiply reject_rate by this
