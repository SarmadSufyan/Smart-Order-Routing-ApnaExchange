from dataclasses import dataclass

from backend.gateway.websocket.ws_manager import WebSocketManager
from backend.services.market_data.aggregator import MarketDataAggregator
from backend.services.market_data.collector import MarketDataCollector
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.routing_engine.engine import RoutingEngine
from backend.services.venue_monitor.health_checker import VenueHealthMonitor


@dataclass
class Services:
    venue_monitor: VenueHealthMonitor
    market_data_collector: MarketDataCollector
    market_data_aggregator: MarketDataAggregator
    order_manager: OrderManager
    risk_engine: PreTradeRiskEngine
    routing_engine: RoutingEngine
    ws_manager: WebSocketManager


_services: Services | None = None


def init_services() -> Services:
    global _services

    venue_monitor = VenueHealthMonitor()
    collector = MarketDataCollector()
    aggregator = MarketDataAggregator(collector, venue_monitor)
    order_manager = OrderManager()

    kill_switch = KillSwitch()
    position_tracker = PositionTracker()
    risk_engine = PreTradeRiskEngine(kill_switch, position_tracker)

    routing_engine = RoutingEngine(
        market_data_aggregator=aggregator,
        risk_engine=risk_engine,
        venue_monitor=venue_monitor,
        order_manager=order_manager,
    )

    ws_manager = WebSocketManager()

    _services = Services(
        venue_monitor=venue_monitor,
        market_data_collector=collector,
        market_data_aggregator=aggregator,
        order_manager=order_manager,
        risk_engine=risk_engine,
        routing_engine=routing_engine,
        ws_manager=ws_manager,
    )
    return _services


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Services not initialized. Call init_services() first.")
    return _services
