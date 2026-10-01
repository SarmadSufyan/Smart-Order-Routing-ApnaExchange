from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.services.wallet import BaselineBusyError
from backend.shared.models.portfolio import BaselineResetRequest, PortfolioPnL
from backend.shared.models.wallet import WalletSnapshot

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _services():
    from backend.gateway.dependencies import get_services

    return get_services()


@router.get("/", response_model=WalletSnapshot)
async def get_wallet(user: Annotated[User, Depends(get_current_user)]):
    """Real balances across every connected exchange, valued in USDT."""
    wallet = _services().wallet_service
    snapshot = wallet.get_snapshot()
    if snapshot.as_of is None:  # first request before the background refresh ran
        snapshot = await wallet.refresh()
    return snapshot


@router.post("/refresh", response_model=WalletSnapshot)
async def refresh_wallet(user: Annotated[User, Depends(get_current_user)]):
    return await _services().wallet_service.refresh()


@router.get("/pnl", response_model=PortfolioPnL)
async def get_pnl(user: Annotated[User, Depends(get_current_user)]):
    """Holdings, cost and P&L from our own records, reconciled against the exchanges."""
    return _services().pnl_service.compute()


@router.post(
    "/baseline",
    response_model=PortfolioPnL,
    dependencies=[Depends(require_role(UserRole.RISK_MANAGER, UserRole.ADMIN))],
)
async def reset_baseline(body: BaselineResetRequest, user: Annotated[User, Depends(get_current_user)]):
    """Accept the exchanges' current balances as the new opening balances.

    Use after a reconciliation break (testnet reset, manual trade, deposit).
    P&L and net-traded positions restart from here; history stays in the audit log.
    """
    pnl = _services().pnl_service
    try:
        await pnl.rebaseline(body.venue_id, actor=user.username)
    except BaselineBusyError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return pnl.compute()
