"""The trade desk (research R1): scanner, entry timer, time machine, plan. Read-only: nothing here can place an order."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.gateway.middleware.auth import User, get_current_user
from backend.services.research.service import ResearchUnavailableError

router = APIRouter(prefix="/api/trade-desk", tags=["trade-desk"])

Timeframe = Literal["5m", "1h", "1d"]


def _desk():
    from backend.gateway.dependencies import get_services

    return get_services().trade_desk


async def _call(coro):
    try:
        return await coro
    except ResearchUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class TimeMachineRequest(BaseModel):
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: Timeframe = "1h"
    entry: float = Field(..., gt=0)
    stop: float = Field(..., gt=0, description="Below the entry (a long bracket)")
    target: float = Field(..., gt=0, description="Above the entry")
    max_bars: int | None = Field(None, ge=1, le=600, description="Time limit in bars; default: a day of hours, ten days, 4 hours of 5m")
    replays: int | None = Field(None, ge=100, le=100_000)


class PlanRequest(BaseModel):
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: Timeframe = "1h"
    setup: str | None = Field(None, description="A setup from the scan; its past fires become the history")
    equity: float = Field(1_000, gt=0, description="Money available for this trade (USDT)")
    risk_pct: float = Field(1.0, gt=0, le=10, description="% of equity lost if the stop is hit")
    k_atr: float = Field(1.0, gt=0, le=10, description="Stop distance in ATRs")
    rr: float = Field(1.5, gt=0, le=10, description="Reward : risk")
    max_bars: int | None = Field(None, ge=1, le=600)
    stop_rule: Literal["atr", "range_mid"] = "atr"
    replays: int | None = Field(None, ge=100, le=100_000)


@router.get("/status")
async def status(user: Annotated[User, Depends(get_current_user)]):
    """Universe, last scan, settings, data problems."""
    return _desk().status()


@router.get("/scan")
async def scan(user: Annotated[User, Depends(get_current_user)]):
    """The latest scan: every live setup across the universe, each with its measured history and entry state."""
    latest = _desk().latest()
    if latest is None:
        raise HTTPException(status_code=409, detail="No scan yet — the first one runs shortly after start-up")
    return latest


@router.post("/rescan")
async def rescan(user: Annotated[User, Depends(get_current_user)]):
    """Run a scan now (the regular one keeps its schedule). Read-only."""
    desk = _desk()
    if desk.scanning:
        raise HTTPException(status_code=409, detail="A scan is already running")
    return await _call(desk.scan_now())


@router.get("/entry")
async def entry(user: Annotated[User, Depends(get_current_user)], symbol: str = Query(...), setup: str = Query(...),
                timeframe: Timeframe = "1h"):
    """The entry timer for one hit: waiting / triggered / expired against the live price."""
    return await _call(_desk().entry(symbol, setup, timeframe))


@router.post("/time-machine")
async def time_machine(body: TimeMachineRequest, user: Annotated[User, Depends(get_current_user)]):
    """Replay the next bars 10,000 times from the coin's own candles: target first, stop first, or neither."""
    return await _call(_desk().time_machine(body.symbol, body.timeframe, body.entry, body.stop, body.target,
                                            body.max_bars, body.replays))


@router.post("/plan")
async def plan(body: PlanRequest, user: Annotated[User, Depends(get_current_user)]):
    """Stop, target, size, the setup's real history and the time machine's odds, with one verdict. Sends nothing."""
    return await _call(_desk().plan(body.symbol, body.timeframe, body.setup, body.equity, body.risk_pct, body.k_atr,
                                    body.rr, body.max_bars, body.stop_rule, body.replays))
