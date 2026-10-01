"""Order entry: the ONE way an order gets into the system.

The REST endpoint, the TWAP bot and (later) the LLM copilot all call
``OrderEntry.submit``. That is what makes "algos are clients of the router"
true in code, not just on a slide: there is no second path around pre-trade
risk, the funding gate, the kill switch or price protection.
"""

from backend.shared.exceptions import DomainError
from backend.shared.models.order import Order, OrderCreate


class OrderEntry:
    def __init__(self, order_manager, routing_engine) -> None:
        self._orders = order_manager
        self._router = routing_engine

    async def submit(
        self, order_in: OrderCreate, submitted_by: str, parent_id: str | None = None,
    ) -> tuple[Order, DomainError | None]:
        """Create the order and route it.

        Returns the order in its final state and, if it was rejected by a
        gate (risk, kill switch, symbol...), the error that rejected it. The
        order itself is always recorded, rejected or not.
        """
        order = await self._orders.create_order(order_in, submitted_by=submitted_by, parent_id=parent_id)
        try:
            await self._router.route_order(order)
        except DomainError as exc:
            return self._orders.get_order(order.id), exc
        return self._orders.get_order(order.id), None
