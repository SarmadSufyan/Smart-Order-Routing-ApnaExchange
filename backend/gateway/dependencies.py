from dataclasses import dataclass

from backend.exchanges.registry import ExchangeRegistry
from backend.gateway.config import settings
from backend.gateway.websocket.ws_manager import WebSocketManager
from backend.services.agents import Copilot, build_llm_client
from backend.services.algos import TwapEngine
from backend.services.audit import AuditRecorder
from backend.services.consensus.client import ConsensusClient, parse_cluster
from backend.services.market_data.aggregator import MarketDataAggregator
from backend.services.market_data.collector import MarketDataCollector
from backend.services.order_state.entry import OrderEntry
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.batch import RiskBatchRunner
from backend.services.risk_engine.controls import RiskControls
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.risk_engine.preflight import PreflightService
from backend.services.risk_engine.risk_monitor import RiskMonitor
from backend.services.risk_engine.volatility import VolatilityTracker
from backend.services.routing_engine.engine import RoutingEngine
from backend.services.venue_monitor.coherence import CoherenceMonitor
from backend.services.venue_monitor.health_checker import VenueHealthMonitor
from backend.services.wallet import PnLService, WalletService
from backend.shared.database.store import AuditStore
from backend.shared.models.risk import RiskLimits


@dataclass
class Services:
    exchange_registry: ExchangeRegistry
    venue_monitor: VenueHealthMonitor
    coherence_monitor: CoherenceMonitor
    market_data_collector: MarketDataCollector
    market_data_aggregator: MarketDataAggregator
    order_manager: OrderManager
    order_entry: OrderEntry
    risk_engine: PreTradeRiskEngine
    routing_engine: RoutingEngine
    twap_engine: TwapEngine
    wallet_service: WalletService
    pnl_service: PnLService
    risk_monitor: RiskMonitor
    audit_store: AuditStore
    audit_recorder: AuditRecorder
    ws_manager: WebSocketManager
    copilot: Copilot
    volatility: VolatilityTracker
    preflight: PreflightService
    risk_controls: RiskControls


_services: Services | None = None


def default_risk_limits() -> RiskLimits:
    return RiskLimits(
        max_order_size=settings.default_max_order_size,
        max_order_notional=settings.default_max_order_notional,
        max_position_per_symbol=settings.default_max_position,
        max_notional_exposure=settings.default_max_notional,
        max_orders_per_second=settings.default_max_orders_per_second,
        max_algo_notional_usdt=settings.default_max_algo_notional,
        max_trading_loss_usdt=settings.default_max_trading_loss,
        max_asset_concentration_pct=settings.default_max_asset_concentration_pct,
        max_venue_concentration_pct=settings.default_max_venue_concentration_pct,
    )


def init_services() -> Services:
    global _services

    registry = ExchangeRegistry.from_settings(settings)
    venue_monitor = VenueHealthMonitor(
        registry,
        check_interval_s=settings.venue_health_check_interval_s,
        feed_staleness_threshold_ms=settings.feed_staleness_threshold_ms,
    )
    collector = MarketDataCollector(
        registry,
        settings.symbol_list,
        publish_interval_ms=settings.market_data_publish_interval_ms,
        staleness_threshold_ms=settings.feed_staleness_threshold_ms,
    )
    aggregator = MarketDataAggregator(collector, venue_monitor)
    coherence = CoherenceMonitor(
        registry,
        collector,
        settings.symbol_list,
        threshold_bps=settings.coherence_threshold_bps,
        interval_s=settings.coherence_check_interval_s,
    )
    venue_monitor.set_coherence(coherence)
    order_manager = OrderManager()
    store = AuditStore(settings.resolved_database_path)

    kill_switch = KillSwitch()
    position_tracker = PositionTracker()
    # Pre-flight batches with a stress test run on a pool of worker processes.
    batch_runner = RiskBatchRunner(settings.risk_pool_workers, settings.risk_pool_min_orders)
    risk_engine = PreTradeRiskEngine(kill_switch, position_tracker, default_risk_limits(), batch_runner)

    wallet_service = WalletService(
        registry,
        collector,
        settings.symbol_list,
        mode=settings.exchange_mode,
        refresh_interval_s=settings.wallet_refresh_interval_s,
    )
    pnl_service = PnLService(
        registry, wallet_service, order_manager, position_tracker, store=store, mode=settings.exchange_mode,
    )
    routing_engine = RoutingEngine(
        market_data_aggregator=aggregator,
        market_data_collector=collector,
        risk_engine=risk_engine,
        venue_monitor=venue_monitor,
        order_manager=order_manager,
        registry=registry,
        depth_levels=settings.routing_depth_levels,
        price_protection_bps=settings.child_price_protection_bps,
        coherence=coherence,
        wallet=wallet_service,  # per-venue balances cap each venue's slice
        trading_block_s=settings.trading_block_cooldown_s,
    )
    # The one way to change the kill switch, limits and venue blacklist —
    # through the RAFT cluster's leader when RAFT_ENABLED=true.
    consensus = ConsensusClient(
        parse_cluster(settings.raft_cluster_nodes), write_timeout_s=settings.raft_write_timeout_s,
    ) if settings.raft_enabled else None
    risk_controls = RiskControls(
        risk_engine, order_manager, registry, venue_monitor, client=consensus,
        default_limits=default_risk_limits(), poll_interval_s=settings.raft_poll_interval_s,
        mode=settings.exchange_mode,
    )
    risk_monitor = RiskMonitor(
        risk_engine, pnl_service, wallet_service, order_manager, registry,
        interval_s=settings.risk_monitor_interval_s, controls=risk_controls,
    )
    # The one way orders enter: the REST endpoint and every algo use it.
    order_entry = OrderEntry(order_manager, routing_engine)
    twap_engine = TwapEngine(
        order_entry, order_manager, aggregator, collector, routing_engine, registry,
        risk_engine, wallet_service, store=store,
    )
    # The copilot only proposes; a confirmed proposal goes through order_entry
    # (or the TWAP engine, which uses order_entry for every slice).
    llm, llm_unavailable = build_llm_client(settings)
    copilot = Copilot(
        llm, order_entry, twap_engine, aggregator, collector, routing_engine, registry, risk_engine,
        unavailable_reason=llm_unavailable, ttl_s=settings.copilot_proposal_ttl_s,
        max_price_move_bps=settings.copilot_max_price_move_bps,
    )

    volatility = VolatilityTracker(aggregator, settings.symbol_list, settings.volatility_sample_interval_s)

    _services = Services(
        exchange_registry=registry,
        venue_monitor=venue_monitor,
        coherence_monitor=coherence,
        market_data_collector=collector,
        market_data_aggregator=aggregator,
        order_manager=order_manager,
        order_entry=order_entry,
        risk_engine=risk_engine,
        routing_engine=routing_engine,
        twap_engine=twap_engine,
        wallet_service=wallet_service,
        pnl_service=pnl_service,
        risk_monitor=risk_monitor,
        audit_store=store,
        audit_recorder=AuditRecorder(store, order_manager, kill_switch),
        ws_manager=WebSocketManager(),
        copilot=copilot,
        volatility=volatility,
        preflight=PreflightService(
            risk_engine, aggregator, settings.symbol_list, volatility,
            stress_paths=settings.risk_stress_paths, stress_horizon_min=settings.risk_stress_horizon_min,
        ),
        risk_controls=risk_controls,
    )
    return _services


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Services not initialized. Call init_services() first.")
    return _services
