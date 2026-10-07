from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.gateway.middleware.auth import User, get_current_user
from backend.services.algos import AlgoNotFoundError, AlgoRejectedError
from backend.shared.models.strategy import (
    AlgoCreate,
    AlgoKind,
    AlgoList,
    AlgoRecommendation,
    AlgoState,
    RecommendRequest,
)

router = APIRouter(prefix="/api/algos", tags=["algos"])


def _engine():
    from backend.gateway.dependencies import get_services

    return get_services().algo_engine


async def _control(action, algo_id: str, user: User) -> AlgoState:
    try:
        return await action(algo_id, user.username)
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except AlgoRejectedError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc


async def _start(body: AlgoCreate, user: User) -> AlgoState:
    try:
        return await _engine().create(body, user.username)
    except AlgoRejectedError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/", response_model=AlgoList)
async def list_algos(user: Annotated[User, Depends(get_current_user)]):
    """Algo (parent) orders: running and paused first, then the most recent."""
    return AlgoList(algos=_engine().list())


@router.post("/", response_model=AlgoState, status_code=201)
async def start_algo(body: AlgoCreate, user: Annotated[User, Depends(get_current_user)]):
    """Start an execution algorithm: TWAP, VWAP, IS (implementation shortfall), POV or ICEBERG.

    Every child is an ordinary order through the same entry point as the order
    button — same pre-trade risk, funding gate, routing (``routing``) and price
    protection. Required fields depend on ``kind``: TWAP / VWAP / IS need
    ``slices``; POV needs ``participation_pct``; ICEBERG needs ``limit_price``
    and ``display_qty``.
    """
    return await _start(body, user)


@router.post("/twap", response_model=AlgoState, status_code=201)
async def start_twap(body: AlgoCreate, user: Annotated[User, Depends(get_current_user)]):
    """Slice a parent order evenly over a time window (kept for older clients: same as kind=TWAP)."""
    return await _start(body.model_copy(update={"kind": AlgoKind.TWAP}), user)


@router.post("/recommend", response_model=AlgoRecommendation)
async def recommend_algo(body: RecommendRequest, user: Annotated[User, Depends(get_current_user)]):
    """Auto: one order now, or which algorithm over how long — with the reasons and the numbers.

    Nothing is sent. Start the recommendation with ``POST /api/algos/`` (its
    ``params``) or, for ``SINGLE``, as one order (``POST /api/orders/``).
    """
    try:
        return await _engine().recommend(body)
    except AlgoRejectedError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/{algo_id}", response_model=AlgoState)
async def get_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    try:
        return _engine().get(algo_id)
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.post("/{algo_id}/pause", response_model=AlgoState)
async def pause_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_engine().pause, algo_id, user)


@router.post("/{algo_id}/resume", response_model=AlgoState)
async def resume_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_engine().resume, algo_id, user)


@router.post("/{algo_id}/cancel", response_model=AlgoState)
async def cancel_algo(algo_id: str, user: Annotated[User, Depends(get_current_user)]):
    return await _control(_engine().cancel, algo_id, user)
