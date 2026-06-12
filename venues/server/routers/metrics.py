"""
server/routers/metrics.py

GET /metrics → Prometheus-style text format.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics() -> str:
    from server.venue_app import get_engines

    engines = get_engines()
    vid = engines.profile.venue_id
    vol = engines.price_engine.volume
    regime = engines.price_engine.current_regime.value
    degraded = 1 if engines.fill_sim.is_degraded else 0

    lines = [
        f'# HELP venue_volume_total Total shares traded',
        f'# TYPE venue_volume_total counter',
        f'venue_volume_total{{venue="{vid}"}} {vol}',
        f'# HELP venue_degraded Whether venue is in degraded state',
        f'# TYPE venue_degraded gauge',
        f'venue_degraded{{venue="{vid}"}} {degraded}',
        f'# HELP venue_regime Current price regime',
        f'# TYPE venue_regime gauge',
        f'venue_regime{{venue="{vid}",regime="{regime}"}} 1',
        f'# HELP venue_price Current mid price',
        f'# TYPE venue_price gauge',
        f'venue_price{{venue="{vid}"}} {engines.price_engine.current_price:.2f}',
    ]
    return "\n".join(lines)
