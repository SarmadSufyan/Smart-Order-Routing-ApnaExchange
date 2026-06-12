from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.execution_report import ExecutionReport, ExecutionReportResponse

router = APIRouter(prefix="/api/execution-reports", tags=["execution-reports"])


_reports: list[ExecutionReport] = []
MAX_REPORTS = 10_000


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
