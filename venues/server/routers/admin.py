"""
server/routers/admin.py

POST /admin/degrade → force venue into degraded mode.
POST /admin/recover → restore normal operation.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import structlog

router = APIRouter(prefix="/admin", tags=["admin"])
logger = structlog.get_logger()


class DegradeResponse(BaseModel):
    degraded: bool


@router.post("/degrade", response_model=DegradeResponse)
async def admin_degrade() -> DegradeResponse:
    from server.venue_app import get_engines

    engines = get_engines()
    engines.latency.set_degraded(True)
    for se in engines.symbols.values():
        se.fill_sim.set_degraded(True)

    logger.warning("admin.degraded", venue_id=engines.profile.venue_id)
    return DegradeResponse(degraded=True)


@router.post("/recover", response_model=DegradeResponse)
async def admin_recover() -> DegradeResponse:
    from server.venue_app import get_engines

    engines = get_engines()
    engines.latency.set_degraded(False)
    for se in engines.symbols.values():
        se.fill_sim.set_degraded(False)

    logger.info("admin.recovered", venue_id=engines.profile.venue_id)
    return DegradeResponse(degraded=False)
