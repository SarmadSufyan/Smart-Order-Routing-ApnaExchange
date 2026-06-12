from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from backend.shared.models.order import OrderSide


class ExecType(str, Enum):
    FILL = "FILL"
    PARTIAL = "PARTIAL"
    REJECT = "REJECT"
    CANCEL = "CANCEL"


class ExecutionReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    child_order_id: UUID | None = None
    venue_id: str
    exec_type: ExecType
    symbol: str
    side: OrderSide
    quantity: float
    price: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    venue_latency_ms: float = 0.0
    rejection_reason: str | None = None


class ExecutionReportResponse(BaseModel):
    reports: list[ExecutionReport]
    total: int
    page: int
    page_size: int
