from typing import Annotated

from fastapi import APIRouter, Depends

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.wallet import WalletSnapshot

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _wallet():
    from backend.gateway.dependencies import get_services

    return get_services().wallet_service


@router.get("/", response_model=WalletSnapshot)
async def get_wallet(user: Annotated[User, Depends(get_current_user)]):
    """Real balances across every connected exchange, valued in USDT."""
    wallet = _wallet()
    snapshot = wallet.get_snapshot()
    if snapshot.as_of is None:  # first request before the background refresh ran
        snapshot = await wallet.refresh()
    return snapshot


@router.post("/refresh", response_model=WalletSnapshot)
async def refresh_wallet(user: Annotated[User, Depends(get_current_user)]):
    return await _wallet().refresh()
