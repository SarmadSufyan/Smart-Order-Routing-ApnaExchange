"""V3 — GammaMarkets (narrative: Azure eastus). DEGRADED — demo target for blacklisting."""

from server.profiles.base import VenueProfile

profile = VenueProfile(
    venue_id="V3",
    name="GammaMarkets",
    narrative_cloud="Azure",
    narrative_region="eastus",
    initial_price=150.00,
    volatility=0.0004,                       # Higher vol = noisier
    drift=0.00001,
    regime_transition_prob=0.015,            # Regimes flip more often
    spread_bps=2.0,                          # Looks attractive on price…
    liquidity_depth=400,
    depth_variance=0.30,
    base_latency_ms_range=(50.0, 200.0),     # …but terrible latency
    degraded_latency_multiplier=4.0,
    default_fill_rate=0.62,                  # Poor fill quality
    reject_rate=0.30,                        # 30% reject = BLACKLIST trigger
    partial_fill_prob=0.35,
    degraded_reject_multiplier=2.5,
)
