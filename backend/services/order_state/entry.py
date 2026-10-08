"""Order entry: the ONE way an order gets into the system.

The REST endpoint, every execution algorithm, the LLM copilot and every bot call
``OrderEntry.submit``. That is what makes "algos are clients of the router" true in
code, not just on a slide: there is no second path around pre-trade risk, the
funding gate, the kill switch or price protection.

One more gate sits here, before routing: the automation fund (Phase C). A bot may only
use its own pocket of the fund, and a manual order may not use what the fund holds.
With nothing in the fund it never refuses anything.
"""

from collections.abc import Awaitable, Callable

from backend.shared.exceptions import DomainError
from backend.shared.models.order import Order, OrderCreate, OrderStatus

# (order, parent_id) -> why it may not be sent, or None
Guard = Callable[[Order, str | None], Awaitable[str | None]]


class FundReservedError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "FUND_RESERVED")


class OrderEntry:
    def __init__(self, order_manager, routing_engine, guard: Guard | None = None) -> None:
        self._orders = order_manager
        self._router = routing_engine
        self._guard = guard

    def set_guard(self, guard: Guard | None) -> None:
        """The automation fund's gate (set once the fund exists — it needs the wallet and prices)."""
        self._guard = guard

    async def submit(
        self, order_in: OrderCreate, submitted_by: str, parent_id: str | None = None,
    ) -> tuple[Order, DomainError | None]:
        """Create the order and route it.

        Returns the order in its final state and, if it was rejected by a
        gate (risk, kill switch, symbol, the automation fund...), the error that
        rejected it. The order itself is always recorded, rejected or not.
        """
        order = await self._orders.create_order(order_in, submitted_by=submitted_by, parent_id=parent_id)
        if self._guard is not None and (reason := await self._guard(order, parent_id)):
            await self._orders.transition(order.id, OrderStatus.REJECTED, rejection_reason=reason)
            return self._orders.get_order(order.id), FundReservedError(reason)
        try:
            await self._router.route_order(order)
        except DomainError as exc:
            return self._orders.get_order(order.id), exc
        return self._orders.get_order(order.id), None
