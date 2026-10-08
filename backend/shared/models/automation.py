"""Phase C — automation: the automation fund, the bots, and the practice lab.

The automation fund is money the owner moves out of the wallet for bots only. ApnaSOR
keeps its own book of it: each bot gets a *pocket* (a budget it can never exceed), and
manual orders can't spend what the fund holds. On one exchange account the money is
physically pooled — the separation is enforced by ApnaSOR's books and order gate (real
money in Phase D: a separate exchange sub-account is the stronger option).

Bots (``backend/services/automation``):
    CYCLE     profit-cycle bot: buy a small lot, sell it only above its cost + both fees +
              the owner's threshold, repeat. Never sells a lot below that.
    ADVISOR   advisor agent: reads the research forecasts, the trade plan and the lab's
              own measurements, proposes a bracketed buy with its reasons; the owner
              approves — or, on testnet only, lets it approve itself within limits.

Every bot order is an ordinary order through ``OrderEntry.submit`` (risk, kill switch,
routing, price-protected IOC children). Every lot a bot opens is a lab sample: the
market conditions at the buy and what happened after fees.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.shared.models.order import RoutingStrategy

FUND_HISTORY_KEPT = 200
PROPOSALS_KEPT = 50


def _now() -> datetime:
    return datetime.now(UTC)


# ── the automation fund ─────────────────────────────────────────────
class Pocket(BaseModel):
    """One bot's slice of the automation fund: its own small book."""

    budget_usdt: float = 0.0  # given to the bot, net of what was taken back
    cash_usdt: float = 0.0  # USDT not in coins right now
    coins: dict[str, float] = {}  # asset -> quantity this bot holds
    cost_usdt: dict[str, float] = {}  # asset -> what the coins it holds cost (incl. buy fees)
    market_cost_usdt: dict[str, float] = {}  # the same, if every fee had been the real-market rate
    realized_usdt: float = 0.0  # closed profit, fees as actually charged
    realized_market_usdt: float = 0.0  # closed profit at real-market fees (testnet fees can be 0)
    fees_usdt: float = 0.0
    market_fees_usdt: float = 0.0


FundAction = Literal["deposit", "withdraw", "assign", "release", "coins_to_manual", "write_off"]


class FundEvent(BaseModel):
    ts: datetime = Field(default_factory=_now)
    actor: str
    action: FundAction
    amount_usdt: float
    bot_id: str | None = None
    note: str | None = None


class FundState(BaseModel):
    mode: str = "testnet"
    allocated_usdt: float = 0.0  # moved in from the wallet, net of what was moved out
    unassigned_usdt: float = 0.0  # in the fund, not given to any bot
    pockets: dict[str, Pocket] = {}
    history: list[FundEvent] = []  # newest last, the last FUND_HISTORY_KEPT
    updated_at: datetime | None = None


class FundAmount(BaseModel):
    amount_usdt: float = Field(..., gt=0, le=10_000_000)


class FundView(BaseModel):
    mode: str
    allocated_usdt: float
    unassigned_usdt: float
    bots_cash_usdt: float
    coins: dict[str, float]  # held by bots, per asset
    coins_value_usdt: float | None  # None while a held coin has no live price
    value_usdt: float | None
    pnl_usdt: float | None  # value - allocated (fees as charged)
    pnl_market_usdt: float | None  # the same at real-market fees
    reserved: dict[str, float]  # what manual orders may not use
    wallet_free: dict[str, float]  # free on the exchanges, all venues
    manual_free: dict[str, float]  # wallet_free - reserved
    pockets: dict[str, Pocket]
    history: list[FundEvent]
    warnings: list[str] = []
    note: str


# ── lots and bot settings ───────────────────────────────────────────
class LotStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"  # sold
    WRITTEN_OFF = "WRITTEN_OFF"  # the coins left the bot's book without a sale (moved to manual, or lost)


