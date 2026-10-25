from dataclasses import dataclass

from backend.exchanges.fees import LiveFees
from backend.exchanges.registry import ExchangeRegistry
from backend.gateway.config import settings
from backend.gateway.websocket.ws_manager import WebSocketManager
from backend.services.agents import Analyst, Copilot, build_llm_client
from backend.services.alerts import DiscordAlerts, TelegramAlerts
from backend.services.algos import AlgoEngine
from backend.services.arbitrage import ArbitrageDetector, parse_fee_schedule
from backend.services.audit import AuditRecorder
from backend.services.automation import AutomationEngine, AutomationFund, PracticeLab
from backend.services.consensus.client import ConsensusClient, parse_cluster
from backend.services.consensus.wire import MessageSigner
from backend.services.market_data.aggregator import MarketDataAggregator
from backend.services.market_data.collector import MarketDataCollector
from backend.services.market_data.history import MarketHistory
from backend.services.order_state.entry import OrderEntry
from backend.services.order_state.order_manager import OrderManager
from backend.services.research.candles import CandleStore, CandleSync
from backend.services.research.service import ResearchService
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
from backend.shared.models.risk import RiskCeilings, RiskLimits


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
    algo_engine: AlgoEngine
    market_history: MarketHistory
    research: ResearchService
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
    arbitrage: ArbitrageDetector
    automation: AutomationEngine
    alerts: DiscordAlerts | TelegramAlerts | None = None


_services: Services | None = None


def real_money_ceilings() -> RiskCeilings | None:
    """The .env caps on real money (None on testnet): no limit in force is ever above them."""
    if not settings.is_real_money:
        return None
    return RiskCeilings(
        max_order_notional=settings.mainnet_max_order_usdt,
        max_algo_notional_usdt=settings.mainnet_max_algo_usdt,
        max_notional_exposure=settings.mainnet_max_exposure_usdt,
        max_trading_loss_usdt=settings.mainnet_max_trading_loss_usdt,
    )


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
    risk_engine = PreTradeRiskEngine(kill_switch, position_tracker, default_risk_limits(), batch_runner,
                                     ceilings=real_money_ceilings())

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
    # Taker fees per venue: the public base tier, replaced by the account's own rate once an exchange reports
    # it (real money only). One live view shared by the router, the bots and the lab.
    live_fees = LiveFees(parse_fee_schedule(settings.arb_taker_fees_bps),
                         lambda vid: getattr(registry.get(vid), "account_taker_fee_bps", None))
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
        # Venues are ranked by price + taker fee (the public table, or the account's own rate on real money).
        taker_fees_bps=live_fees,
        sweep_band_bps=settings.sweep_band_bps,
    )
    # The one way to change the kill switch, limits and venue blacklist —
    # through the RAFT cluster's leader when RAFT_ENABLED=true.
    if settings.raft_enabled and (problem := settings.raft_secret_problem()):
        raise RuntimeError(problem)
    consensus = ConsensusClient(
        parse_cluster(settings.raft_cluster_nodes), write_timeout_s=settings.raft_write_timeout_s,
        signer=MessageSigner(settings.raft_shared_secret.get_secret_value()),  # checked just above
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
    # Real-market candles (public data) for VWAP profiles, POV volume and the market-VWAP benchmark.
    market_history = MarketHistory(registry)
    volatility = VolatilityTracker(aggregator, settings.symbol_list, settings.volatility_sample_interval_s)
    # Every execution algorithm (TWAP, VWAP, IS, POV, ICEBERG): each child goes through order_entry.
    algo_engine = AlgoEngine(
        order_entry, order_manager, aggregator, collector, routing_engine, registry,
        risk_engine, wallet_service, store=store, history=market_history, volatility=volatility,
    )
    # The copilot only proposes; a confirmed proposal goes through order_entry
    # (or the TWAP engine, which uses order_entry for every slice).
    llm, llm_unavailable = build_llm_client(settings)
    copilot = Copilot(
        llm, order_entry, algo_engine, aggregator, collector, routing_engine, registry, risk_engine,
        unavailable_reason=llm_unavailable, ttl_s=settings.copilot_proposal_ttl_s,
        max_price_move_bps=settings.copilot_max_price_move_bps,
    )

    # Detection only: it gets read-only lookups (books, balances, which venues can
    # execute) — no adapter, router or order entry, so it can't trade.
    arbitrage = ArbitrageDetector(
        collector, venue_monitor, store, settings.symbol_list, parse_fee_schedule(settings.arb_taker_fees_bps),
        venue_names={a.venue_id: a.name for a in registry.all()},
        excluded_venues=routing_engine.excluded_venues, balance=wallet_service.available,
        interval_s=settings.arb_scan_interval_ms / 1000,
        min_net_bps=settings.arb_min_net_bps, audit_cooldown_s=settings.arb_audit_cooldown_s,
        enabled=settings.arb_enabled,
    )

    # Research (Phase B): reference-market candle history, forecasts with a live track record,
    # backtests, trade plans. Read-only: it has no order entry.
    fees = live_fees
    candle_store = CandleStore(settings.resolved_market_history_path)
    research = ResearchService(
        candle_store, CandleSync(registry, candle_store, settings.symbol_list), collector, aggregator,
        settings.symbol_list, reference_fee_bps=fees.get("V1", 10.0), risk_engine=risk_engine,
    )

    # Bots and the practice lab (Phase C): money set aside in the automation fund, every bot
    # order through order_entry — whose gate keeps bots in their pockets and manual orders off
    # what the fund holds. The lab's measured costs feed the research backtester and planner.
    fund = AutomationFund(wallet_service, aggregator, settings.exchange_mode, store=store,
                          protection_bps=settings.child_price_protection_bps)
    order_entry.set_guard(fund.check_order)
    lab = PracticeLab(store, research, volatility, aggregator, settings.exchange_mode, fees)
    research.set_cost_source(lab.measured_slippage)
    automation = AutomationEngine(
        order_entry, order_manager, aggregator, routing_engine, registry, risk_engine, wallet_service, fund, lab,
        settings.symbol_list, research=research, store=store, mode=settings.exchange_mode, market_fee_bps=fees,
        protection_bps=settings.child_price_protection_bps, cycle_bots_on_real_money=settings.mainnet_cycle_bots,
    )
    # The copilot's analyst: research, techniques, forecasts, trade plans, wallet, risk, bots — read-only.
    # The language model only words what it computed; every action still goes through a confirm.
    copilot.attach(
        analyst=Analyst(research, aggregator, wallet_service, risk_engine, settings.symbol_list, venues=venue_monitor,
                        risk_monitor=risk_monitor, arbitrage=arbitrage, automation=automation,
                        mode=settings.exchange_mode, max_suggest_usdt=settings.copilot_max_suggest_usdt),
        automation=automation, mode=settings.exchange_mode, auto_max_usdt=settings.copilot_auto_max_usdt,
    )

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
        algo_engine=algo_engine,
        market_history=market_history,
        research=research,
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
        arbitrage=arbitrage,
        automation=automation,
        alerts=_build_alerts(risk_controls, risk_engine, risk_monitor, wallet_service, venue_monitor, algo_engine,
                             automation),
    )
    return _services


