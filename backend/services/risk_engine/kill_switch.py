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
