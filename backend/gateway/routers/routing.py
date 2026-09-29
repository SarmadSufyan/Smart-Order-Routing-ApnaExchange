from typing import Annotated

from fastapi import APIRouter, Depends

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.routing import RoutingStatusResponse

router = APIRouter(prefix="/api/routing", tags=["routing"])


def _get_routing_engine():
    from backend.gateway.dependencies import get_services

    return get_services().routing_engine


@router.get("/status", response_model=RoutingStatusResponse)
async def get_routing_status(user: Annotated[User, Depends(get_current_user)]):
    engine = _get_routing_engine()
    return RoutingStatusResponse(
        active_strategy="best_price",
        available_strategies=["best_price"],
        routable_venues=engine.executable_venues(),
        excluded_venues=engine.excluded_venues(),
        stats=engine.stats,
    )