def _build_alerts(risk_controls, risk_engine, risk_monitor, wallet, venue_monitor, algo_engine,
                  automation) -> DiscordAlerts | TelegramAlerts | None:
    """The phone alerts: Discord when its bot, channel and owner are configured, else Telegram, else none."""
    discord_token = settings.discord_bot_token.get_secret_value().strip() if settings.discord_bot_token else ""
    use_discord = bool(discord_token and settings.discord_channel_id.strip() and settings.discord_owner_id.strip())
    token = settings.telegram_bot_token.get_secret_value().strip() if settings.telegram_bot_token else ""
    if not use_discord and (not token or not settings.telegram_chat_id.strip()):
        return None

    def running(items) -> int:
        return sum(1 for x in items if str(getattr(x.status, "value", x.status)) == "RUNNING")

    async def status() -> str:
        ks = risk_engine.kill_switch.get_state()
        state = risk_monitor.state
        pnl = state.trading_pnl if state is not None else None
        trading = [h.name for h in venue_monitor.get_all_health() if h.trading_enabled]
        where = "REAL MONEY" if settings.is_real_money else "Sandbox"
        return "\n".join([
            f"{where} - exchanges trading: {', '.join(trading) or 'none'}",
            f"Kill switch: {'ENGAGED - ' + (ks.reason or '') if ks.active else 'off'}",
            f"Trading P&L: {'unknown' if pnl is None else f'{pnl:+,.2f} USDT'}",
            f"Balances: {wallet.get_snapshot().total_value_usdt:,.2f} USDT",
            f"Running: {running(algo_engine.list())} algos, {running(automation.list())} bots",
        ])

    if use_discord:
        return DiscordAlerts(discord_token, settings.discord_channel_id, settings.discord_owner_id,
                             real_money=settings.is_real_money, engage_kill_switch=risk_controls.engage_kill_switch,
                             status=status, summary_hour_utc=settings.alerts_summary_hour_utc)
    return TelegramAlerts(token, settings.telegram_chat_id, real_money=settings.is_real_money,
                          engage_kill_switch=risk_controls.engage_kill_switch, status=status,
                          summary_hour_utc=settings.alerts_summary_hour_utc)


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Services not initialized. Call init_services() first.")
    return _services
