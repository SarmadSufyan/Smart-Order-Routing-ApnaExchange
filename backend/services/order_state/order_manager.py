from datetime import UTC, datetime
from uuid import UUID

from backend.exchanges.precision import is_close
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import InvalidStateTransitionError, OrderNotFoundError
from backend.shared.models.order import (
    ChildOrder,
    Order,
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatus,
)
from backend.shared.utils.logger import get_logger

logger = get_logger("order_manager")

TERMINAL = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
# Statuses of an order the gateway was still working on when it stopped.
NOT_YET_SENT = {OrderStatus.PENDING, OrderStatus.VALIDATED, OrderStatus.APPROVED, OrderStatus.ROUTING}


class OrderManager:
    def __init__(self) -> None:
        self._orders: dict[UUID, Order] = {}
        # Orders the routing engine is executing right now. Children are IOC,
        # so a parent left PARTIALLY_FILLED after execution is finished — its
        # remainder expired on the venue — and has nothing left to cancel.
        self._in_flight: set[UUID] = set()

    def get_order(self, order_id: UUID) -> Order:
        order = self._orders.get(order_id)
        if not order:
            raise OrderNotFoundError(str(order_id))
        return order

    def begin_execution(self, order_id: UUID) -> None:
        self._in_flight.add(order_id)

    def end_execution(self, order_id: UUID) -> None:
        self._in_flight.discard(order_id)

    def has_in_flight(self) -> bool:
        return bool(self._in_flight)

    def active_orders(self) -> list[Order]:
        """Orders that could still receive fills (being executed right now)."""
        return [
            o for oid in self._in_flight
            if (o := self._orders.get(oid)) is not None
            and o.status in (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED)
        ]

    def restore(self, orders: list[Order]) -> list[Order]:
        """Load persisted orders after a restart.

        An order the gateway was still working on can't be resumed: say what
        happened to it. Returns the orders whose status had to change.
        """
        fixed: list[Order] = []
        for order in orders:
            if order.status in NOT_YET_SENT:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "Gateway stopped before this order was sent to any venue"
                fixed.append(order)
            elif order.status == OrderStatus.WORKING:
                order.status = OrderStatus.CANCELLED
                order.rejection_reason = ("Gateway stopped while this order was executing — "
                                          "check the exchange and the reconciliation for late fills")
                fixed.append(order)
            self._orders[order.id] = order
        if fixed:
            logger.warning("orders_recovered_after_restart", count=len(fixed))
        return fixed

    def list_orders(
        self,
        status: str | None = None,
        symbol: str | None = None,
        side: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> OrderListResponse:
        filtered = list(self._orders.values())

        if status:
            statuses = {s.strip().upper() for s in status.split(",")}
            filtered = [o for o in filtered if o.status.value in statuses]
        if symbol:
            filtered = [o for o in filtered if o.symbol == symbol.upper()]
        if side:
            filtered = [o for o in filtered if o.side.value == side.upper()]

        filtered.sort(key=lambda o: o.created_at, reverse=True)
        total = len(filtered)
        start = (page - 1) * page_size
        page_items = filtered[start : start + page_size]

        return OrderListResponse(
            orders=[OrderResponse.from_order(o) for o in page_items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=max(1, (total + page_size - 1) // page_size),
        )

    async def create_order(
        self, order_in: OrderCreate, submitted_by: str | None = None, parent_id: str | None = None,
    ) -> Order:
        order = Order(
            symbol=order_in.symbol.upper(),
            side=order_in.side,
            quantity=order_in.quantity,
            order_type=order_in.order_type,
            limit_price=order_in.limit_price,
            strategy=order_in.strategy,
            submitted_by=submitted_by,
            parent_id=parent_id,
        )
        self._orders[order.id] = order
        logger.info(
            "order_created",
            order_id=str(order.id),
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            submitted_by=submitted_by,
        )
        await audit(
            "order_submitted",
            f"{order.side.value} {order.quantity:g} {order.symbol} {order.order_type.value}"
            + (f" limit {order.limit_price:g}" if order.limit_price else "")
            + f" submitted by {submitted_by or 'unknown'}"
            + (f" (slice of {parent_id})" if parent_id else ""),
            actor=submitted_by, order_id=str(order.id), symbol=order.symbol,
            payload=order_in.model_dump(mode="json"),
        )
        return order

    async def transition(self, order_id: UUID, new_status: OrderStatus, **kwargs) -> Order:
        order = self.get_order(order_id)
        if not order.can_transition_to(new_status):
            raise InvalidStateTransitionError(
                str(order_id), order.status.value, new_status.value
            )

        old_status = order.status
        order.status = new_status
        order.updated_at = datetime.now(UTC)

        if "rejection_reason" in kwargs:
            order.rejection_reason = kwargs["rejection_reason"]
        if "child_orders" in kwargs:
            order.child_orders = kwargs["child_orders"]
        if "filled_quantity" in kwargs:
            order.filled_quantity = kwargs["filled_quantity"]
        if "avg_fill_price" in kwargs:
            order.avg_fill_price = kwargs["avg_fill_price"]

        await event_bus.publish(
            "order_update",
            {
                "order_id": str(order.id),
                "old_status": old_status.value,
                "new_status": new_status.value,
                "symbol": order.symbol,
                "side": order.side.value,
                "timestamp": order.updated_at.isoformat(),
            },
        )

        logger.info(
            "order_transitioned",
            order_id=str(order.id),
            from_status=old_status.value,
            to_status=new_status.value,
        )
        return order

    async def add_child_order(self, order_id: UUID, child: ChildOrder) -> Order:
        order = self.get_order(order_id)
        order.child_orders.append(child)
        order.updated_at = datetime.now(UTC)
        return order

    async def update_fill(
        self, order_id: UUID, child_order_id: UUID, fill_qty: float, fill_price: float
    ) -> Order:
        order = self.get_order(order_id)

        for child in order.child_orders:
            if child.id == child_order_id:
                child.filled_quantity = fill_qty
                child.fill_price = fill_price
                # An IOC child can come back partially filled — say so.
                child.status = (
                    OrderStatus.FILLED
                    if fill_qty >= child.quantity or is_close(fill_qty, child.quantity)
                    else OrderStatus.PARTIALLY_FILLED
                )
                child.filled_at = datetime.now(UTC)
                break

        total_filled = sum(c.filled_quantity for c in order.child_orders)
        total_cost = sum(c.filled_quantity * c.fill_price for c in order.child_orders if c.fill_price > 0)
        order.filled_quantity = total_filled
        order.avg_fill_price = round(total_cost / total_filled, 8) if total_filled > 0 else 0.0
        order.updated_at = datetime.now(UTC)

        if order.status in TERMINAL:
            # The parent was cancelled (kill switch / operator) while an IOC
            # child was already at the exchange. The exchange filled it: record
            # the fill — reality wins — but keep the parent's final status.
            logger.warning(
                "fill_after_terminal_status",
                order_id=str(order.id), status=order.status.value, filled=total_filled,
            )
            return order

        # Float sums of lot-rounded quantities need a tolerance, not ==.
        if total_filled >= order.quantity or is_close(total_filled, order.quantity):
            await self.transition(order.id, OrderStatus.FILLED)
        elif total_filled > 0 and order.status == OrderStatus.WORKING:
            await self.transition(order.id, OrderStatus.PARTIALLY_FILLED)

        return order

    async def cancel_order(self, order_id: UUID, reason: str | None = None) -> Order:
        order = self.get_order(order_id)
        if order.status == OrderStatus.PARTIALLY_FILLED and order_id not in self._in_flight:
            raise InvalidStateTransitionError(
                str(order_id), order.status.value, OrderStatus.CANCELLED.value,
                reason="execution is complete — the unfilled remainder already expired on the venue (IOC)",
            )
        if order.status not in (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED):
            raise InvalidStateTransitionError(
                str(order_id), order.status.value, OrderStatus.CANCELLED.value
            )
        if reason:
            return await self.transition(order_id, OrderStatus.CANCELLED, rejection_reason=reason)
        return await self.transition(order_id, OrderStatus.CANCELLED)
