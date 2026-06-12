"""
server/routers/health.py

GET /health → venue status for the Venue Health Monitor.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_startup_time: float = time.time()


def reset_startup_time() -> None:
    global _startup_time
    _startup_time = time.time()


class HealthResponse(BaseModel):
    venue_id: str
    name: str
    status: str              # "ok" or "degraded"
    uptime_seconds: int
    current_regime: str


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    from server.venue_app import get_engines

    engines = get_engines()
    degraded = engines.fill_sim.is_degraded

    return HealthResponse(
        venue_id=engines.profile.venue_id,
        name=engines.profile.name,
        status="degraded" if degraded else "ok",
        uptime_seconds=int(time.time() - _startup_time),
        current_regime=engines.price_engine.current_regime.value,
    )
