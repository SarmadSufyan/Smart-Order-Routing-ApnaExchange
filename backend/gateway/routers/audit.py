from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.audit import AuditEventList

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _store():
    from backend.gateway.dependencies import get_services

    return get_services().audit_store


@router.get("/", response_model=AuditEventList)
async def list_audit_events(
    user: Annotated[User, Depends(get_current_user)],
    order_id: str | None = Query(None, description="Full timeline of one order (oldest first)"),
    event_type: str | None = Query(None, description="Comma-separated, e.g. kill_switch,risk_limits_changed"),
    venue_id: str | None = Query(None),
    before_seq: int | None = Query(None, description="Page backwards from this sequence number"),
    limit: int = Query(100, ge=1, le=500),
):
    """The append-only audit trail. Newest first, except an order's timeline."""
    store = _store()
    await store.flush()  # include what just happened
    events = await store.query_events(
        order_id=order_id, event_type=event_type, venue_id=venue_id, before_seq=before_seq,
        limit=limit + 1, ascending=bool(order_id),
    )
    return AuditEventList(events=events[:limit], has_more=len(events) > limit, store=Path(store.path).name)
