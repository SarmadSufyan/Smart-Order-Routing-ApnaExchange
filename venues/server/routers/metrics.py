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
    degraded = 1 if engines.latency._is_degraded else 0

    lines = [
        f'# HELP venue_degraded Whether venue is in degraded state',
        f'# TYPE venue_degraded gauge',
        f'venue_degraded{{venue="{vid}"}} {degraded}',
    ]

    for symbol, se in engines.symbols.items():
        vol = se.price_engine.volume
        regime = se.price_engine.current_regime.value
        price = se.price_engine.current_price

        lines.extend([
            f'venue_volume_total{{venue="{vid}",symbol="{symbol}"}} {vol}',
            f'venue_regime{{venue="{vid}",symbol="{symbol}",regime="{regime}"}} 1',
            f'venue_price{{venue="{vid}",symbol="{symbol}"}} {price:.2f}',
        ])

    return "\n".join(lines)
