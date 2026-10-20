from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class VenueStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    BLACKLISTED = "BLACKLISTED"
    DISCONNECTED = "DISCONNECTED"


class VenueProfile(BaseModel):
    venue_id: str = Field(..., examples=["V1"])
    name: str = Field(..., examples=["AlphaExchange"])
    cloud: str = Field(..., examples=["AWS"])
    host: str = Field(..., examples=["localhost"])
    port: int = Field(..., examples=[8001])
    base_latency_ms: float = Field(..., examples=[10.0])
    personality: str = Field(..., examples=["stable"])


class VenueMetrics(BaseModel):
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    fill_rate: float = 0.0
    reject_rate: float = 0.0
    orders_last_5min: int = 0
    fills_last_5min: int = 0
    rejects_last_5min: int = 0


class CoherenceStatus(str, Enum):
    OK = "OK"
    DIVERGED = "DIVERGED"  # execution book too far from the real market
    EMPTY = "EMPTY"  # execution book has no bid or no ask
    UNKNOWN = "UNKNOWN"  # not measured yet / probe failed
    SAME_BOOK = "SAME_BOOK"  # data and execution are the same market


class CoherenceState(BaseModel):
    """How far a venue's EXECUTION book sits from the book we decide on."""

    status: CoherenceStatus
    divergence_bps: float | None = None
    exec_bid: float | None = None
    exec_ask: float | None = None
    checked_at: datetime | None = None


class VenueHealth(BaseModel):
    venue_id: str
    name: str = ""
    cloud: str = ""
    status: VenueStatus = VenueStatus.DISCONNECTED
    health_score: float = Field(0.0, ge=0.0, le=1.0)
    latency_ms: float = 0.0
    fill_rate: float = 0.0
    reject_rate: float = 0.0
    uptime: float = 0.0
    last_checked: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_count: int = 0
    orders_total: int = 0  # venue-attributable outcomes; 0 -> fill/reject rates are undefined
    # Measured REST round-trip percentiles (latency_ms above is the p95)
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    # Live-exchange feed diagnostics
    mode: str = ""  # "testnet" | "mainnet"
    feed_connected: bool = False
    feed_age_ms: float | None = None
    books_synced: int = 0
    books_total: int = 0
    resyncs: int = 0
    reconnects: int = 0
    degraded_mode: bool = False  # artificial latency injected for demos
    data_mode: str = ""  # where the order book comes from: "testnet" | "mainnet"
    execution_available: bool = True  # False for data-only venues (e.g. Coinbase: no real testnet)
    trading_enabled: bool = False  # execution available AND API keys present
    execution_note: str | None = None  # why the venue can't trade, if it can't
    key_note: str | None = None  # real money: the exchange's verdict on our API key (trade-only?)
    fee_note: str | None = None  # real money: the account's own taker fee, as the exchange reports it
    # Per symbol: does the execution (testnet) book track the real market?
    execution_coherence: dict[str, CoherenceState] = {}


class VenueDetail(BaseModel):
    venue_id: str
    name: str
    cloud: str
    host: str
    port: int
    status: VenueStatus
    health_score: float
    current_metrics: VenueMetrics
    latency_history: list[dict] = []


class VenueListResponse(BaseModel):
    venues: list[VenueHealth]


class HealthScoreWeights(BaseModel):
    latency: float = 0.25
    fill_rate: float = 0.25
    reject_rate: float = 0.20
    uptime: float = 0.15
    freshness: float = 0.15


class HealthScoreConfig(BaseModel):
    weights: HealthScoreWeights = Field(default_factory=HealthScoreWeights)
    max_latency_ms: float = 1000.0
    max_staleness_ms: float = 2000.0
    healthy_threshold: float = 0.6
    degraded_threshold: float = 0.3
