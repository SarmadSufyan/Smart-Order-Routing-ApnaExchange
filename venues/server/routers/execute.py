"""
server/routers/execute.py

POST /execute-order → fill / partial / reject using per-symbol engines.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import structlog

router = APIRouter()
logger = structlog.get_logger()


class ExecuteOrderRequest(BaseModel):
    child_order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    order_type: str = "MARKET"


class ExecuteOrderResponse(BaseModel):
    child_order_id: str
    venue_id: str
    exec_type: str
    filled_qty: int = 0
    fill_price: float = 0.0
    venue_exec_id: Optional[str] = None
    venue_latency_ms: float = 0.0
    reject_reason: Optional[str] = None
    timestamp: str = ""


@router.post("/execute-order", response_model=ExecuteOrderResponse)
async def execute_order(req: ExecuteOrderRequest) -> ExecuteOrderResponse:
    from server.venue_app import get_engines, get_symbol_engines

    engines = get_engines()

    if req.side.upper() not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")

    try:
        se = get_symbol_engines(req.symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    latency_ms = await engines.latency.inject()

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    try:
        result = se.fill_sim.execute(
            side=req.side.upper(),
            quantity=req.quantity,
            price=req.price,
        )
    except Exception as exc:
        logger.error("execute_order.engine_error", venue_id=engines.profile.venue_id,
                      child_order_id=req.child_order_id, error=str(exc))
        return ExecuteOrderResponse(
            child_order_id=req.child_order_id,
            venue_id=engines.profile.venue_id,
            exec_type="REJECT",
            reject_reason=str(exc),
            venue_latency_ms=latency_ms,
            timestamp=ts,
        )

    exec_id = se.fill_sim.next_exec_id()

    logger.info("execute_order.done",
                venue_id=engines.profile.venue_id,
                symbol=req.symbol,
                child_order_id=req.child_order_id,
                exec_type=result.exec_type.value,
                filled_qty=result.filled_qty,
                fill_price=result.fill_price,
                latency_ms=latency_ms)

    return ExecuteOrderResponse(
        child_order_id=req.child_order_id,
        venue_id=engines.profile.venue_id,
        exec_type=result.exec_type.value,
        filled_qty=result.filled_qty,
        fill_price=result.fill_price,
        venue_exec_id=exec_id if result.exec_type.value != "REJECT" else None,
        venue_latency_ms=latency_ms,
        reject_reason=result.reject_reason,
        timestamp=ts,
    )
