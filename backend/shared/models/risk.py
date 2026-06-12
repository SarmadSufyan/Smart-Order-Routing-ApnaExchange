from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class RiskCheckResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_KILL_SWITCH = "REJECTED_KILL_SWITCH"
    REJECTED_SIZE_LIMIT = "REJECTED_SIZE_LIMIT"
    REJECTED_RATE_LIMIT = "REJECTED_RATE_LIMIT"
    REJECTED_POSITION_LIMIT = "REJECTED_POSITION_LIMIT"
    REJECTED_NOTIONAL_LIMIT = "REJECTED_NOTIONAL_LIMIT"
    REJECTED_VENUE_RESTRICTED = "REJECTED_VENUE_RESTRICTED"
    REJECTED_SYMBOL_RESTRICTED = "REJECTED_SYMBOL_RESTRICTED"


class RiskLimits(BaseModel):
    max_order_size: float = 10_000.0
    max_position_per_symbol: float = 50_000.0
    max_notional_exposure: float = 1_000_000.0
    max_orders_per_second: int = 50
    restricted_symbols: list[str] = []
    restricted_venues: list[str] = []


class RiskCheckDetail(BaseModel):
    order_id: str
    result: RiskCheckResult
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_us: float = 0.0


class PositionInfo(BaseModel):
    symbol: str
    net_position: float = 0.0
    notional: float = 0.0
    avg_entry_price: float = 0.0
    unrealized_pnl: float = 0.0


class KillSwitchState(BaseModel):
    active: bool = False
    last_activated: datetime | None = None
    last_deactivated: datetime | None = None
    activated_by: str | None = None
    reason: str | None = None
    orders_cancelled: int = 0


class KillSwitchActivateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class RiskStatusResponse(BaseModel):
    kill_switch: KillSwitchState
    positions: dict[str, PositionInfo] = {}
    total_notional_exposure: float = 0.0
    risk_limits: RiskLimits
    recent_checks: list[RiskCheckDetail] = []
