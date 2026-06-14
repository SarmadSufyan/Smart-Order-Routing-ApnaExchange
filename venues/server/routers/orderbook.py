"""
server/routers/orderbook.py

GET /orderbook?symbol=AAPL&depth=5 → Top-N depth levels.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class LevelResponse(BaseModel):
    price: float
    size: int


class OrderBookResponse(BaseModel):
    venue_id: str
    symbol: str
    bids: list[LevelResponse]
    asks: list[LevelResponse]
    timestamp: str


@router.get("/orderbook", response_model=OrderBookResponse)
async def get_orderbook(
    symbol: str = Query(default="AAPL"),
    depth: int = Query(default=5, ge=1, le=20),
) -> OrderBookResponse:
    from server.venue_app import get_engines, get_symbol_engines

    engines = get_engines()
    await engines.latency.inject()

    se = get_symbol_engines(symbol)
    mid = se.price_engine.current_price
    snap = se.orderbook.snapshot(mid, depth=depth)

    now = datetime.now(timezone.utc)

    return OrderBookResponse(
        venue_id=engines.profile.venue_id,
        symbol=symbol,
        bids=[LevelResponse(price=b.price, size=b.size) for b in snap.bids],
        asks=[LevelResponse(price=a.price, size=a.size) for a in snap.asks],
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
    )
