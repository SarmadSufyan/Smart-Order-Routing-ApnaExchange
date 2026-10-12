from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.shared.events.event_bus import audit
from backend.shared.models.risk import (
    KillSwitchActivateRequest,
    KillSwitchState,
    PreflightRequest,
    PreflightResponse,
    RiskLimits,
    RiskStatusResponse,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _services():
    from backend.gateway.dependencies import get_services

    return get_services()


@router.get("/status", response_model=RiskStatusResponse)
async def get_risk_status(user: Annotated[User, Depends(get_current_user)]):
    svc = _services()
    risk = svc.risk_engine
    return RiskStatusResponse(
        kill_switch=risk.kill_switch.get_state(),
        # Net traded by ApnaSOR since the P&L baseline (holdings are on the Wallet page).
        positions=dict(risk.position_tracker.get_all_positions()),
        total_notional_exposure=risk.position_tracker.get_total_notional(),
        risk_limits=risk.limits,
        recent_checks=risk.recent_checks[-20:],
        portfolio=svc.risk_monitor.state,
        real_money_ceilings=risk.ceilings,
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
    # Halts trading here at once; with RAFT on it is also replicated (see RiskControls).
    return await _services().risk_controls.engage_kill_switch(body.reason, operator=user.username)


@router.post(
    "/kill-switch/deactivate",
    response_model=KillSwitchState,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def deactivate_kill_switch(
    user: Annotated[User, Depends(get_current_user)],
):
    svc = _services()
    if blocker := svc.risk_monitor.deactivation_blocker():
        await audit("kill_switch_release_refused", f"Release refused for {user.username}: {blocker}",
                    actor=user.username)
        raise HTTPException(status_code=409, detail=blocker)
    # With RAFT on, a release needs a majority of the cluster (503 otherwise).
    return await svc.risk_controls.release_kill_switch(operator=user.username)


@router.put(
    "/limits",
    response_model=RiskLimits,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_risk_limits(
    new_limits: RiskLimits,
    user: Annotated[User, Depends(get_current_user)],
):
    svc = _services()
    limits = await svc.risk_controls.set_limits(new_limits, actor=user.username)
    await svc.risk_monitor.evaluate()  # a raised loss limit takes effect immediately
    return limits


@router.post("/preflight", response_model=PreflightResponse)
async def preflight_orders(body: PreflightRequest, user: Annotated[User, Depends(get_current_user)]):
    """Check a basket of orders against pre-trade risk WITHOUT sending anything.

    Each order meets the position it would face if every earlier order in the
    basket had filled. ``stress: true`` adds a Monte Carlo price-risk test per
    order (CPU-heavy: runs on the worker-process pool when the basket is big
    enough to pay for it — ``executor`` in the answer says which ran).
    """
    return await _services().preflight.run(body, actor=user.username)
