import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.gateway.config import settings
from backend.gateway.dependencies import get_services, init_services
from backend.gateway.routers import admin, auth, execution_reports, market_data, orders, risk, routing, venues
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import DomainError
from backend.shared.utils.logger import get_logger, setup_logging

logger = get_logger("gateway")

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(debug=settings.debug)
    logger.info("starting_apna_exchange", app_name=settings.app_name)

    svc = init_services()

    async def _wire_ws_broadcasts(channel: str):
        async def handler(data: dict):
            await svc.ws_manager.broadcast(channel, data)
        event_bus.subscribe(channel, handler)

    for ch in [
        "venue_health",
        "market_data",
        "nbbo_update",
        "order_update",
        "risk_alert",
        "kill_switch",
        "execution_report",
    ]:
        await _wire_ws_broadcasts(ch)

    async def _store_execution_report(data: dict):
        from uuid import UUID
        from backend.shared.models.execution_report import ExecutionReport, ExecType
        from backend.shared.models.order import OrderSide
        from backend.gateway.routers.execution_reports import record_execution_report

        report = ExecutionReport(
            order_id=UUID(data["order_id"]),
            child_order_id=UUID(data["child_order_id"]) if data.get("child_order_id") else None,
            venue_id=data["venue_id"],
            exec_type=ExecType(data["exec_type"]),
            symbol=data["symbol"],
            side=OrderSide(data["side"]),
            quantity=data.get("quantity", 0),
            price=data.get("price", 0),
            venue_latency_ms=data.get("venue_latency_ms", 0),
        )
        record_execution_report(report)

    event_bus.subscribe("execution_report", _store_execution_report)

    await svc.routing_engine.start()

    _background_tasks.append(asyncio.create_task(svc.venue_monitor.start()))
    _background_tasks.append(asyncio.create_task(svc.market_data_collector.start()))

    logger.info("apna_exchange_ready", port=settings.api_port)
    yield

    logger.info("shutting_down")
    await svc.venue_monitor.stop()
    await svc.market_data_collector.stop()
    await svc.routing_engine.stop()
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    logger.info("shutdown_complete")


app = FastAPI(
    title="Apna Exchange — DEIRCP API",
    description="Distributed Execution Intelligence & Risk Control Platform",
    version="1.0.0",
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
    from fastapi.responses import JSONResponse

    status_map = {
        "ORDER_NOT_FOUND": 404,
        "VENUE_NOT_FOUND": 404,
        "VENUE_UNAVAILABLE": 503,
        "INVALID_STATE_TRANSITION": 409,
        "RISK_CHECK_FAILED": 400,
        "KILL_SWITCH_ACTIVE": 503,
    }
    return JSONResponse(
        status_code=status_map.get(exc.code, 500),
        content={"error": exc.code, "message": exc.message},
    )


app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(venues.router)
app.include_router(market_data.router)
app.include_router(risk.router)
app.include_router(routing.router)
app.include_router(admin.router)
app.include_router(execution_reports.router)


@app.get("/api/health")
async def health_check():
    svc = get_services()
    venue_status = {}
    for h in svc.venue_monitor.get_all_health():
        reachable = h.status.value not in ("DISCONNECTED",)
        venue_status[h.venue_id] = "reachable" if reachable else "unreachable"

    return {
        "status": "healthy",
        "services": {
            "gateway": "up",
            "market_data": "up",
            "routing_engine": "up",
            "risk_engine": "up",
            "order_state": "up",
            "venue_monitor": "up",
        },
        "venues": venue_status,
        "websocket_connections": svc.ws_manager.connection_count,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    svc = get_services()
    user_id = ""

    if token:
        try:
            from jose import jwt
            payload = jwt.decode(
                token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
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
