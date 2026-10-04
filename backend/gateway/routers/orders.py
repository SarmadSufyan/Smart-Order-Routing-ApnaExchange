from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.exceptions import (
    InvalidStateTransitionError,
    KillSwitchActiveError,
    OrderNotFoundError,
)
from backend.shared.models.order import OrderCreate, OrderListResponse, OrderResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_order_manager():
    from backend.gateway.dependencies import get_services

    return get_services().order_manager


@router.post("/", response_model=OrderResponse, status_code=201)
async def submit_order(
    order_in: OrderCreate,
    user: Annotated[User, Depends(get_current_user)],
):
    from backend.gateway.dependencies import get_services

    order, error = await get_services().order_entry.submit(order_in, submitted_by=user.username)
    if isinstance(error, KillSwitchActiveError):
        raise HTTPException(status_code=503, detail="Kill switch is active - all orders rejected")
    if error is not None:
        raise HTTPException(status_code=400, detail=error.message)
    return OrderResponse.from_order(order)


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
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found") from None


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
):
    order_manager = _get_order_manager()
    try:
        order = await order_manager.cancel_order(order_id, reason=f"Cancelled by {user.username}")
        return OrderResponse.from_order(order)
    except OrderNotFoundError:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found") from None
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=e.message) from None
