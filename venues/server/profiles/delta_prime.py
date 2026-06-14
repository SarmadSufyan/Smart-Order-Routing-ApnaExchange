"""V4 — DeltaPrime (narrative: AWS eu-west-1). Premium venue, lowest latency, thin books."""

from server.profiles.base import VenueProfile

profile = VenueProfile(
    venue_id="V4",
    name="DeltaPrime",
    narrative_cloud="AWS",
    narrative_region="eu-west-1",
    initial_price=150.00,
    volatility=0.0002,
    drift=0.00001,
    regime_transition_prob=0.004,
    spread_bps=4.5,                          # Wider spread = premium fee venue
    liquidity_depth=50,                      # Premium venue, thin top-of-book
    depth_variance=0.20,
    base_latency_ms_range=(1.0, 5.0),        # Lowest latency
    degraded_latency_multiplier=10.0,
    default_fill_rate=0.98,                  # Best execution quality
    reject_rate=0.015,
    partial_fill_prob=0.04,
    degraded_reject_multiplier=5.0,
)
