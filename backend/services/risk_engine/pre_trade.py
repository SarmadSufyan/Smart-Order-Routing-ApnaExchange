import time
from collections import defaultdict
from datetime import UTC, datetime

from backend.shared.events.event_bus import event_bus
from backend.shared.models.order import Order
from backend.shared.models.risk import RiskCheckDetail, RiskCheckResult, RiskLimits
from backend.shared.utils.logger import get_logger

from .kill_switch import KillSwitch
from .position_tracker import PositionTracker

logger = get_logger("pre_trade_risk")

MAX_RECENT_CHECKS = 100


class PreTradeRiskEngine:
    def __init__(
        self,
        kill_switch: KillSwitch,
        position_tracker: PositionTracker,
    ) -> None:
        self._kill_switch = kill_switch
        self._positions = position_tracker
        self._limits = RiskLimits()
        self._recent_checks: list[RiskCheckDetail] = []
        self._order_timestamps: list[float] = []

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def position_tracker(self) -> PositionTracker:
        return self._positions

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    @limits.setter
    def limits(self, new_limits: RiskLimits) -> None:
        self._limits = new_limits

    @property
    def recent_checks(self) -> list[RiskCheckDetail]:
        return list(self._recent_checks)

    async def check_order(self, order: Order, mid_price: float = 0.0) -> RiskCheckDetail:
        start = time.perf_counter_ns()
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        result = RiskCheckResult.APPROVED

        if self._kill_switch.is_active:
            result = RiskCheckResult.REJECTED_KILL_SWITCH
            checks_failed.append("kill_switch")
        else:
            checks_passed.append("kill_switch")

        if result == RiskCheckResult.APPROVED:
            if order.quantity > self._limits.max_order_size:
                result = RiskCheckResult.REJECTED_SIZE_LIMIT
                checks_failed.append("size_limit")
            else:
                checks_passed.append("size_limit")

        if result == RiskCheckResult.APPROVED:
            now = time.monotonic()
            self._order_timestamps = [
                t for t in self._order_timestamps if now - t < 1.0
            ]
            if len(self._order_timestamps) >= self._limits.max_orders_per_second:
                result = RiskCheckResult.REJECTED_RATE_LIMIT
                checks_failed.append("rate_limit")
            else:
                checks_passed.append("rate_limit")

        if result == RiskCheckResult.APPROVED:
            pos = self._positions.get_position(order.symbol)
            projected = abs(pos.net_position + order.quantity)
            if projected > self._limits.max_position_per_symbol:
                result = RiskCheckResult.REJECTED_POSITION_LIMIT
                checks_failed.append("position_limit")
            else:
                checks_passed.append("position_limit")

        if result == RiskCheckResult.APPROVED and mid_price > 0:
            order_notional = order.quantity * mid_price
            total_notional = self._positions.get_total_notional() + order_notional
            if total_notional > self._limits.max_notional_exposure:
                result = RiskCheckResult.REJECTED_NOTIONAL_LIMIT
                checks_failed.append("notional_limit")
            else:
                checks_passed.append("notional_limit")

        if result == RiskCheckResult.APPROVED:
            if order.symbol in self._limits.restricted_symbols:
                result = RiskCheckResult.REJECTED_SYMBOL_RESTRICTED
                checks_failed.append("symbol_restriction")
            else:
                checks_passed.append("symbol_restriction")

        latency_us = (time.perf_counter_ns() - start) / 1000

        if result == RiskCheckResult.APPROVED:
            self._order_timestamps.append(time.monotonic())

        detail = RiskCheckDetail(
            order_id=str(order.id),
            result=result,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            timestamp=datetime.now(UTC),
            latency_us=round(latency_us, 1),
        )

        self._recent_checks.append(detail)
        if len(self._recent_checks) > MAX_RECENT_CHECKS:
            self._recent_checks = self._recent_checks[-MAX_RECENT_CHECKS:]

        await event_bus.publish(
            "risk_alert" if result != RiskCheckResult.APPROVED else "risk_check",
            {
                "order_id": str(order.id),
                "result": result.value,
                "checks_passed": checks_passed,
                "checks_failed": checks_failed,
                "latency_us": detail.latency_us,
            },
        )

        logger.info(
            "risk_check_completed",
            order_id=str(order.id),
            result=result.value,
            latency_us=detail.latency_us,
        )
        return detail
