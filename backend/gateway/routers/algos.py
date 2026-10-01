from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, get_current_user
from backend.services.algos import AlgoNotFoundError, AlgoRejectedError
from backend.shared.models.strategy import AlgoList, TwapCreate, TwapState

router = APIRouter(prefix="/api/algos", tags=["algos"])


def _twap():
    from backend.gateway.dependencies import get_services

    return get_services().twap_engine


async def _control(action, algo_id: str, user: User) -> TwapState:
    try:
        return await action(algo_id, user.username)
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except AlgoRejectedError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc


@router.get("/", response_model=AlgoList)
async def list_algos(user: Annotated[User, Depends(get_current_user)]):
    """Algo (parent) orders: running and paused first, then the most recent."""
    return AlgoList(algos=_twap().list())


@router.post("/twap", response_model=TwapState, status_code=201)
async def start_twap(body: TwapCreate, user: Annotated[User, Depends(get_current_user)]):
    """Slice a parent order evenly over a time window.

    Every slice is an ordinary order through the same entry point as the order
    button — same pre-trade risk, funding gate, routing and price protection.
    """
    try:
        return await _twap().create(body, user.username)
    except AlgoRejectedError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/{algo_id}", response_model=TwapState)
async def get_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    try:
        return _twap().get(algo_id)
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.post("/{algo_id}/pause", response_model=TwapState)
async def pause_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_twap().pause, algo_id, user)


@router.post("/{algo_id}/resume", response_model=TwapState)
async def resume_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_twap().resume, algo_id, user)


@router.post("/{algo_id}/cancel", response_model=TwapState)
async def cancel_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_twap().cancel, algo_id, user)
