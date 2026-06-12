from fastapi import APIRouter, Depends

from backend.gateway.middleware.auth import UserRole, require_role

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


def _get_venue_monitor():
    from backend.gateway.dependencies import get_services

    return get_services().venue_monitor


def _get_ws_manager():
    from backend.gateway.dependencies import get_services

    return get_services().ws_manager


@router.post("/venues/{venue_id}/degrade")
async def degrade_venue(venue_id: str):
    monitor = _get_venue_monitor()
    health = monitor.get_venue_health(venue_id)
    if not health:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")

    from backend.shared.models.venue import VenueStatus

    health.status = VenueStatus.DEGRADED
    health.health_score = 0.4
    return {"venue_id": venue_id, "status": "DEGRADED", "message": "Venue manually degraded"}


@router.post("/venues/{venue_id}/recover")
async def recover_venue(venue_id: str):
    monitor = _get_venue_monitor()
    health = monitor.get_venue_health(venue_id)
    if not health:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Venue {venue_id} not found")

    from backend.shared.models.venue import VenueStatus

    health.status = VenueStatus.HEALTHY
    health.health_score = 0.9
    return {"venue_id": venue_id, "status": "HEALTHY", "message": "Venue manually recovered"}


@router.get("/metrics")
async def get_system_metrics():
    ws = _get_ws_manager()
    monitor = _get_venue_monitor()
    return {
        "websocket_connections": ws.connection_count,
        "venues": {
            h.venue_id: {
                "status": h.status.value,
                "health_score": h.health_score,
            }
            for h in monitor.get_all_health()
        },
    }
