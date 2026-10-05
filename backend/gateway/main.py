import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.gateway.config import settings
from backend.gateway.dependencies import Services, default_risk_limits, get_services, init_services
from backend.gateway.routers import (
    admin,
    algos,
    arbitrage,
    audit,
    auth,
    consensus,
    copilot,
    execution_reports,
    market_data,
    orders,
    risk,
    routing,
    venues,
    wallet,
)
from backend.gateway.routers.execution_reports import record_execution_report, report_from_event
from backend.shared.events.event_bus import audit as record_audit
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.execution_report import ExecType
from backend.shared.models.risk import KillSwitchState, RiskLimits
from backend.shared.utils.logger import get_logger, setup_logging

logger = get_logger("gateway")

_background_tasks: list[asyncio.Task] = []

WS_CHANNELS = [
    "venue_health",
    "market_data",
    "nbbo_update",
    "order_update",
    "risk_alert",
    "kill_switch",
    "execution_report",
    "wallet_update",
    "algo_update",
    "arb_update",
]


async def restore_state(svc: Services) -> None:
    """Reload what the previous run left in the audit store.

    Order matters: the kill switch comes back FIRST, so a gateway that was
    halted stays halted before anything else can happen.
    """
    store = svc.audit_store
    if saved := await store.kv_get("kill_switch"):
        svc.risk_engine.kill_switch.restore(KillSwitchState.model_validate(saved))
    if saved := await store.kv_get("risk_limits"):
        # Defaults first, so limits added in a newer version get their default.
        svc.risk_engine.limits = RiskLimits.model_validate({**default_risk_limits().model_dump(), **saved})
    if saved := await store.kv_get("venue_blacklist"):
        svc.venue_monitor.restore_blacklist(saved.get("venues", []))

    orders = await store.load_orders(limit=500)
    for order in svc.order_manager.restore(orders):
        store.upsert_order(order)
        store.append_event(
            "order_status", f"Recovered after restart -> {order.status.value}: {order.rejection_reason}",
            actor="system", order_id=str(order.id), symbol=order.symbol,
        )
    svc.routing_engine.restore_stats(orders)
    for payload in await store.recent_payloads("execution_report", limit=1000):
        try:
            record_execution_report(report_from_event(payload))
        except (KeyError, ValueError):
            continue
    await svc.pnl_service.load()
    await svc.arbitrage.load()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(debug=settings.debug)
    logger.info(
        "starting_apnasor",
        exchange_mode=settings.exchange_mode,
        exchanges=settings.enabled_exchange_list,
        symbols=settings.symbol_list,
    )
    svc = init_services()
    await svc.audit_store.open()
    await restore_state(svc)
    svc.audit_recorder.start()
    svc.pnl_service.start()
    await svc.twap_engine.load()  # after the recorder: interrupted TWAPs are paused, and that is audited
    svc.twap_engine.start()

    # Fan every internal event out to WebSocket subscribers of that channel.
    for channel in WS_CHANNELS:
        async def forward(data: dict, _ch: str = channel) -> None:
            await svc.ws_manager.broadcast(_ch, data)

        event_bus.subscribe(channel, forward)

    async def store_execution_report(data: dict) -> None:
        record_execution_report(report_from_event(data))

    async def refresh_wallet_after_fill(data: dict) -> None:
        if data.get("exec_type") in (ExecType.FILL.value, ExecType.PARTIAL.value):
            svc.wallet_service.request_refresh()

    event_bus.subscribe("execution_report", store_execution_report)
    event_bus.subscribe("execution_report", refresh_wallet_after_fill)

    ks = svc.risk_engine.kill_switch.get_state()
    await record_audit(
        "gateway_started",
        f"Gateway started ({settings.exchange_mode}; venues {', '.join(settings.enabled_exchange_list)})"
        + (f" — kill switch still ENGAGED: {ks.reason}" if ks.active else ""),
        actor="system",
        payload={"exchange_mode": settings.exchange_mode, "exchanges": settings.enabled_exchange_list,
                 "symbols": settings.symbol_list, "kill_switch_active": ks.active},
    )

    # Exchanges first (they feed everything else), then the services reading them.
    await svc.exchange_registry.start_all()
    await svc.routing_engine.start()
    svc.risk_engine.batch_runner.start()  # spawn the risk worker processes in the background
    # RAFT on: agree with the cluster before anything can trade (no-op when off).
    await svc.risk_controls.startup()
    background = (svc.venue_monitor, svc.market_data_collector, svc.coherence_monitor,
                  svc.wallet_service, svc.risk_monitor, svc.volatility, svc.risk_controls, svc.arbitrage)
    for service in background:
        _background_tasks.append(asyncio.create_task(service.start()))

    logger.info("apnasor_ready", port=settings.api_port, audit_store=svc.audit_store.path)
    yield

    logger.info("shutting_down")
    # Algos first: running TWAPs are paused and an in-flight slice finishes
    # while the exchanges and the router are still up.
    await svc.twap_engine.stop()
    await record_audit("gateway_stopped", "Gateway stopped", actor="system")
    for service in reversed(background):
        await service.stop()
    await svc.routing_engine.stop()
    await svc.risk_engine.batch_runner.stop()
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()
    await svc.exchange_registry.stop_all()
    svc.pnl_service.stop()
    svc.audit_recorder.stop()
    await svc.audit_store.close()
    logger.info("shutdown_complete")