class Lot(BaseModel):
    """One buy a bot made, and how it ended."""

    id: int
    symbol: str
    status: LotStatus = LotStatus.OPEN
    opened_at: datetime = Field(default_factory=_now)
    qty: float  # coins this lot holds now (net of a fee taken in the coin)
    bought_qty: float
    cost_usdt: float  # what the coins it holds cost, fees as charged
    market_cost_usdt: float  # ... at real-market fees (never less than cost_usdt)
    buy_price: float  # average fill price
    mid_at_buy: float | None = None
    target_price: float  # sell at or above this
    stop_price: float | None = None  # advisor trades: sell at or below this
    expires_at: datetime | None = None  # advisor trades: sell when this time comes
    buy_order_id: str
    venues: list[str] = []
    sell_order_ids: list[str] = []
    sold_qty: float = 0.0
    proceeds_usdt: float = 0.0  # after the sell fees as charged
    proceeds_market_usdt: float = 0.0
    sold_cost_usdt: float = 0.0
    sold_market_cost_usdt: float = 0.0
    closed_at: datetime | None = None
    exit_reason: str | None = None  # target / stop / max hold / sold by owner / moved to manual / lost
    profit_usdt: float | None = None  # when closed
    profit_market_usdt: float | None = None
    retry_after: datetime | None = None  # an IOC sell that didn't fill waits a little before the next try
    proposal_id: str | None = None
    features: dict[str, Any] = {}  # market conditions at the buy (lab sample)


class BotKind(str, Enum):
    CYCLE = "CYCLE"
    ADVISOR = "ADVISOR"


class BotStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"  # by the owner, the kill switch, a restart, or repeated rejections
    STOPPED = "STOPPED"  # no new trades; its cash went back to the fund (open lots stay until sold)
    FAILED = "FAILED"


ACTIVE_BOT_STATUSES = {BotStatus.RUNNING, BotStatus.PAUSED}


def _upper_symbol(value: str) -> str:
    return value.strip().upper()


