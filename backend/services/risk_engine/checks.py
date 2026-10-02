"""The pre-trade checks as ONE pure function, shared by the live order gate
(``PreTradeRiskEngine``) and the risk batch (``batch.py``, other processes).

Pure = no I/O, no clock, no shared objects: everything a check needs comes
in a ``RiskContext`` snapshot. That is what lets the same code run in a
worker process, and it means the gate and a pre-flight can never disagree
about what a check does.

Checks run in order and stop at the first failure:
    kill_switch -> size_limit -> order_notional -> rate_limit
    -> position_limit -> notional_limit -> symbol_restriction
"""

from dataclasses import dataclass

from backend.shared.models.risk import RiskCheckResult, RiskLimits


@dataclass(frozen=True, slots=True)
class OrderSpec:
    """The three facts about an order the checks look at (cheap to pickle)."""

    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: float


@dataclass(frozen=True, slots=True)
class RiskContext:
    """The state an order meets: limits, kill switch, positions."""

    limits: RiskLimits
    kill_switch_active: bool
    net_position: float  # current net position in the order's symbol (base units)
    total_notional: float  # current total exposure, USDT
    # Orders approved in the last second. None = skip the rate check: a
    # pre-flight sends nothing, and the live gate checks every real order.
    orders_last_second: int | None = None


@dataclass(slots=True)
class CheckOutcome:
    result: RiskCheckResult
    passed: list[str]
    failed: list[str]
    message: str | None

    @property
    def approved(self) -> bool:
        return self.result == RiskCheckResult.APPROVED


def evaluate_order(ctx: RiskContext, order: OrderSpec, mid_price: float) -> CheckOutcome:
    """Run every check for one order. Deterministic: same input, same answer."""
    lim = ctx.limits
    passed: list[str] = []

    def fail(result: RiskCheckResult, check: str, message: str) -> CheckOutcome:
        return CheckOutcome(result, passed, [check], message)

    if ctx.kill_switch_active:
        return fail(RiskCheckResult.REJECTED_KILL_SWITCH, "kill_switch", "Kill switch is active")
    passed.append("kill_switch")

    if order.quantity > lim.max_order_size:
        return fail(RiskCheckResult.REJECTED_SIZE_LIMIT, "size_limit",
                    f"Quantity {order.quantity:g} exceeds the max order size {lim.max_order_size:g}")
    passed.append("size_limit")

    order_notional = order.quantity * mid_price
    if mid_price > 0 and order_notional > lim.max_order_notional:
        return fail(RiskCheckResult.REJECTED_ORDER_NOTIONAL_LIMIT, "order_notional",
                    f"Order notional ${order_notional:,.2f} exceeds the per-order limit ${lim.max_order_notional:,.2f}")
    passed.append("order_notional")

    if ctx.orders_last_second is not None:
        if ctx.orders_last_second >= lim.max_orders_per_second:
            return fail(RiskCheckResult.REJECTED_RATE_LIMIT, "rate_limit",
                        f"More than {lim.max_orders_per_second} orders in the last second")
        passed.append("rate_limit")

    signed = order.quantity if order.side == "BUY" else -order.quantity
    projected = abs(ctx.net_position + signed)
    if projected > lim.max_position_per_symbol:
        return fail(RiskCheckResult.REJECTED_POSITION_LIMIT, "position_limit",
                    f"Projected {order.symbol} position {projected:g} exceeds {lim.max_position_per_symbol:g}")
    passed.append("position_limit")

    if mid_price > 0:
        total = ctx.total_notional + order_notional
        if total > lim.max_notional_exposure:
            return fail(RiskCheckResult.REJECTED_NOTIONAL_LIMIT, "notional_limit",
                        f"Total exposure ${total:,.2f} would exceed ${lim.max_notional_exposure:,.2f}")
        passed.append("notional_limit")

    if order.symbol in lim.restricted_symbols:
        return fail(RiskCheckResult.REJECTED_SYMBOL_RESTRICTED, "symbol_restriction",
                    f"Symbol {order.symbol} is restricted")
    passed.append("symbol_restriction")

    return CheckOutcome(RiskCheckResult.APPROVED, passed, [], None)
