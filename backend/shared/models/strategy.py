"""Algo (parent) orders — the strategy layer's view of an execution.

An algo order is a parent order that the engine works into ordinary child
orders over time. Every child is a normal order through the normal entry
point, so it carries its own routing proof and audit trail; this model only
adds the plan (schedule / rules) and how the whole execution went.

Kinds (backend/services/algos/schedules.py has the maths):
    TWAP     equal slices over a window
    VWAP     slices follow the market's real volume profile over the window
    IS       implementation shortfall: Almgren-Chriss schedule, front-loaded by urgency
    POV      a set percentage of the real market volume, as it happens
    ICEBERG  never more than a display size at once, only at or better than a limit price
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.shared.models.order import OrderSide, RoutingStrategy

MIN_SLICE_INTERVAL_S = 3.0  # a slice takes up to ~1-2 s on a real venue, plus a balance refresh


class AlgoKind(str, Enum):
    TWAP = "TWAP"
    VWAP = "VWAP"
    IS = "IS"  # implementation shortfall (Almgren-Chriss)
    POV = "POV"  # percentage of volume
    ICEBERG = "ICEBERG"


SCHEDULED_KINDS = {AlgoKind.TWAP, AlgoKind.VWAP, AlgoKind.IS}  # a plan of slices fixed at the start


class Urgency(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# Implementation shortfall: urgency = how front-loaded the schedule is, as the
# dimensionless kappa*T of Almgren-Chriss (equivalent to choosing the risk
# aversion; 0 would be TWAP). HIGH trades ~half the order in the first fifth.
URGENCY_KAPPA_T = {Urgency.LOW: 0.5, Urgency.MEDIUM: 2.0, Urgency.HIGH: 5.0}


class AlgoStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"  # by an operator, the kill switch, a restart, or repeated rejections
    COMPLETED = "COMPLETED"  # everything filled
    EXPIRED = "EXPIRED"  # the window ended with some quantity unfilled
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"  # internal error — see status_reason


ACTIVE_STATUSES = {AlgoStatus.RUNNING, AlgoStatus.PAUSED}


class AlgoCreate(BaseModel):
    kind: AlgoKind = AlgoKind.TWAP
    symbol: str = Field(..., min_length=1, max_length=20, examples=["BTC/USDT"])
    side: OrderSide
    quantity: float = Field(..., gt=0, examples=[0.001])
    # TWAP / VWAP / IS: the execution window. POV / ICEBERG: the longest it may run.
    duration_s: float = Field(..., gt=0, le=86_400, description="Execution window in seconds")
    slices: int | None = Field(None, ge=2, le=500, description="TWAP / VWAP / IS: number of slices")
    limit_price: float | None = Field(None, gt=0, description="Worst price for any child (ICEBERG: required)")
    jitter_pct: float = Field(0.0, ge=0, le=50, description="Randomise slice times by ± this % of the interval")
    routing: RoutingStrategy = Field(RoutingStrategy.BEST_PRICE, description="How each child is split across exchanges")
    # VWAP: how many past days of the same time window make the volume profile
    volume_days: int = Field(5, ge=1, le=14)
    # IS: how front-loaded the schedule is
    urgency: Urgency = Urgency.MEDIUM
    # POV: the share of the market's volume to trade, and how often to check it
    participation_pct: float | None = Field(None, gt=0, le=50)
    check_interval_s: float = Field(10.0, gt=0, le=300)
    # ICEBERG: the most shown at once, how often to look, and how much the piece size varies
    display_qty: float | None = Field(None, gt=0)
    refresh_s: float = Field(5.0, gt=0, le=300)
    size_variance_pct: float = Field(20.0, ge=0, le=50)

    @model_validator(mode="after")
    def _kind_rules(self) -> "AlgoCreate":
        if self.kind in SCHEDULED_KINDS:
            if self.slices is None:
                raise ValueError(f"{self.kind.value} needs a number of slices")
            if self.duration_s / self.slices < MIN_SLICE_INTERVAL_S:
                raise ValueError(
                    f"{self.slices} slices in {self.duration_s:g} s is one every "
                    f"{self.duration_s / self.slices:.1f} s; the minimum is {MIN_SLICE_INTERVAL_S:g} s"
                )
        elif self.kind == AlgoKind.POV:
            if self.participation_pct is None:
                raise ValueError("POV needs participation_pct (the share of market volume to trade, 1-50 %)")
            if self.check_interval_s < MIN_SLICE_INTERVAL_S:
                raise ValueError(f"POV checks the volume at most every {MIN_SLICE_INTERVAL_S:g} s")
        elif self.kind == AlgoKind.ICEBERG:
            if self.limit_price is None or self.display_qty is None:
                raise ValueError("ICEBERG needs limit_price and display_qty")
            if self.display_qty > self.quantity:
                raise ValueError("display_qty can't be larger than the whole quantity")
            if self.refresh_s < MIN_SLICE_INTERVAL_S:
                raise ValueError(f"ICEBERG looks at the book at most every {MIN_SLICE_INTERVAL_S:g} s")
        return self


TwapCreate = AlgoCreate  # the original name (TWAP was the first algo); a TWAP is kind=TWAP, the default


SliceStatus = Literal["PENDING", "FILLED", "PARTIAL", "UNFILLED", "SKIPPED"]


class TwapSlice(BaseModel):
    index: int  # 1-based
    scheduled_at: datetime
    target_cumulative: float  # the plan: how much should be done after this slice
    sent_at: datetime | None = None
    quantity: float = 0.0  # asked for (catch-up included)
    filled: float = 0.0
    avg_price: float = 0.0
    mid_at_send: float | None = None  # market mid when the slice went out (TWAP benchmark)
    order_id: str | None = None
    venues: list[str] = []
    status: SliceStatus = "PENDING"
    note: str | None = None


class AlgoState(BaseModel):
    id: str
    kind: str = "TWAP"
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AlgoStatus = AlgoStatus.RUNNING
    status_reason: str | None = None
    params: AlgoCreate
    interval_s: float
    nominal_slice: float  # quantity / slices (TWAP), the average planned slice otherwise; on the lot grid
    lot_step: float
    started_at: datetime | None = None
    ended_at: datetime | None = None
    deadline_at: datetime | None = None  # POV / ICEBERG: when it stops (moved on by the time spent paused)
    paused_at: datetime | None = None
    arrival_mid: float | None = None  # NBBO mid when the algo started (the benchmark)
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees_usdt: float = 0.0
    slices: list[TwapSlice] = []
    # How the execution went (cost-positive: + means we paid more / received less)
    arrival_slippage_bps: float | None = None  # vs the mid at start (implementation shortfall)
    interval_twap: float | None = None  # mean market mid across the slice times
    vs_interval_twap_bps: float | None = None
    market_vwap: float | None = None  # the real market's VWAP over the run (all exchanges we watch)
    vs_market_vwap_bps: float | None = None
    market_volume: float | None = None  # POV: real-market volume since the start (base asset)
    participation_actual_pct: float | None = None  # POV: our fills / that volume
    waiting_reason: str | None = None  # POV / ICEBERG: why nothing was sent at the last check
    plan: dict = {}  # kind-specific numbers shown on the page (IS cost/risk, VWAP profile source, ...)
    notes: list[str] = []

    @model_validator(mode="after")
    def _kind_follows_params(self) -> "AlgoState":
        self.kind = self.params.kind.value
        return self

    @property
    def remaining(self) -> float:
        return max(0.0, self.params.quantity - self.filled_quantity)


TwapState = AlgoState  # the original name


class AlgoList(BaseModel):
    algos: list[AlgoState]


class RecommendRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20, examples=["BTC/USDT"])
    side: OrderSide
    quantity: float = Field(..., gt=0)
    urgency: Urgency = Urgency.MEDIUM
    max_duration_s: float = Field(3_600, ge=60, le=86_400, description="The longest the execution may take")
    routing: RoutingStrategy = RoutingStrategy.BEST_PRICE


class AlgoRecommendation(BaseModel):
    """Auto: what to do with an order, and why. ``params`` is ready to start as is."""

    choice: str  # "SINGLE" (one order now), an AlgoKind value, or "NONE" (can't be done as asked)
    params: AlgoCreate | None = None  # the algo to start (None for SINGLE / NONE)
    reasons: list[str] = []
    warnings: list[str] = []
    alternatives: list[str] = []
    metrics: dict = {}  # the numbers the choice is based on (spread, impact now, volume per minute, ...)
