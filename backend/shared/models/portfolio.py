"""Portfolio book of record: opening balances + fills -> positions and P&L.

Kept separate from the wallet models on purpose: the wallet is what the
exchanges SAY we hold; the portfolio ledger is what we EXPECT to hold from
our own records. Comparing the two is reconciliation.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.shared.models.order import OrderSide


class FillRecord(BaseModel):
    """One child execution that moved balances (a FILL or PARTIAL)."""
    id: str  # child order id — one record per child execution
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    order_id: str
    venue_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float = 0.0
    fee_asset: str | None = None
    fee_usdt: float | None = None  # fee valued at fill time (None if unpriceable)
    exchange_order_id: str | None = None


class VenueBaseline(BaseModel):
    """Opening balances of one venue, marked at our own mid prices.

    Testnet accounts start pre-funded and their coins have no known cost, so
    P&L is measured from here: fills after ``ts`` are applied on top.
    """
    venue_id: str
    ts: datetime
    set_by: str = "auto"
    balances: dict[str, float]
    marks: dict[str, float] = {}  # USDT per unit at ``ts`` (stablecoins omitted)


class AssetPnL(BaseModel):
    asset: str
    quantity: float  # expected holdings from the ledger, all venues
    opening_quantity: float
    avg_cost: float | None = None
    mark: float | None = None
    value_usdt: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float | None = None
    fees_usdt: float = 0.0  # fees on trades in this asset
    total_pnl: float | None = None  # realized + unrealized - fees
    hold_pnl: float | None = None  # opening quantity x price move
    by_venue: dict[str, float] = {}


ReconStatus = Literal["OK", "BREAK", "PENDING", "UNKNOWN", "NO_BASELINE"]


class ReconItem(BaseModel):
    asset: str
    expected: float
    actual: float | None = None
    difference: float | None = None
    difference_usdt: float | None = None
    status: ReconStatus


class VenueReconciliation(BaseModel):
    venue_id: str
    name: str
    status: ReconStatus
    baseline_at: datetime | None = None
    baseline_set_by: str | None = None
    note: str | None = None
    items: list[ReconItem] = []


class PortfolioPnL(BaseModel):
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: str = ""
    since: datetime | None = None  # earliest baseline
    complete: bool = False  # every holding priced -> totals are exact
    unpriced_assets: list[str] = []
    opening_value_usdt: float | None = None
    value_usdt: float | None = None  # ledger holdings at live marks
    total_pnl: float | None = None
    hold_pnl: float | None = None
    trading_pnl: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    fees_usdt: float = 0.0
    fills_applied: int = 0
    fills_before_baseline: int = 0
    assets: list[AssetPnL] = []
    reconciliation: list[VenueReconciliation] = []
    notes: list[str] = []


class BaselineResetRequest(BaseModel):
    venue_id: str | None = None  # None = every venue whose balances are readable


class ConcentrationItem(BaseModel):
    kind: Literal["asset", "venue"]
    key: str
    name: str
    value_usdt: float
    share_pct: float
    limit_pct: float
    breached: bool


class PortfolioRiskState(BaseModel):
    """What the risk monitor last saw: loss limit and concentration."""
    as_of: datetime | None = None
    trading_pnl: float | None = None
    loss_limit_usdt: float = 0.0
    loss_used_pct: float | None = None
    loss_limit_breached: bool = False
    concentration: list[ConcentrationItem] = []
    alerts: list[str] = []