class CycleParams(BaseModel):
    """Profit-cycle bot. Defaults decided with Sarmad on 2026-10-08."""

    symbol: str = Field(..., examples=["BTC/USDT"])
    budget_usdt: float = Field(50.0, gt=0, le=1_000_000, description="Taken from the automation fund")
    buy_usdt: float = Field(10.0, gt=0, le=100_000, description="Size of each buy")
    threshold_pct: float = Field(0.3, gt=0, le=20, description="Profit on top of both fees before a lot is sold")
    max_lots: int | None = Field(None, ge=1, le=100, description="Lots waiting to sell at once (default budget / buy)")
    spacing_pct: float = Field(0.5, ge=0, le=20, description="Another lot only this far under the lowest open lot")
    cooldown_s: float = Field(60.0, ge=0, le=86_400, description="Wait after a buy before the next one")
    max_drawdown_pct: float = Field(10.0, gt=0, le=100, description="No new buys while open lots are this far under cost")
    check_interval_s: float = Field(5.0, ge=2, le=300)
    routing: RoutingStrategy = RoutingStrategy.BEST_PRICE
    lab: bool = Field(False, description="Part of the practice lab (testnet only)")
    label: str | None = Field(None, max_length=60)

    _symbol = field_validator("symbol")(_upper_symbol)

    @model_validator(mode="after")
    def _sizes(self) -> "CycleParams":
        if self.buy_usdt > self.budget_usdt:
            raise ValueError("buy_usdt can't be larger than budget_usdt")
        return self

    @property
    def lots_allowed(self) -> int:
        return self.max_lots or max(1, int(self.budget_usdt // self.buy_usdt))


class AdvisorMode(str, Enum):
    ASK = "ASK"  # every trade waits for the owner's approval
    AUTO = "AUTO"  # approves itself within its limits — testnet only (decided 2026-10-08)


class AdvisorParams(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"])
    budget_usdt: float = Field(50.0, gt=0, le=1_000_000)
    trade_usdt: float = Field(10.0, gt=0, le=100_000, description="Size of each trade")
    mode: AdvisorMode = AdvisorMode.ASK
    min_probability: float = Field(0.55, ge=0.5, le=0.95, description="Next-hour P(up) needed to propose a buy")
    require_edge: bool = Field(False, description="Only when the forecast model has a proven edge for that coin")
    min_expectancy_r: float = Field(0.0, ge=-1, le=5,
                                    description="The bracket's average result on past candles, after costs, in R")
    k_atr: float = Field(1.5, gt=0, le=10, description="Stop distance in ATRs (hourly)")
    rr: float = Field(2.0, gt=0, le=10, description="Target distance in multiples of the stop distance")
    max_hold_hours: float = Field(12.0, gt=0, le=168)
    max_open: int = Field(2, ge=1, le=20)
    max_trades_per_day: int = Field(6, ge=1, le=200)
    scan_interval_s: float = Field(300.0, ge=30, le=3_600)
    proposal_ttl_s: float = Field(600.0, ge=30, le=86_400)
    check_interval_s: float = Field(5.0, ge=2, le=300)
    routing: RoutingStrategy = RoutingStrategy.BEST_PRICE
    lab: bool = False
    label: str | None = Field(None, max_length=60)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        out = list(dict.fromkeys(_upper_symbol(s) for s in value if s.strip()))
        if not out:
            raise ValueError("at least one symbol")
        return out

    @model_validator(mode="after")
    def _sizes(self) -> "AdvisorParams":
        if self.trade_usdt > self.budget_usdt:
            raise ValueError("trade_usdt can't be larger than budget_usdt")
        return self


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"  # waiting for the owner
    APPROVED = "APPROVED"  # sent (by the owner, or by itself in AUTO)
    REJECTED = "REJECTED"  # by the owner
    EXPIRED = "EXPIRED"  # not answered in time, or the price moved away
    FAILED = "FAILED"  # the buy was refused or didn't fill


class AdvisorProposal(BaseModel):
    id: str
    bot_id: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    status: ProposalStatus = ProposalStatus.PROPOSED
    symbol: str
    notional_usdt: float
    entry_price: float  # the ask when proposed
    stop_price: float
    target_price: float
    max_hold_hours: float
    p_up: float
    reasons: list[str]
    evidence: dict[str, Any] = {}
    decided_by: str | None = None
    decided_at: datetime | None = None
    result: str | None = None
    lot_id: int | None = None


class BotStats(BaseModel):
    buys: int = 0
    sells: int = 0
    lots_closed: int = 0
    wins: int = 0  # advisor: target hit / cycle: every closed lot
    losses: int = 0  # advisor: stop hit (or a sale below cost by the owner)
    timeouts: int = 0  # advisor: max hold reached
    trades_today: int = 0
    trades_day: str | None = None  # the UTC day trades_today counts
    consecutive_rejections: int = 0
    last_buy_at: datetime | None = None
    last_scan_at: datetime | None = None


class BotState(BaseModel):
    id: str
    kind: BotKind
    label: str
    created_by: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    status: BotStatus = BotStatus.RUNNING
    status_reason: str | None = None
    lab: bool = False
    cycle: CycleParams | None = None
    advisor: AdvisorParams | None = None
    lots: list[Lot] = []
    next_lot_id: int = 1
    proposals: list[AdvisorProposal] = []  # newest first
    last_scan: list[dict[str, Any]] = []  # advisor: per coin, what it saw and why it did / didn't propose
    waiting_reason: str | None = None
    stats: BotStats = Field(default_factory=BotStats)
    # Filled in for API answers (the fund is the book of record for the pocket):
    pocket: Pocket | None = None
    value_usdt: float | None = None
    unrealized_usdt: float | None = None
    pnl_usdt: float | None = None
    pnl_market_usdt: float | None = None

    @property
    def symbols(self) -> list[str]:
        if self.cycle is not None:
            return [self.cycle.symbol]
        return list(self.advisor.symbols) if self.advisor else []

    @property
    def open_lots(self) -> list[Lot]:
        return [lot for lot in self.lots if lot.status == LotStatus.OPEN]


class BotList(BaseModel):
    bots: list[BotState]


class SellNowRequest(BaseModel):
    lot_id: int | None = Field(None, description="One lot, or every open lot when omitted")


class CoinsRelease(BaseModel):
    how: Literal["to_manual", "lost"] = Field(
        "to_manual", description="to_manual: the coins stay in the wallet for manual trading; "
                                 "lost: they are gone (e.g. a testnet balance reset)")


class ProposalDecision(BaseModel):
    approve: bool


# ── the practice lab ────────────────────────────────────────────────
class LabSampleKind(str, Enum):
    CYCLE_LOT = "CYCLE_LOT"  # one profit-cycle lot: conditions at the buy -> how it ended
    ADVISOR_TRADE = "ADVISOR_TRADE"  # one advisor trade
    EXECUTION = "EXECUTION"  # one child order of a bot: what we expected vs what the exchange gave


class LabSample(BaseModel):
    id: str
    kind: LabSampleKind
    bot_id: str
    variant: str  # the bot's label: samples of one variant are compared with another's
    symbol: str
    venue_id: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    features: dict[str, Any] = {}
    outcome: dict[str, Any] = {}


class LabPreset(BaseModel):
    name: str
    title: str
    description: str
    budget_usdt: float  # moved from the wallet into the fund when started


class LabReport(BaseModel):
    mode: str
    enabled: bool
    note: str
    samples: dict[str, int]
    costs: list[dict[str, Any]]
    scoreboard: list[dict[str, Any]]
    advisor: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    models: list[dict[str, Any]]
    presets: list[LabPreset]
