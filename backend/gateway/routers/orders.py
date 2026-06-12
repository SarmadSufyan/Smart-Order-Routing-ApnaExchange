from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.shared.exceptions import (
    DomainError,
    InvalidStateTransitionError,
    KillSwitchActiveError,
    OrderNotFoundError,
    RiskCheckFailedError,
)
from backend.shared.models.order import OrderCreate, OrderListResponse, OrderResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_order_manager():
    from backend.gateway.dependencies import get_services

    return get_services().order_manager


def _get_routing_engine():
    from backend.gateway.dependencies import get_services

    return get_services().routing_engine


@router.post("/", response_model=OrderResponse, status_code=201)
async def submit_order(
    order_in: OrderCreate,
    user: Annotated[User, Depends(get_current_user)],
):
    order_manager = _get_order_manager()
    routing_engine = _get_routing_engine()

    order = await order_manager.create_order(order_in)
    try:
        result = await routing_engine.route_order(order)
        updated = order_manager.get_order(order.id)
        return OrderResponse.from_order(updated)
    except KillSwitchActiveError:
        raise HTTPException(status_code=503, detail="Kill switch is active - all orders rejected")
    except RiskCheckFailedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/", response_model=OrderListResponse)
async def list_orders(
    user: Annotated[User, Depends(get_current_user)],
    status: str | None = Query(None),
    symbol: str | None = Query(None),
    side: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    order_manager = _get_order_manager()
    return order_manager.list_orders(
        status=status, symbol=symbol, side=side, page=page, page_size=page_size
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
):
    order_manager = _get_order_manager()
    try:
        order = order_manager.get_order(order_id)
        return OrderResponse.from_order(order)
    except OrderNotFoundError:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
):
    order_manager = _get_order_manager()
    try:
        order = await order_manager.cancel_order(order_id)
        return OrderResponse.from_order(order)
    except OrderNotFoundError:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=e.message)
