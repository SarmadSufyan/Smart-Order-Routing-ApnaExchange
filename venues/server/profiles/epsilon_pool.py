"""V5 — EpsilonPool (narrative: GCP asia-southeast1). Dark pool, balanced."""

from server.profiles.base import VenueProfile

profile = VenueProfile(
    venue_id="V5",
    name="EpsilonPool",
    narrative_cloud="GCP",
    narrative_region="asia-southeast1",
    initial_price=150.00,
    volatility=0.00025,
    drift=0.00001,
    regime_transition_prob=0.006,
    spread_bps=5.0,
    liquidity_depth=100,                     # Dark pool, mid-depth
    depth_variance=0.35,                     # Variable liquidity
    base_latency_ms_range=(10.0, 25.0),
    degraded_latency_multiplier=8.0,
    default_fill_rate=0.93,
    reject_rate=0.04,
    partial_fill_prob=0.12,
    degraded_reject_multiplier=4.0,
)
