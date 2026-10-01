"""Audit recorder: turns internal events into the durable audit trail.

Subscribes to the event bus, so no service has to know the audit log exists.
Each event becomes one append-only record with the actor and a plain-English
summary; order snapshots, the kill-switch state and anything published with
a ``persist`` field are also saved so they survive a restart.

Services record operator actions by publishing on the ``audit`` channel:
    {"event_type", "summary", "actor"?, "order_id"?, "venue_id"?, "symbol"?,
     "payload"?, "persist"?: {"key", "value"}}
"""

from uuid import UUID

from backend.shared.database.store import AuditStore
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import OrderNotFoundError
from backend.shared.utils.logger import get_logger

logger = get_logger("audit_recorder")


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:,.8g}"


class AuditRecorder:
    def __init__(self, store: AuditStore, order_manager, kill_switch) -> None:
        self._store = store
        self._orders = order_manager
        self._kill_switch = kill_switch

    def _subscriptions(self) -> list[tuple[str, object]]:
        return [
            ("audit", self._on_audit),
            ("order_update", self._on_order_update),
            ("risk_check", self._on_risk_check),
            ("risk_alert", self._on_risk_check),
            ("routing_decision", self._on_routing_decision),
            ("execution_report", self._on_execution_report),
            ("kill_switch", self._on_kill_switch),
        ]

    def start(self) -> None:
        for channel, handler in self._subscriptions():
            event_bus.subscribe(channel, handler)

    def stop(self) -> None:
        for channel, handler in self._subscriptions():
            event_bus.unsubscribe(channel, handler)

    def _save_order(self, order_id: str) -> None:
        try:
            self._store.upsert_order(self._orders.get_order(UUID(order_id)))
        except (OrderNotFoundError, ValueError):
            pass

    # ── handlers (the synchronous part runs before any await, so records
    #    are queued in exactly the order the events happened) ─────────
    async def _on_audit(self, data: dict) -> None:
        self._store.append_event(
            data["event_type"], data["summary"],
            actor=data.get("actor"), order_id=data.get("order_id"), venue_id=data.get("venue_id"),
            symbol=data.get("symbol"), payload=data.get("payload"),
        )
        if persist := data.get("persist"):
            self._store.kv_set(persist["key"], persist["value"])

    async def _on_order_update(self, data: dict) -> None:
        order_id = data["order_id"]
        self._save_order(order_id)
        new = data["new_status"]
        summary = f"Status {data['old_status']} -> {new}"
        actor = None
        try:
            order = self._orders.get_order(UUID(order_id))
            actor = order.submitted_by
            if new in ("REJECTED", "CANCELLED") and order.rejection_reason:
                summary += f": {order.rejection_reason}"
            elif new in ("FILLED", "PARTIALLY_FILLED"):
                summary += f": {_num(order.filled_quantity)} of {_num(order.quantity)} @ {_num(order.avg_fill_price)} avg"
        except (OrderNotFoundError, ValueError):
            pass
        self._store.append_event(
            "order_status", summary, actor=actor, order_id=order_id, symbol=data.get("symbol"), payload=data,
        )

    async def _on_risk_check(self, data: dict) -> None:
        if "order_id" not in data:
            return  # portfolio alerts are recorded by the risk monitor itself
        passed, failed = data.get("checks_passed", []), data.get("checks_failed", [])
        if data.get("result") == "APPROVED":
            summary = f"Pre-trade risk APPROVED — {len(passed)} checks passed in {data.get('latency_us', 0):g} µs"
        else:
            summary = f"Pre-trade risk REJECTED at {', '.join(failed) or data.get('result')}: {data.get('message')}"
        self._store.append_event("risk_check", summary, actor="risk-engine", order_id=data["order_id"],
                                 payload=data)

    async def _on_routing_decision(self, data: dict) -> None:
        self._store.append_event(
            "routing_decision", data["summary"], actor="router", order_id=data["order_id"],
            symbol=data.get("symbol"), payload=data,
        )

    async def _on_execution_report(self, data: dict) -> None:
        self._save_order(data["order_id"])  # children were just updated
        base = data["symbol"].split("/")[0]
        kind = data["exec_type"]
        if kind in ("FILL", "PARTIAL"):
            summary = f"{data['venue_id']} {kind} {_num(data['quantity'])} {base} @ {_num(data['price'])}"
            if data.get("fee"):
                summary += f", fee {_num(data['fee'])} {data.get('fee_asset') or ''}".rstrip()
        else:
            summary = f"{data['venue_id']} {kind} {_num(data['quantity'])} {base}: {data.get('rejection_reason')}"
        self._store.append_event(
            "execution_report", summary, actor=data["venue_id"], order_id=data["order_id"],
            venue_id=data["venue_id"], symbol=data["symbol"], payload=data,
        )

    async def _on_kill_switch(self, data: dict) -> None:
        self._store.kv_set("kill_switch", self._kill_switch.get_state().model_dump(mode="json"))
        operator = data.get("operator")
        if not data.get("active"):
            summary = f"Kill switch RELEASED by {operator}"
        elif "venue_orders_swept" in data:
            summary = (f"Kill switch cleanup: {data.get('orders_cancelled', 0)} order(s) halted, "
                       f"{data['venue_orders_swept']} resting exchange order(s) cancelled")
        else:
            summary = f"Kill switch ENGAGED by {operator}: {data.get('reason')}"
        self._store.append_event("kill_switch", summary, actor=operator, payload=data)
