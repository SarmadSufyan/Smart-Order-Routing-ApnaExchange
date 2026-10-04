from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from backend.shared.models.portfolio import PortfolioRiskState


class RiskCheckResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_KILL_SWITCH = "REJECTED_KILL_SWITCH"
    REJECTED_SIZE_LIMIT = "REJECTED_SIZE_LIMIT"
    REJECTED_ORDER_NOTIONAL_LIMIT = "REJECTED_ORDER_NOTIONAL_LIMIT"
    REJECTED_RATE_LIMIT = "REJECTED_RATE_LIMIT"
    REJECTED_POSITION_LIMIT = "REJECTED_POSITION_LIMIT"
    REJECTED_NOTIONAL_LIMIT = "REJECTED_NOTIONAL_LIMIT"
    REJECTED_VENUE_RESTRICTED = "REJECTED_VENUE_RESTRICTED"
    REJECTED_SYMBOL_RESTRICTED = "REJECTED_SYMBOL_RESTRICTED"


class RiskLimits(BaseModel):
    max_order_size: float = 10_000.0  # base-asset units
    max_order_notional: float = 10_000.0  # USDT per order — the meaningful cap for crypto
    # USDT for a whole algo (parent) order. Without it, slicing a $50k order
    # into $5k pieces would walk straight past the per-order limit.
    max_algo_notional_usdt: float = 50_000.0
    max_position_per_symbol: float = 50_000.0
    max_notional_exposure: float = 1_000_000.0
    max_orders_per_second: int = 50
    restricted_symbols: list[str] = []
    restricted_venues: list[str] = []
    # Portfolio-level controls, watched continuously by the risk monitor.
    max_trading_loss_usdt: float = 1_000.0  # trading P&L below -this engages the kill switch; 0 = off
    max_asset_concentration_pct: float = 60.0  # one non-stable asset's share of the portfolio
    max_venue_concentration_pct: float = 80.0  # one exchange's share (counterparty risk)


class RiskCheckDetail(BaseModel):
    order_id: str
    result: RiskCheckResult
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_us: float = 0.0
    message: str | None = None  # human-readable reason when rejected


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


class PreflightOrder(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float = Field(..., gt=0)


class PreflightRequest(BaseModel):
    """A basket to check without sending anything (``POST /api/risk/preflight``)."""

    orders: list[PreflightOrder] = Field(..., min_length=1, max_length=200)
    stress: bool = False  # also run the Monte Carlo stress test per order (CPU-heavy)
    mode: Literal["auto", "serial", "pool"] = "auto"  # auto = pool only when it pays off


class StressSummary(BaseModel):
    position_after: float  # base units held after the order (signed)
    var_99_usdt: float  # worst loss within the horizon on 99% of simulated paths
    expected_shortfall_usdt: float  # average loss over the worst 1% of paths
    horizon_min: int
    paths: int
    sigma_per_min_pct: float  # measured volatility, % per minute


class PreflightItemResult(BaseModel):
    index: int
    symbol: str
    side: str
    quantity: float
    mid_price: float
    result: RiskCheckResult
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    message: str | None = None
    position_after: float  # if this and every earlier order in the basket filled
    stress: StressSummary | None = None


class PreflightResponse(BaseModel):
    all_approved: bool
    approved: int
    rejected: int
    executor: str  # "in-process" or "pool (N processes)"
    elapsed_ms: float
    notes: list[str] = []
    results: list[PreflightItemResult]


class RiskStatusResponse(BaseModel):
    kill_switch: KillSwitchState
    positions: dict[str, PositionInfo] = {}
    total_notional_exposure: float = 0.0
    risk_limits: RiskLimits
    recent_checks: list[RiskCheckDetail] = []
    portfolio: PortfolioRiskState | None = None
