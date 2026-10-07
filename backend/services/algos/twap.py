"""TWAP — kept as an import path: the engine now runs every algo kind.

TWAP was the first execution algorithm (2026-10-01). Since 2026-10-07 one
engine (``engine.py``) works every kind — TWAP, VWAP, IS, POV, ICEBERG — and
the pure maths lives in ``schedules.py``. This module re-exports the names
older code and tests import from here.
"""

from .engine import AlgoEngine, AlgoNotFoundError, AlgoRejectedError, TwapEngine
from .schedules import MAX_CATCHUP, cost_bps, plan_schedule, recompute_metrics, slice_quantity

__all__ = [
    "MAX_CATCHUP",
    "AlgoEngine",
    "AlgoNotFoundError",
    "AlgoRejectedError",
    "TwapEngine",
    "cost_bps",
    "plan_schedule",
    "recompute_metrics",
    "slice_quantity",
]
