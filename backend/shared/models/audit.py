"""Audit trail: one append-only record per thing that happened.

Every order transition, risk decision, routing decision, child execution,
kill-switch action and operator control is recorded with who did it and a
plain-English summary, so any order can be explained after the fact.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    seq: int = 0  # strictly increasing: the order things happened in
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str
    actor: str | None = None  # username, "risk-monitor", "system", ...
    order_id: str | None = None
    venue_id: str | None = None
    symbol: str | None = None
    summary: str
    payload: dict[str, Any] = {}


class AuditEventList(BaseModel):
    events: list[AuditEvent]
    has_more: bool = False
    store: str = ""  # where the audit log lives (e.g. the SQLite file)
