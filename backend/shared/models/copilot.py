"""LLM copilot: a sentence in, a structured proposal out, an order only on confirm.

``Intent`` is what the model fills in. It is untrusted: the copilot re-checks
every field in code and builds the exact ``OrderCreate`` / ``TwapCreate`` that
a confirmation would submit, so what the trader sees is what gets sent.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.models.order import OrderCreate, OrderSide, OrderType
from backend.shared.models.strategy import TwapCreate


class IntentAction(str, Enum):
    ORDER = "order"  # one order, now
    TWAP = "twap"  # spread over a time window
    CLARIFY = "clarify"  # something needed is missing or ambiguous
    UNSUPPORTED = "unsupported"  # not a single buy/sell of one listed coin


class Intent(BaseModel):
    """The model's reading of one request. Every field is re-validated in code."""

    model_config = ConfigDict(extra="forbid")

    action: IntentAction
    side: OrderSide | None = None
    base_asset: str | None = Field(None, max_length=12)
    quantity: float | None = Field(None, gt=0)  # in coins
    notional_usdt: float | None = Field(None, gt=0)  # "$50 of BTC"
    order_type: OrderType | None = None
    limit_price: float | None = Field(None, gt=0)
    duration_s: float | None = Field(None, gt=0)
    slices: int | None = Field(None, ge=2, le=500)
    message: str | None = Field(None, max_length=300)  # the question or refusal reason


# JSON schema sent to the model (structured output). Hand-written rather than
# Intent.model_json_schema(): model APIs accept a subset of JSON Schema, and a
# flat object of nullable fields is what they handle reliably. A unit test
# keeps the property names in step with Intent.
def _nullable(kind: str, **extra) -> dict:
    return {"type": [kind, "null"], **extra}


INTENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [a.value for a in IntentAction]},
        "side": _nullable("string", enum=["BUY", "SELL", None]),
        "base_asset": _nullable("string"),
        "quantity": _nullable("number"),
        "notional_usdt": _nullable("number"),
        "order_type": _nullable("string", enum=["MARKET", "LIMIT", None]),
        "limit_price": _nullable("number"),
        "duration_s": _nullable("number"),
        "slices": _nullable("integer"),
        "message": _nullable("string"),
    },
    "required": ["action", "side", "base_asset", "quantity", "notional_usdt", "order_type",
                 "limit_price", "duration_s", "slices", "message"],
}


class CopilotRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, examples=["buy $50 of BTC over 2 minutes"])


class ProposalAction(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=40)


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"  # waiting for the trader's confirmation
    CLARIFY = "CLARIFY"  # the copilot asks a question; nothing to confirm
    REFUSED = "REFUSED"  # unsupported request; nothing to confirm
    CONFIRMED = "CONFIRMED"  # submitted (the order / TWAP has its own status)
    FAILED = "FAILED"  # confirmed, but stopped by a gate (risk, kill switch, price moved...)
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class Proposal(BaseModel):
    id: str
    status: ProposalStatus
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    text: str  # the trader's original words
    message: str  # plain English: the proposal, the question or the refusal
    kind: Literal["order", "twap"] | None = None
    order: OrderCreate | None = None  # exactly what a confirmation submits
    twap: TwapCreate | None = None
    reference_mid: float | None = None  # NBBO mid used for conversion and estimates
    estimated_notional_usdt: float | None = None
    conversion: str | None = None  # "$50 / mid $85,070.02 = 0.00058775 -> 0.00058 BTC (lot 0.00001)"
    warnings: list[str] = []
    model: str | None = None  # which LLM read the request
    result_order_id: str | None = None
    result_algo_id: str | None = None
    result_message: str | None = None


class CopilotStatus(BaseModel):
    available: bool
    provider: str
    model: str | None = None
    reason: str | None = None  # why it is unavailable
