"""LLM copilot: a message in, a structured reply out — an answer, or a proposal that acts only on confirm.

``Intent`` is what the model fills in. It is untrusted: the copilot re-checks
every field in code and builds the exact ``OrderCreate`` / ``AlgoCreate`` / bot
parameters that a confirmation would submit, so what the trader sees is what
gets sent. Answers and analyses come from numbers the code computed; the model
only words them.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.models.automation import AdvisorParams, CycleParams
from backend.shared.models.order import OrderCreate, OrderSide, OrderType
from backend.shared.models.strategy import AlgoCreate, TwapCreate


class IntentAction(str, Enum):
    ORDER = "order"  # one order, now
    TWAP = "twap"  # spread over a time window
    ALGO = "algo"  # worked by a named execution algorithm (VWAP, IS, POV, ICEBERG)
    ANALYZE = "analyze"  # the trader wants a view / a trade suggestion on a coin (or on all of them)
    QUESTION = "question"  # a question about the market, the portfolio, risk, research, bots, the system...
    START_BOT = "start_bot"  # start a profit-cycle bot or the advisor agent
    CLARIFY = "clarify"  # something needed is missing or ambiguous
    UNSUPPORTED = "unsupported"  # none of the above


class Topic(str, Enum):
    MARKET = "market"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    RESEARCH = "research"
    TECHNIQUES = "techniques"
    ARBITRAGE = "arbitrage"
    BOTS = "bots"
    SYSTEM = "system"
    EDUCATION = "education"  # a general trading concept: no system data needed


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
    algo_kind: Literal["VWAP", "IS", "POV", "ICEBERG"] | None = None
    urgency: Literal["LOW", "MEDIUM", "HIGH"] | None = None  # IS: how front-loaded
    participation_pct: float | None = Field(None, gt=0, le=50)  # POV
    display_qty: float | None = Field(None, gt=0)  # ICEBERG: coins shown at once
    topic: Topic | None = None  # QUESTION
    bot_kind: Literal["cycle", "advisor"] | None = None  # START_BOT
    budget_usdt: float | None = Field(None, gt=0)  # START_BOT: taken from the automation fund
    threshold_pct: float | None = Field(None, gt=0, le=20)  # START_BOT cycle: profit over both fees
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
        "algo_kind": _nullable("string", enum=["VWAP", "IS", "POV", "ICEBERG", None]),
        "urgency": _nullable("string", enum=["LOW", "MEDIUM", "HIGH", None]),
        "participation_pct": _nullable("number"),
        "display_qty": _nullable("number"),
        "topic": _nullable("string", enum=[t.value for t in Topic] + [None]),
        "bot_kind": _nullable("string", enum=["cycle", "advisor", None]),
        "budget_usdt": _nullable("number"),
        "threshold_pct": _nullable("number"),
        "message": _nullable("string"),
    },
    "required": ["action", "side", "base_asset", "quantity", "notional_usdt", "order_type",
                 "limit_price", "duration_s", "slices", "algo_kind", "urgency", "participation_pct",
                 "display_qty", "topic", "bot_kind", "budget_usdt", "threshold_pct", "message"],
}


class CopilotRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, examples=["buy $50 of BTC over 2 minutes"])
    # Sandbox only: an order or algorithm the copilot proposes may run without a click (up to
    # COPILOT_AUTO_MAX_USDT, and only without warnings). Ignored with real money.
    auto: bool = False


class ProposalAction(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=40)


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"  # waiting for the trader's confirmation
    ANSWERED = "ANSWERED"  # an answer or an analysis; nothing to confirm
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
    kind: Literal["order", "twap", "algo", "bot"] | None = None
    order: OrderCreate | None = None  # exactly what a confirmation submits
    twap: TwapCreate | None = None
    algo: AlgoCreate | None = None  # VWAP / IS / POV / ICEBERG
    cycle: CycleParams | None = None  # a profit-cycle bot to start
    advisor: AdvisorParams | None = None  # the advisor agent to start
    answer: str | None = None  # the copilot's answer in plain words (questions and analyses)
    analysis: dict | None = None  # the analyst's verdict and its evidence (shown as a card)
    topic: str | None = None
    auto: bool = False  # Auto was requested
    auto_executed: bool = False  # ...and the proposal ran without a click (sandbox only)
    reference_mid: float | None = None  # NBBO mid used for conversion and estimates
    estimated_notional_usdt: float | None = None
    conversion: str | None = None  # "$50 / mid $85,070.02 = 0.00058775 -> 0.00058 BTC (lot 0.00001)"
    warnings: list[str] = []
    model: str | None = None  # which LLM read the request
    result_order_id: str | None = None
    result_algo_id: str | None = None
    result_bot_id: str | None = None
    result_message: str | None = None


class CopilotStatus(BaseModel):
    available: bool
    provider: str
    model: str | None = None
    reason: str | None = None  # why it is unavailable
    environment: str = "sandbox"  # sandbox | real funds
    auto_allowed: bool = False  # Auto works in the sandbox only
    auto_max_usdt: float = 0.0
    analysis_available: bool = False  # the analyst (works even without a language model)
