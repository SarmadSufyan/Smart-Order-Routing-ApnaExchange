from datetime import UTC, datetime

from pydantic import BaseModel, Field


class VenueQuote(BaseModel):
    venue_id: str
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    last_price: float
    volume: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_stale: bool = False


class NBBO(BaseModel):
    symbol: str
    best_bid: float
    best_bid_venue: str
    best_bid_size: float
    best_ask: float
    best_ask_venue: str
    best_ask_size: float
    spread: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    venue_quotes: dict[str, VenueQuote] = {}


class OrderBookLevel(BaseModel):
    price: float
    size: float
    order_count: int = 1


class OrderBookSnapshot(BaseModel):
    venue_id: str
    symbol: str
    bids: list[OrderBookLevel] = []
    asks: list[OrderBookLevel] = []
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def split_symbol(symbol: str) -> tuple[str, str]:
    """``"BTC/USDT" -> ("BTC", "USDT")``."""
    base, _, quote = symbol.partition("/")
    return base, quote
