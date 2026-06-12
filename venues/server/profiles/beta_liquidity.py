"""V2 — BetaLiquidity (narrative: GCP us-central1). Best prices, fastest, deepest."""

from server.profiles.base import VenueProfile

profile = VenueProfile(
    venue_id="V2",
    name="BetaLiquidity",
    narrative_cloud="GCP",
    narrative_region="us-central1",
    initial_price=150.00,
    volatility=0.0002,
    drift=0.00001,
    regime_transition_prob=0.005,
    spread_bps=1.5,                          # Tightest spread = best prices
    liquidity_depth=500,                     # Deep liquidity
    depth_variance=0.20,
    base_latency_ms_range=(2.0, 8.0),        # Fastest
    degraded_latency_multiplier=10.0,
    default_fill_rate=0.97,
    reject_rate=0.02,
    partial_fill_prob=0.06,
    degraded_reject_multiplier=5.0,
)
