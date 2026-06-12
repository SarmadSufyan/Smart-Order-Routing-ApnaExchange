from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class MarketRegime(str, Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    VOLATILE = "VOLATILE"
    CRISIS = "CRISIS"


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


class SymbolConfig(BaseModel):
    symbol: str = Field(..., examples=["AAPL"])
    initial_price: float = Field(..., gt=0, examples=[150.0])
    daily_volume: float = Field(50_000_000, gt=0)
    volatility: float = Field(0.02, gt=0, le=1.0)
    drift: float = Field(0.0001)


DEFAULT_SYMBOLS: list[SymbolConfig] = [
    SymbolConfig(symbol="AAPL", initial_price=150.00, daily_volume=50_000_000, volatility=0.02),
    SymbolConfig(symbol="GOOGL", initial_price=175.00, daily_volume=20_000_000, volatility=0.025),
    SymbolConfig(symbol="MSFT", initial_price=420.00, daily_volume=25_000_000, volatility=0.018),
    SymbolConfig(symbol="AMZN", initial_price=185.00, daily_volume=30_000_000, volatility=0.022),
    SymbolConfig(symbol="TSLA", initial_price=250.00, daily_volume=80_000_000, volatility=0.04),
]
