from datetime import UTC, datetime

from backend.shared.events.event_bus import event_bus
from backend.shared.models.risk import KillSwitchState
from backend.shared.utils.logger import get_logger

logger = get_logger("kill_switch")


class KillSwitch:
    def __init__(self) -> None:
        self._state = KillSwitchState()

    @property
    def is_active(self) -> bool:
        return self._state.active

    def get_state(self) -> KillSwitchState:
        return self._state.model_copy()

    async def activate(self, reason: str, activated_by: str, orders_cancelled: int = 0) -> KillSwitchState:
        self._state.active = True
        self._state.last_activated = datetime.now(UTC)
        self._state.activated_by = activated_by
        self._state.reason = reason
        self._state.orders_cancelled = orders_cancelled

        await event_bus.publish(
            "kill_switch",
            {
                "active": True,
                "reason": reason,
                "operator": activated_by,
                "timestamp": self._state.last_activated.isoformat(),
                "orders_cancelled": orders_cancelled,
            },
        )
        logger.warning(
            "kill_switch_activated",
            reason=reason,
            activated_by=activated_by,
            orders_cancelled=orders_cancelled,
        )
        return self.get_state()

    async def deactivate(self, deactivated_by: str) -> KillSwitchState:
        self._state.active = False
        self._state.last_deactivated = datetime.now(UTC)

        await event_bus.publish(
            "kill_switch",
            {
                "active": False,
                "reason": "deactivated",
                "operator": deactivated_by,
                "timestamp": self._state.last_deactivated.isoformat(),
            },
        )
        logger.info("kill_switch_deactivated", deactivated_by=deactivated_by)
        return self.get_state()

    def add_cancelled(self, count: int) -> None:
        self._state.orders_cancelled += count


async def engage_kill_switch(
    kill_switch: KillSwitch, order_manager, registry, reason: str, operator: str
) -> KillSwitchState:
    """Halt all trading.

    Order matters: the gate closes FIRST so no new order can slip through
    while the cleanup (which does network I/O) is still running.

      1. Activate -> every new order is rejected by pre-trade risk.
      2. Cancel parent orders still WORKING / PARTIALLY_FILLED in our OMS.
      3. Sweep every venue for resting orders (e.g. placed manually on the
         exchange's own UI) — defense in depth, since our children are IOC.
    """
    await kill_switch.activate(reason=reason, activated_by=operator)

    cancelled = 0
    for order in order_manager.active_orders():
        try:
            await order_manager.cancel_order(order.id)
            cancelled += 1
        except Exception:  # noqa: BLE001 — a race with a final fill is fine
            pass
    swept = await registry.cancel_all_open_orders()
    kill_switch.add_cancelled(cancelled + swept)

    state = kill_switch.get_state()
    await event_bus.publish(
        "kill_switch",
        {
            "active": True,
            "reason": reason,
            "operator": operator,
            "timestamp": state.last_activated.isoformat() if state.last_activated else None,
            "orders_cancelled": state.orders_cancelled,
            "venue_orders_swept": swept,
        },
    )
    logger.warning("kill_switch_cleanup_done", oms_cancelled=cancelled, venue_orders_swept=swept)
    return state
