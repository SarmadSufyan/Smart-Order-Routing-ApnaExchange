from datetime import UTC, datetime
from uuid import UUID

from backend.shared.events.event_bus import event_bus
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


class OrderManager:
    def __init__(self) -> None:
        self._orders: dict[UUID, Order] = {}

    def get_order(self, order_id: UUID) -> Order:
        order = self._orders.get(order_id)
        if not order:
            raise OrderNotFoundError(str(order_id))
        return order

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

    async def create_order(self, order_in: OrderCreate) -> Order:
        order = Order(
            symbol=order_in.symbol.upper(),
            side=order_in.side,
            quantity=order_in.quantity,
            order_type=order_in.order_type,
            limit_price=order_in.limit_price,
            strategy=order_in.strategy,
        )
        self._orders[order.id] = order
        logger.info(
            "order_created",
            order_id=str(order.id),
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
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
                child.status = OrderStatus.FILLED
                child.filled_at = datetime.now(UTC)
                break

        total_filled = sum(c.filled_quantity for c in order.child_orders)
        total_cost = sum(c.filled_quantity * c.fill_price for c in order.child_orders if c.fill_price > 0)
        order.filled_quantity = total_filled
        order.avg_fill_price = round(total_cost / total_filled, 6) if total_filled > 0 else 0.0
        order.updated_at = datetime.now(UTC)

        if total_filled >= order.quantity:
            await self.transition(order.id, OrderStatus.FILLED)
        elif total_filled > 0 and order.status == OrderStatus.WORKING:
            await self.transition(order.id, OrderStatus.PARTIALLY_FILLED)

        return order

    async def cancel_order(self, order_id: UUID) -> Order:
        order = self.get_order(order_id)
        if order.status not in (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED):
            raise InvalidStateTransitionError(
                str(order_id), order.status.value, OrderStatus.CANCELLED.value
            )
        return await self.transition(order_id, OrderStatus.CANCELLED)
