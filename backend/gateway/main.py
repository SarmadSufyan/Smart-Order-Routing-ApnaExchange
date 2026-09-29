import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.gateway.config import settings
from backend.gateway.dependencies import get_services, init_services
from backend.gateway.routers import (
    admin,
    auth,
    execution_reports,
    market_data,
    orders,
    risk,
    routing,
    venues,
    wallet,
)
from backend.gateway.routers.execution_reports import record_execution_report
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.execution_report import ExecType, ExecutionReport
from backend.shared.models.order import OrderSide
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
]


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

    # Fan every internal event out to WebSocket subscribers of that channel.
    for channel in WS_CHANNELS:
        async def forward(data: dict, _ch: str = channel) -> None:
            await svc.ws_manager.broadcast(_ch, data)

        event_bus.subscribe(channel, forward)

    async def store_execution_report(data: dict) -> None:
        record_execution_report(
            ExecutionReport(
                order_id=UUID(data["order_id"]),
                child_order_id=UUID(data["child_order_id"]) if data.get("child_order_id") else None,
                venue_id=data["venue_id"],
                exec_type=ExecType(data["exec_type"]),
                symbol=data["symbol"],
                side=OrderSide(data["side"]),
                quantity=data.get("quantity", 0),
                price=data.get("price", 0),
                venue_latency_ms=data.get("venue_latency_ms", 0),
                rejection_reason=data.get("rejection_reason"),
                exchange_order_id=data.get("exchange_order_id"),
                fee=data.get("fee") or 0.0,
                fee_asset=data.get("fee_asset"),
            )
        )

    async def refresh_wallet_after_fill(data: dict) -> None:
        if data.get("exec_type") in (ExecType.FILL.value, ExecType.PARTIAL.value):
            svc.wallet_service.request_refresh()

    event_bus.subscribe("execution_report", store_execution_report)
    event_bus.subscribe("execution_report", refresh_wallet_after_fill)

    # Exchanges first (they feed everything else), then the services reading them.
    await svc.exchange_registry.start_all()
    await svc.routing_engine.start()
    for service in (svc.venue_monitor, svc.market_data_collector, svc.coherence_monitor, svc.wallet_service):
        _background_tasks.append(asyncio.create_task(service.start()))

    logger.info("apnasor_ready", port=settings.api_port)
    yield

    logger.info("shutting_down")
    for service in (svc.wallet_service, svc.coherence_monitor, svc.market_data_collector, svc.venue_monitor):
        await service.stop()
    await svc.routing_engine.stop()
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()
    await svc.exchange_registry.stop_all()
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
    }
    return JSONResponse(
        status_code=status_map.get(exc.code, 500),
        content={"error": exc.code, "message": exc.message},
    )


for router_module in (auth, orders, venues, market_data, risk, routing, admin, execution_reports, wallet):
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
        },
        "exchanges": exchanges,
        "websocket_connections": svc.ws_manager.connection_count,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    svc = get_services()
    user_id = ""

    if token:
        try:
            from jose import jwt

            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            user_id = payload.get("sub", "")
        except Exception:
            await websocket.close(code=4001, reason="Invalid token")
            return

    conn_id = await svc.ws_manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            await svc.ws_manager.handle_client_message(conn_id, raw)
    except WebSocketDisconnect:
        await svc.ws_manager.disconnect(conn_id)
