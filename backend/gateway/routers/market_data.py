from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.market_data import NBBO, VenueQuote

router = APIRouter(prefix="/api/market-data", tags=["market-data"])


def _get_aggregator():
    from backend.gateway.dependencies import get_services

    return get_services().market_data_aggregator


def _get_collector():
    from backend.gateway.dependencies import get_services

    return get_services().market_data_collector


@router.get("/nbbo", response_model=NBBO)
async def get_nbbo(
    symbol: str = Query(..., description="Symbol to get NBBO for"),
    user: User = Depends(get_current_user),
):
    aggregator = _get_aggregator()
    nbbo = await aggregator.compute_nbbo(symbol.upper())
    if not nbbo:
        raise HTTPException(
            status_code=404, detail=f"No market data available for {symbol}"
        )
    return nbbo


@router.get("/quotes")
async def get_quotes(
    symbol: str = Query(...),
    user: User = Depends(get_current_user),
):
    collector = _get_collector()
    quotes = collector.get_all_quotes_for_symbol(symbol.upper())
    return {"symbol": symbol.upper(), "quotes": {vid: q.model_dump(mode="json") for vid, q in quotes.items()}}


@router.get("/nbbo/all")
async def get_all_nbbo(user: User = Depends(get_current_user)):
    aggregator = _get_aggregator()
    from backend.shared.models.market_data import DEFAULT_SYMBOLS

    result = {}
    for sym_cfg in DEFAULT_SYMBOLS:
        nbbo = await aggregator.compute_nbbo(sym_cfg.symbol)
        if nbbo:
            result[sym_cfg.symbol] = nbbo.model_dump(mode="json")
    return {"nbbo": result}
