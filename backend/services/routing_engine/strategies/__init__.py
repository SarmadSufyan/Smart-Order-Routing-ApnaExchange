"""Routing strategies — how ONE order is split across venues (the "where")."""

from backend.shared.models.order import RoutingStrategy as StrategyName

from .base import RoutingStrategy
from .best_price import BestPriceStrategy
from .smart import SmartStrategy
from .sweep import DEFAULT_BAND_BPS, LiquiditySweepStrategy


def build_strategies(sweep_band_bps: float = DEFAULT_BAND_BPS) -> dict[StrategyName, RoutingStrategy]:
    """One instance of every strategy, keyed by the name an order asks for."""
    return {
        StrategyName.BEST_PRICE: BestPriceStrategy(),
        StrategyName.LIQUIDITY_SWEEP: LiquiditySweepStrategy(sweep_band_bps),
        StrategyName.SMART: SmartStrategy(),
    }


__all__ = ["BestPriceStrategy", "LiquiditySweepStrategy", "RoutingStrategy", "SmartStrategy", "build_strategies"]
