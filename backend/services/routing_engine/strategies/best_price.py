from uuid import uuid4

from backend.shared.models.market_data import NBBO
from backend.shared.models.order import ChildOrder, Order, OrderSide
from backend.shared.models.routing import RoutingResult, RoutingStatus

from .base import RoutingStrategy


class BestPriceStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "best_price"

    async def route(
        self,
        order: Order,
        nbbo: NBBO,
        routable_venues: list[str],
    ) -> RoutingResult:
        eligible = []
        for vid, quote in nbbo.venue_quotes.items():
            if vid not in routable_venues:
                continue
            if order.side == OrderSide.BUY:
                eligible.append((vid, quote.ask_price, quote.ask_size))
            else:
                eligible.append((vid, quote.bid_price, quote.bid_size))

        if not eligible:
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No eligible venues with liquidity",
            )

        if order.side == OrderSide.BUY:
            eligible.sort(key=lambda x: x[1])
        else:
            eligible.sort(key=lambda x: x[1], reverse=True)

        child_orders: list[ChildOrder] = []
        remaining = order.quantity

        for vid, price, available_size in eligible:
            if remaining <= 0:
                break
            if available_size <= 0 or price <= 0:
                continue

            alloc = min(remaining, available_size)
            child_orders.append(
                ChildOrder(
                    id=uuid4(),
                    parent_order_id=order.id,
                    venue_id=vid,
                    quantity=alloc,
                    price=price,
                )
            )
            remaining -= alloc

        if not child_orders:
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No available liquidity across venues",
            )

        if remaining > 0:
            return RoutingResult(
                status=RoutingStatus.PARTIAL,
                child_orders=child_orders,
                remaining_quantity=remaining,
            )

        return RoutingResult(
            status=RoutingStatus.SUCCESS,
            child_orders=child_orders,
        )
