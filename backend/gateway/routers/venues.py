from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.shared.models.venue import VenueDetail, VenueHealth, VenueListResponse

router = APIRouter(prefix="/api/venues", tags=["venues"])


def _get_venue_monitor():
    from backend.gateway.dependencies import get_services

    return get_services().venue_monitor


@router.get("/", response_model=VenueListResponse)
async def list_venues(user: Annotated[User, Depends(get_current_user)]):
    monitor = _get_venue_monitor()
    return VenueListResponse(venues=monitor.get_all_health())


@router.get("/{venue_id}", response_model=VenueDetail)
async def get_venue(venue_id: str, user: Annotated[User, Depends(get_current_user)]):
    monitor = _get_venue_monitor()
    health = monitor.get_venue_health(venue_id)
    profile = monitor.get_venue_profile(venue_id)
    metrics = monitor.get_venue_metrics(venue_id)

    if not health or not profile:
        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")

    return VenueDetail(
        venue_id=profile.venue_id,
        name=profile.name,
        cloud=profile.cloud,
        host=profile.host,
        port=profile.port,
        status=health.status,
        health_score=health.health_score,
        current_metrics=metrics,
    )


@router.post(
    "/{venue_id}/blacklist",
    response_model=VenueHealth,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def blacklist_venue(venue_id: str):
    monitor = _get_venue_monitor()
    if not monitor.get_venue_health(venue_id):
        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")
    await monitor.blacklist_venue(venue_id)
    return monitor.get_venue_health(venue_id)


@router.post(
    "/{venue_id}/unblacklist",
    response_model=VenueHealth,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def unblacklist_venue(venue_id: str):
    monitor = _get_venue_monitor()
    if not monitor.get_venue_health(venue_id):
        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")
    await monitor.unblacklist_venue(venue_id)
    return monitor.get_venue_health(venue_id)
