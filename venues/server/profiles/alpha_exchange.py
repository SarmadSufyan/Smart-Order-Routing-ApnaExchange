"""V1 — AlphaExchange (narrative: AWS us-east-1). Stable all-rounder."""

from server.profiles.base import VenueProfile

profile = VenueProfile(
    venue_id="V1",
    name="AlphaExchange",
    narrative_cloud="AWS",
    narrative_region="us-east-1",
    initial_price=150.00,
    volatility=0.0002,
    drift=0.00001,
    regime_transition_prob=0.005,
    spread_bps=3.0,
    liquidity_depth=300,
    depth_variance=0.25,
    base_latency_ms_range=(5.0, 15.0),
    degraded_latency_multiplier=10.0,
    default_fill_rate=0.96,
    reject_rate=0.03,
    partial_fill_prob=0.08,
    degraded_reject_multiplier=5.0,
)
