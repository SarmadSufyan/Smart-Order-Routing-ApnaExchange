from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.execution_report import ExecType, ExecutionReport, ExecutionReportResponse
from backend.shared.models.order import OrderSide

router = APIRouter(prefix="/api/execution-reports", tags=["execution-reports"])


_reports: list[ExecutionReport] = []
MAX_REPORTS = 10_000


def report_from_event(data: dict) -> ExecutionReport:
    """An ``execution_report`` event (live, or replayed from the audit log)."""
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
        rejection_reason=data.get("rejection_reason"),
        exchange_order_id=data.get("exchange_order_id"),
        fee=data.get("fee") or 0.0,
        fee_asset=data.get("fee_asset"),
    )
    if data.get("timestamp"):
        report.timestamp = datetime.fromisoformat(data["timestamp"])
    return report


def record_execution_report(report: ExecutionReport) -> None:
    _reports.append(report)
    if len(_reports) > MAX_REPORTS:
        _reports.pop(0)


@router.get("/", response_model=ExecutionReportResponse)
async def list_execution_reports(
    user: Annotated[User, Depends(get_current_user)],
    order_id: str | None = Query(None),
    venue_id: str | None = Query(None),
    exec_type: str | None = Query(None),
    symbol: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    filtered = list(_reports)

    if order_id:
        filtered = [r for r in filtered if str(r.order_id) == order_id]
    if venue_id:
        filtered = [r for r in filtered if r.venue_id == venue_id]
    if exec_type:
        filtered = [r for r in filtered if r.exec_type.value == exec_type.upper()]
    if symbol:
        filtered = [r for r in filtered if r.symbol == symbol.upper()]

    filtered.sort(key=lambda r: r.timestamp, reverse=True)
    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]

    return ExecutionReportResponse(
        reports=page_items,
        total=total,
        page=page,
        page_size=page_size,
    )
