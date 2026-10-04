from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.arbitrage import ArbStatus, ArbWindowList

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])


def _detector():
    from backend.gateway.dependencies import get_services

    return get_services().arbitrage


@router.get("/status", response_model=ArbStatus)
async def arbitrage_status(user: Annotated[User, Depends(get_current_user)]):
    """Cross-venue edges right now (best direction per symbol, gross and after
    taker fees, how much our accounts could fund), open windows, and the
    statistics since ``stats_since``. Detection only — nothing is traded."""
    return _detector().status()


@router.get("/windows", response_model=ArbWindowList)
async def arbitrage_windows(
    user: Annotated[User, Depends(get_current_user)],
    symbol: Annotated[str | None, Query(description="e.g. BTC/USDT; empty = every symbol")] = None,
    profitable_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Recent windows (a direction that stayed crossed over consecutive scans),
    newest first, plus the ones still open. ``profitable_only``: only windows
    whose edge after taker fees was above ``ARB_MIN_NET_BPS``."""
    return _detector().windows(symbol.upper() if symbol else None, profitable_only, limit)
