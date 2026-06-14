"""
server/routers/quote.py

GET /quote?symbol=AAPL → bid/ask snapshot from the symbol's orderbook engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

import structlog

router = APIRouter()
logger = structlog.get_logger()


class QuoteResponse(BaseModel):
    venue_id: str
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    last_price: float
    volume: int
    timestamp: str


@router.get("/quote", response_model=QuoteResponse)
async def get_quote(symbol: str = Query(default="AAPL")) -> QuoteResponse:
    from server.venue_app import get_engines, get_symbol_engines

    engines = get_engines()
    await engines.latency.inject()

    se = get_symbol_engines(symbol)
    mid = se.price_engine.current_price
    bid_price, ask_price, bid_size, ask_size = se.orderbook.top_of_book(mid)

    now = datetime.now(timezone.utc)

    return QuoteResponse(
        venue_id=engines.profile.venue_id,
        symbol=symbol,
        bid_price=bid_price,
        ask_price=ask_price,
        bid_size=bid_size,
        ask_size=ask_size,
        last_price=round(se.price_engine.last_price, 2),
        volume=se.price_engine.volume,
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
    )
