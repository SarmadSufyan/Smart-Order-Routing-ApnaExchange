from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.shared.models.risk import (
    KillSwitchActivateRequest,
    KillSwitchState,
    RiskLimits,
    RiskStatusResponse,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _get_risk_engine():
    from backend.gateway.dependencies import get_services

    return get_services().risk_engine


@router.get("/status", response_model=RiskStatusResponse)
async def get_risk_status(user: Annotated[User, Depends(get_current_user)]):
    risk = _get_risk_engine()
    return RiskStatusResponse(
        kill_switch=risk.kill_switch.get_state(),
        positions={
            sym: pos
            for sym, pos in risk.position_tracker.get_all_positions().items()
        },
        total_notional_exposure=risk.position_tracker.get_total_notional(),
        risk_limits=risk.limits,
        recent_checks=risk.recent_checks[-20:],
    )


@router.post(
    "/kill-switch/activate",
    response_model=KillSwitchState,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def activate_kill_switch(
    body: KillSwitchActivateRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    risk = _get_risk_engine()
    order_manager = None
    try:
        from backend.gateway.dependencies import get_services
        order_manager = get_services().order_manager
    except Exception:
        pass

    cancelled = 0
    if order_manager:
        from backend.shared.models.order import OrderStatus
        for oid, order in list(order_manager._orders.items()):
            if order.status in (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED):
                try:
                    await order_manager.cancel_order(oid)
                    cancelled += 1
                except Exception:
                    pass

    state = await risk.kill_switch.activate(
        reason=body.reason,
        activated_by=user.username,
        orders_cancelled=cancelled,
    )
    return state


@router.post(
    "/kill-switch/deactivate",
    response_model=KillSwitchState,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def deactivate_kill_switch(
    user: Annotated[User, Depends(get_current_user)],
):
    risk = _get_risk_engine()
    return await risk.kill_switch.deactivate(deactivated_by=user.username)


@router.put(
    "/limits",
    response_model=RiskLimits,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_risk_limits(
    new_limits: RiskLimits,
    user: Annotated[User, Depends(get_current_user)],
):
    risk = _get_risk_engine()
    risk.limits = new_limits
    return risk.limits
