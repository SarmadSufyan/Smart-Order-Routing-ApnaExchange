import time
from datetime import UTC, datetime

import httpx

from backend.gateway.config import settings
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import KillSwitchActiveError, RiskCheckFailedError
from backend.shared.models.market_data import DEFAULT_SYMBOLS
from backend.shared.models.order import Order, OrderStatus
from backend.shared.models.risk import RiskCheckResult
from backend.shared.models.routing import RoutingResult, RoutingStatus
from backend.shared.utils.logger import get_logger

from .strategies.best_price import BestPriceStrategy

logger = get_logger("routing_engine")


class RoutingEngine:
    def __init__(
        self,
        market_data_aggregator,
        risk_engine,
        venue_monitor,
        order_manager,
    ) -> None:
        self._aggregator = market_data_aggregator
        self._risk = risk_engine
        self._venue_monitor = venue_monitor
        self._orders = order_manager
        self._strategy = BestPriceStrategy()
        self._http_client: httpx.AsyncClient | None = None
        self._orders_routed_today: int = 0
        self._total_routing_time_ms: float = 0.0
        self._venue_allocation: dict[str, int] = {}

    async def start(self) -> None:
        self._http_client = httpx.AsyncClient()

    async def stop(self) -> None:
        if self._http_client:
            await self._http_client.aclose()

    @property
    def stats(self) -> dict:
        total = max(self._orders_routed_today, 1)
        return {
            "orders_routed_today": self._orders_routed_today,
            "avg_routing_time_ms": round(self._total_routing_time_ms / total, 2),
            "venue_allocation": {
                vid: round(count / total, 2)
                for vid, count in self._venue_allocation.items()
            },
        }

    async def route_order(self, order: Order) -> RoutingResult:
        start = time.monotonic()

        await self._orders.transition(order.id, OrderStatus.VALIDATED)

        nbbo = await self._aggregator.compute_nbbo(order.symbol)
        mid_price = 0.0
        if nbbo:
            mid_price = (nbbo.best_bid + nbbo.best_ask) / 2

        risk_check = await self._risk.check_order(order, mid_price=mid_price)
        if risk_check.result == RiskCheckResult.REJECTED_KILL_SWITCH:
            await self._orders.transition(
                order.id, OrderStatus.REJECTED, rejection_reason="Kill switch active"
            )
            raise KillSwitchActiveError()
        if risk_check.result != RiskCheckResult.APPROVED:
            reason = risk_check.result.value
            await self._orders.transition(
                order.id, OrderStatus.REJECTED, rejection_reason=reason
            )
            raise RiskCheckFailedError(reason)

        await self._orders.transition(order.id, OrderStatus.APPROVED)

        if not nbbo:
            await self._orders.transition(
                order.id,
                OrderStatus.REJECTED,
                rejection_reason="No market data available",
            )
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No market data available",
                routing_time_ms=(time.monotonic() - start) * 1000,
            )

        routable = self._venue_monitor.get_routable_venues()
        await self._orders.transition(order.id, OrderStatus.ROUTING)

        result = await self._strategy.route(order, nbbo, routable)
        result.routing_time_ms = round((time.monotonic() - start) * 1000, 2)

        if result.status == RoutingStatus.REJECTED:
            await self._orders.transition(
                order.id,
                OrderStatus.REJECTED,
                rejection_reason=result.rejection_reason,
            )
            return result

        for child in result.child_orders:
            await self._orders.add_child_order(order.id, child)

        await self._orders.transition(order.id, OrderStatus.WORKING)

        self._orders_routed_today += 1
        self._total_routing_time_ms += result.routing_time_ms
        for child in result.child_orders:
            self._venue_allocation[child.venue_id] = (
                self._venue_allocation.get(child.venue_id, 0) + 1
            )

        for child in result.child_orders:
            await self._send_to_venue(order, child)

        return result

    async def _send_to_venue(self, order: Order, child) -> None:
        venue_urls = settings.get_venue_urls()
        url = venue_urls.get(child.venue_id)
        if not url or not self._http_client:
            self._venue_monitor.record_order_result(child.venue_id, filled=False)
            return

        try:
            start = time.monotonic()
            resp = await self._http_client.post(
                f"{url}/execute-order",
                json={
                    "order_id": str(child.id),
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": child.quantity,
                    "price": child.price,
                    "order_type": order.order_type.value,
                },
                timeout=5.0,
            )
            latency_ms = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                fill_price = data.get("fill_price", child.price)
                fill_qty = data.get("filled_quantity", child.quantity)

                await self._orders.update_fill(
                    order.id, child.id, fill_qty, fill_price
                )
                self._risk.position_tracker.update_position(
                    order.symbol, order.side, fill_qty, fill_price
                )
                self._venue_monitor.record_order_result(child.venue_id, filled=True)

                await event_bus.publish(
                    "execution_report",
                    {
                        "order_id": str(order.id),
                        "child_order_id": str(child.id),
                        "venue_id": child.venue_id,
                        "exec_type": "FILL",
                        "symbol": order.symbol,
                        "side": order.side.value,
                        "quantity": fill_qty,
                        "price": fill_price,
                        "venue_latency_ms": round(latency_ms, 2),
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            else:
                self._venue_monitor.record_order_result(child.venue_id, filled=False)
                await event_bus.publish(
                    "execution_report",
                    {
                        "order_id": str(order.id),
                        "child_order_id": str(child.id),
                        "venue_id": child.venue_id,
                        "exec_type": "REJECT",
                        "symbol": order.symbol,
                        "side": order.side.value,
                        "quantity": child.quantity,
                        "price": 0,
                        "venue_latency_ms": round(latency_ms, 2),
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )

        except Exception as e:
            self._venue_monitor.record_order_result(child.venue_id, filled=False)
            logger.error(
                "venue_send_failed",
                venue_id=child.venue_id,
                child_order_id=str(child.id),
                error=str(e),
            )
