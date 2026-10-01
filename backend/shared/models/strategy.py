"""Algo (parent) orders — the strategy layer's view of an execution.

A TWAP is a parent order that the bot slices into ordinary child orders over
time. Every slice is a normal order through the normal entry point, so it
carries its own routing proof and audit trail; this model only adds the
schedule and how the whole execution went.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.shared.models.order import OrderSide

MIN_SLICE_INTERVAL_S = 3.0  # a slice takes up to ~1-2 s on a real venue, plus a balance refresh


class AlgoStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"  # by an operator, the kill switch, a restart, or repeated rejections
    COMPLETED = "COMPLETED"  # everything filled
    EXPIRED = "EXPIRED"  # schedule ended with some quantity unfilled
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"  # internal error — see status_reason


ACTIVE_STATUSES = {AlgoStatus.RUNNING, AlgoStatus.PAUSED}


class TwapCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20, examples=["BTC/USDT"])
    side: OrderSide
    quantity: float = Field(..., gt=0, examples=[0.001])
    # The real floor is the slice interval (validated below), not the duration.
    duration_s: float = Field(..., gt=0, le=86_400, description="Execution window in seconds")
    slices: int = Field(..., ge=2, le=500)
    limit_price: float | None = Field(None, gt=0, description="Worst price for any slice (optional)")
    jitter_pct: float = Field(0.0, ge=0, le=50, description="Randomise slice times by ± this % of the interval")

    @model_validator(mode="after")
    def _interval_is_workable(self) -> "TwapCreate":
        if self.duration_s / self.slices < MIN_SLICE_INTERVAL_S:
            raise ValueError(
                f"{self.slices} slices in {self.duration_s:g} s is one every "
                f"{self.duration_s / self.slices:.1f} s; the minimum is {MIN_SLICE_INTERVAL_S:g} s"
            )
        return self


SliceStatus = Literal["PENDING", "FILLED", "PARTIAL", "UNFILLED", "SKIPPED"]


class TwapSlice(BaseModel):
    index: int  # 1-based
    scheduled_at: datetime
    target_cumulative: float  # the schedule: how much should be done after this slice
    sent_at: datetime | None = None
    quantity: float = 0.0  # asked for (catch-up included)
    filled: float = 0.0
    avg_price: float = 0.0
    mid_at_send: float | None = None  # market mid when the slice went out (TWAP benchmark)
    order_id: str | None = None
    venues: list[str] = []
    status: SliceStatus = "PENDING"
    note: str | None = None


class TwapState(BaseModel):
    id: str
    kind: str = "TWAP"
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AlgoStatus = AlgoStatus.RUNNING
    status_reason: str | None = None
    params: TwapCreate
    interval_s: float
    nominal_slice: float  # quantity / slices, on the lot grid
    lot_step: float
    started_at: datetime | None = None
    ended_at: datetime | None = None
    arrival_mid: float | None = None  # NBBO mid when the TWAP started (the benchmark)
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees_usdt: float = 0.0
    slices: list[TwapSlice] = []
    # How the execution went (cost-positive: + means we paid more / received less)
    arrival_slippage_bps: float | None = None  # vs the mid at start (implementation shortfall)
    interval_twap: float | None = None  # mean market mid across the slice times
    vs_interval_twap_bps: float | None = None
    notes: list[str] = []

    @property
    def remaining(self) -> float:
        return max(0.0, self.params.quantity - self.filled_quantity)


class AlgoList(BaseModel):
    algos: list[TwapState]