app = FastAPI(
    title="ApnaSOR — Smart Order Router API",
    description="Multi-exchange crypto Smart Order Router with pre-trade risk controls",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(request, exc: DomainError):
    status_map = {
        "ORDER_NOT_FOUND": 404,
        "VENUE_NOT_FOUND": 404,
        "VENUE_UNAVAILABLE": 503,
        "INVALID_STATE_TRANSITION": 409,
        "RISK_CHECK_FAILED": 400,
        "KILL_SWITCH_ACTIVE": 503,
        "UNSUPPORTED_SYMBOL": 400,
        "ALGO_NOT_FOUND": 404,
        "ALGO_REJECTED": 409,
        "COPILOT_UNAVAILABLE": 503,
        "LLM_UNAVAILABLE": 503,
        "PROPOSAL_NOT_FOUND": 404,
        "PROPOSAL_NOT_ACTIONABLE": 409,
        "CONSENSUS_UNAVAILABLE": 503,
    }
    return JSONResponse(
        status_code=status_map.get(exc.code, 500),
        content={"error": exc.code, "message": exc.message},
    )


for router_module in (auth, orders, venues, market_data, risk, routing, admin, execution_reports, wallet, audit,
                      algos, copilot, consensus, arbitrage):
    app.include_router(router_module.router)


@app.get("/api/health")
async def health_check():
    svc = get_services()
    exchanges = {}
    for adapter in svc.exchange_registry.all():
        feed = adapter.feed_status()
        exchanges[adapter.venue_id] = {
            "name": adapter.name,
            "mode": adapter.mode,
            "feed_connected": feed.connected,
            "books_synced": f"{feed.books_synced}/{feed.books_total}",
            "trading_enabled": adapter.has_credentials,
        }
    return {
        "status": "healthy",
        "exchange_mode": settings.exchange_mode,
        "services": {
            "gateway": "up",
            "market_data": "up",
            "routing_engine": "up",
            "risk_engine": "up",
            "order_state": "up",
            "venue_monitor": "up",
            "wallet": "up",
            "audit_store": "up" if svc.audit_store.is_open else "down",
            "consensus": _consensus_health(svc),
        },
        "audit_store": svc.audit_store.path,
        "exchanges": exchanges,
        "websocket_connections": svc.ws_manager.connection_count,
    }


def _consensus_health(svc: Services) -> str:
    status = svc.risk_controls.status()
    if not status.enabled:
        return "off"
    if status.leader is not None and status.majority_reachable:
        return f"up (leader node {status.leader})"
    return "no majority" if not status.majority_reachable else "electing"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Live events for logged-in users only.

    The login token comes in the FIRST message ({"type": "auth", "token": ...}),
    never in the URL: URLs are printed in every access log. See ws_manager.py.
    """
    await get_services().ws_manager.serve(websocket)
