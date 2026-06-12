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
    max_latency_ms: float = 200.0
    max_staleness_ms: float = 2000.0
    healthy_threshold: float = 0.8
    degraded_threshold: float = 0.5
