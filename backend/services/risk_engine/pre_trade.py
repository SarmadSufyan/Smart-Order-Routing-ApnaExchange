"""Deterministic pre-trade risk gate. Every order passes through here —
manual orders today, and later the TWAP bot and the LLM copilot too.

Checks run in order and stop at the first failure:
    kill_switch -> size_limit -> order_notional -> rate_limit
    -> position_limit -> notional_limit -> symbol_restriction
"""

import time
from datetime import UTC, datetime

from backend.shared.events.event_bus import audit, event_bus
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
        limits: RiskLimits | None = None,
    ) -> None:
        self._kill_switch = kill_switch
        self._positions = position_tracker
        self._limits = limits or RiskLimits()
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

    async def update_limits(self, new_limits: RiskLimits, actor: str) -> RiskLimits:
        """Change the limits, audited and persisted (they survive a restart)."""
        old = self._limits.model_dump()
        self._limits = new_limits
        new = new_limits.model_dump()
        changed = {k: {"from": old.get(k), "to": v} for k, v in new.items() if old.get(k) != v}
        await audit(
            "risk_limits_changed",
            ("Risk limits changed: " + ", ".join(f"{k} {c['from']} -> {c['to']}" for k, c in changed.items()))
            if changed else "Risk limits saved (no change)",
            actor=actor,
            payload={"changed": changed, "limits": new},
            persist={"key": "risk_limits", "value": new},
        )
        logger.info("risk_limits_updated", actor=actor, changed=list(changed))
        return self._limits

    @property
    def recent_checks(self) -> list[RiskCheckDetail]:
        return list(self._recent_checks)

    def _evaluate(self, order: Order, mid_price: float) -> tuple[RiskCheckResult, list[str], list[str], str | None]:
        lim = self._limits
        passed: list[str] = []

        def fail(result: RiskCheckResult, check: str, message: str):
            return result, passed, [check], message

        if self._kill_switch.is_active:
            return fail(RiskCheckResult.REJECTED_KILL_SWITCH, "kill_switch", "Kill switch is active")
        passed.append("kill_switch")

        if order.quantity > lim.max_order_size:
            return fail(RiskCheckResult.REJECTED_SIZE_LIMIT, "size_limit",
                        f"Quantity {order.quantity:g} exceeds the max order size {lim.max_order_size:g}")
        passed.append("size_limit")

        order_notional = order.quantity * mid_price
        if mid_price > 0 and order_notional > lim.max_order_notional:
            return fail(RiskCheckResult.REJECTED_ORDER_NOTIONAL_LIMIT, "order_notional",
                        f"Order notional ${order_notional:,.2f} exceeds the per-order limit ${lim.max_order_notional:,.2f}")
        passed.append("order_notional")

        now = time.monotonic()
        self._order_timestamps = [t for t in self._order_timestamps if now - t < 1.0]
        if len(self._order_timestamps) >= lim.max_orders_per_second:
            return fail(RiskCheckResult.REJECTED_RATE_LIMIT, "rate_limit",
                        f"More than {lim.max_orders_per_second} orders in the last second")
        passed.append("rate_limit")

        pos = self._positions.get_position(order.symbol)
        signed = order.quantity if order.side.value == "BUY" else -order.quantity
        projected = abs(pos.net_position + signed)
        if projected > lim.max_position_per_symbol:
            return fail(RiskCheckResult.REJECTED_POSITION_LIMIT, "position_limit",
                        f"Projected {order.symbol} position {projected:g} exceeds {lim.max_position_per_symbol:g}")
        passed.append("position_limit")

        if mid_price > 0:
            total = self._positions.get_total_notional() + order_notional
            if total > lim.max_notional_exposure:
                return fail(RiskCheckResult.REJECTED_NOTIONAL_LIMIT, "notional_limit",
                            f"Total exposure ${total:,.2f} would exceed ${lim.max_notional_exposure:,.2f}")
            passed.append("notional_limit")

        if order.symbol in lim.restricted_symbols:
            return fail(RiskCheckResult.REJECTED_SYMBOL_RESTRICTED, "symbol_restriction",
                        f"Symbol {order.symbol} is restricted")
        passed.append("symbol_restriction")

        return RiskCheckResult.APPROVED, passed, [], None

    async def check_order(self, order: Order, mid_price: float = 0.0) -> RiskCheckDetail:
        start = time.perf_counter_ns()
        result, passed, failed, message = self._evaluate(order, mid_price)
        latency_us = (time.perf_counter_ns() - start) / 1000

        if result == RiskCheckResult.APPROVED:
            self._order_timestamps.append(time.monotonic())

        detail = RiskCheckDetail(
            order_id=str(order.id),
            result=result,
            checks_passed=passed,
            checks_failed=failed,
            timestamp=datetime.now(UTC),
            latency_us=round(latency_us, 1),
            message=message,
        )
        self._recent_checks.append(detail)
        if len(self._recent_checks) > MAX_RECENT_CHECKS:
            self._recent_checks = self._recent_checks[-MAX_RECENT_CHECKS:]

        await event_bus.publish(
            "risk_alert" if result != RiskCheckResult.APPROVED else "risk_check",
            {
                "order_id": str(order.id),
                "result": result.value,
                "checks_passed": passed,
                "checks_failed": failed,
                "message": message,
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
