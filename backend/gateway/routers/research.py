"""Research and prediction tools (Phase B). Read-only: nothing here can place an order."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.gateway.middleware.auth import User, get_current_user
from backend.services.research.service import ResearchUnavailableError

router = APIRouter(prefix="/api/research", tags=["research"])

Timeframe = Literal["1m", "1h", "1d"]


def _research():
    from backend.gateway.dependencies import get_services

    return get_services().research


async def _call(coro):
    try:
        return await coro
    except ResearchUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class BacktestRequest(BaseModel):
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: Timeframe = "1h"
    strategy: Literal["buy_hold", "sma_cross", "rsi_reversion", "bollinger", "macd_cross", "model"] = "sma_cross"
    params: dict = Field(default_factory=dict, examples=[{"fast": 20, "slow": 50}])
    bars: int = Field(5_000, ge=300, le=50_000, description="How many past candles to test on (up to 5 years of hours)")
    capital: float = Field(1_000, gt=0)
    fee_bps: float | None = Field(None, ge=0, le=100, description="Default: the reference exchange's taker fee")
    slippage_bps: float | None = Field(None, ge=0, le=100)


class TradePlanRequest(BaseModel):
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: Timeframe = "1h"
    equity: float = Field(1_000, gt=0, description="Money available for this trade (USDT)")
    risk_pct: float = Field(1.0, gt=0, le=10, description="% of equity lost if the stop is hit")
    k_atr: float = Field(1.5, gt=0, le=10, description="Stop distance in ATRs")
    rr: float = Field(2.0, gt=0, le=10, description="Reward : risk")
    max_bars: int = Field(24, ge=1, le=500, description="Give up (close) after this many candles")


@router.get("/status")
async def research_status(user: Annotated[User, Depends(get_current_user)]):
    """History download progress per coin and timeframe, and which models are trained."""
    return _research().status()


@router.get("/forecast")
async def forecast(user: Annotated[User, Depends(get_current_user)],
                   symbol: str = Query(..., description="e.g. BTC/USDT")):
    """Probability the next minute / hour / day closes higher, the likely range, why, and the track record."""
    return await _call(_research().forecast(symbol))


@router.get("/scoreboard")
async def scoreboard(user: Annotated[User, Depends(get_current_user)]):
    """Every coin x horizon: out-of-sample accuracy vs the baseline, range coverage, live record."""
    return await _call(_research().scoreboard())


@router.get("/chart")
async def chart(user: Annotated[User, Depends(get_current_user)], symbol: str = Query(...),
                timeframe: Timeframe = "1h", bars: int = Query(200, ge=50, le=1_000)):
    """Candles with SMA 20/50, Bollinger bands, RSI, MACD and ATR."""
    return await _call(_research().chart(symbol, timeframe, bars))


@router.get("/orderbook")
async def orderbook(user: Annotated[User, Depends(get_current_user)], symbol: str = Query(...)):
    """Live order-book signals: imbalance near the price, microprice, depth, spread."""
    return await _call(_research().orderbook(symbol))


@router.get("/techniques")
async def technique_study(user: Annotated[User, Depends(get_current_user)], symbol: str = Query(...),
                          timeframe: Timeframe = "1h"):
    """Every classic technique on the coin's whole stored history: what worked after fees, what each says now."""
    return await _call(_research().techniques(symbol, timeframe))


@router.post("/backtest")
async def backtest(body: BacktestRequest, user: Annotated[User, Depends(get_current_user)]):
    """Test a trading rule on past candles with fees and slippage (long only). Sends nothing."""
    return await _call(_research().backtest(body.symbol, body.timeframe, body.strategy, body.params, body.bars,
                                            body.capital, body.fee_bps, body.slippage_bps))


@router.post("/trade-plan")
async def trade_plan(body: TradePlanRequest, user: Annotated[User, Depends(get_current_user)]):
    """Stop, target and size for a long trade, with this bracket's measured history and a verdict. Sends nothing."""
    return await _call(_research().trade_plan(body.symbol, body.timeframe, body.equity, body.risk_pct, body.k_atr,
                                              body.rr, body.max_bars))
