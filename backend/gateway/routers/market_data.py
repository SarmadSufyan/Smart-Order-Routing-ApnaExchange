from fastapi import APIRouter, Depends, HTTPException, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.market_data import NBBO, OrderBookLevel, OrderBookSnapshot

router = APIRouter(prefix="/api/market-data", tags=["market-data"])


def _services():
    from backend.gateway.dependencies import get_services

    return get_services()


@router.get("/symbols")
async def list_symbols(user: User = Depends(get_current_user)):
    return {"symbols": _services().market_data_collector.symbols}


@router.get("/nbbo", response_model=NBBO)
async def get_nbbo(
    symbol: str = Query(..., description="Unified symbol, e.g. BTC/USDT"),
    user: User = Depends(get_current_user),
):
    nbbo = await _services().market_data_aggregator.compute_nbbo(symbol.upper())
    if not nbbo:
        raise HTTPException(status_code=404, detail=f"No market data available for {symbol}")
    return nbbo


@router.get("/quotes")
async def get_quotes(symbol: str = Query(...), user: User = Depends(get_current_user)):
    quotes = _services().market_data_collector.get_all_quotes_for_symbol(symbol.upper())
    return {"symbol": symbol.upper(), "quotes": {vid: q.model_dump(mode="json") for vid, q in quotes.items()}}


@router.get("/nbbo/all")
async def get_all_nbbo(user: User = Depends(get_current_user)):
    svc = _services()
    result = {}
    for symbol in svc.market_data_collector.symbols:
        nbbo = await svc.market_data_aggregator.compute_nbbo(symbol)
        if nbbo:
            result[symbol] = nbbo.model_dump(mode="json")
    return {"nbbo": result}


@router.get("/orderbook", response_model=OrderBookSnapshot)
async def get_orderbook(
    symbol: str = Query(..., description="Unified symbol, e.g. BTC/USDT"),
    venue_id: str = Query("V1"),
    depth: int = Query(20, ge=1, le=500),
    user: User = Depends(get_current_user),
):
    """Top ``depth`` levels of a venue's live local order book."""
    book = _services().market_data_collector.get_depth(venue_id, symbol.upper(), depth)
    if book is None:
        raise HTTPException(status_code=404, detail=f"No synced book for {symbol} on {venue_id}")
    return OrderBookSnapshot(
        venue_id=venue_id,
        symbol=symbol.upper(),
        bids=[OrderBookLevel(price=p, size=q) for p, q in book["bids"]],
        asks=[OrderBookLevel(price=p, size=q) for p, q in book["asks"]],
    )
