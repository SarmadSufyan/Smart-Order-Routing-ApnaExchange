from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.shared.models.venue import VenueDetail, VenueHealth, VenueListResponse

router = APIRouter(prefix="/api/venues", tags=["venues"])


def _services():
    from backend.gateway.dependencies import get_services

    return get_services()


def _require_venue(venue_id: str):
    svc = _services()
    adapter = svc.exchange_registry.get(venue_id)
    if adapter is None or not svc.venue_monitor.get_venue_health(venue_id):
        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")
    return svc, adapter


@router.get("/", response_model=VenueListResponse)
async def list_venues(user: Annotated[User, Depends(get_current_user)]):
    return VenueListResponse(venues=_services().venue_monitor.get_all_health())


@router.get("/{venue_id}", response_model=VenueDetail)
async def get_venue(venue_id: str, user: Annotated[User, Depends(get_current_user)]):
    svc, _ = _require_venue(venue_id)
    monitor = svc.venue_monitor
    health = monitor.get_venue_health(venue_id)
    profile = monitor.get_venue_profile(venue_id)
    return VenueDetail(
        venue_id=profile.venue_id,
        name=profile.name,
        cloud=profile.cloud,
        host=profile.host,
        port=profile.port,
        status=health.status,
        health_score=health.health_score,
        current_metrics=monitor.get_venue_metrics(venue_id),
    )


@router.post(
    "/{venue_id}/blacklist",
    response_model=VenueHealth,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def blacklist_venue(venue_id: str):
    svc, _ = _require_venue(venue_id)
    await svc.venue_monitor.blacklist_venue(venue_id)
    return svc.venue_monitor.get_venue_health(venue_id)


@router.post(
    "/{venue_id}/unblacklist",
    response_model=VenueHealth,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def unblacklist_venue(venue_id: str):
    svc, _ = _require_venue(venue_id)
    await svc.venue_monitor.unblacklist_venue(venue_id)
    return svc.venue_monitor.get_venue_health(venue_id)


@router.post(
    "/{venue_id}/degrade",
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def degrade_venue(venue_id: str):
    """Demo control: add artificial latency to OUR connection to the venue.

    A real exchange can't be forced into a degraded state; the health monitor
    measures the injected latency exactly as it would a genuinely slow link.
    """
    _, adapter = _require_venue(venue_id)
    adapter.set_degraded(True)
    return {"venue_id": venue_id, "degraded": True}


@router.post(
    "/{venue_id}/recover",
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def recover_venue(venue_id: str):
    _, adapter = _require_venue(venue_id)
    adapter.set_degraded(False)
    return {"venue_id": venue_id, "degraded": False}
