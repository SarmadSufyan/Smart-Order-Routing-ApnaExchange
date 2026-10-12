"""Deterministic pre-trade risk gate. Every order passes through here —
the order button, every TWAP slice and every confirmed copilot proposal.

Checks run in order and stop at the first failure (the checks themselves
live in ``checks.py``, a pure function shared with the risk batch):
    kill_switch -> size_limit -> order_notional -> rate_limit
    -> position_limit -> notional_limit -> symbol_restriction
"""

import time
from datetime import UTC, datetime

from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import RiskLimitAboveCeilingError
from backend.shared.models.order import Order
from backend.shared.models.risk import RiskCeilings, RiskCheckDetail, RiskCheckResult, RiskLimits
from backend.shared.utils.logger import get_logger

from .batch import BatchReport, Mode, RiskBatchRunner, StressParams, plan_jobs
from .ceilings import apply_ceilings, ceiling_breaches
from .checks import OrderSpec, RiskContext, evaluate_order
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
        batch_runner: RiskBatchRunner | None = None,
        ceilings: RiskCeilings | None = None,
    ) -> None:
        self._kill_switch = kill_switch
        self._positions = position_tracker
        # Real money: every limit held here is clamped to the .env ceilings (ceilings.py).
        self._ceilings = ceilings
        self._limits = apply_ceilings(limits or RiskLimits(), ceilings)
        self._batch = batch_runner or RiskBatchRunner()
        self._recent_checks: list[RiskCheckDetail] = []
        self._order_timestamps: list[float] = []

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def position_tracker(self) -> PositionTracker:
        return self._positions

    @property
    def batch_runner(self) -> RiskBatchRunner:
        return self._batch

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    @limits.setter
    def limits(self, new_limits: RiskLimits) -> None:
        self._limits = apply_ceilings(new_limits, self._ceilings)

    @property
    def ceilings(self) -> RiskCeilings | None:
        """The real-money ceilings (None on testnet)."""
        return self._ceilings

    def effective(self, limits: RiskLimits) -> RiskLimits:
        """These limits as they would be in force here (clamped to the ceilings)."""
        return apply_ceilings(limits, self._ceilings)

    def check_ceilings(self, limits: RiskLimits) -> None:
        """Refuse limits above a real-money ceiling (raises RiskLimitAboveCeilingError)."""
        if breaches := ceiling_breaches(limits, self._ceilings):
            raise RiskLimitAboveCeilingError(breaches)

    async def update_limits(self, new_limits: RiskLimits, actor: str) -> RiskLimits:
        """Change the limits, audited and persisted (they survive a restart). Never past a real-money ceiling."""
        self.check_ceilings(new_limits)
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
        # The checks themselves are a pure function (checks.py), shared with
        # the risk batch; this method only snapshots the live state for it.
        now = time.monotonic()
        self._order_timestamps = [t for t in self._order_timestamps if now - t < 1.0]
        ctx = RiskContext(
            limits=self._limits,
            kill_switch_active=self._kill_switch.is_active,
            net_position=self._positions.get_position(order.symbol).net_position,
            total_notional=self._positions.get_total_notional(),
            orders_last_second=len(self._order_timestamps),
        )
        out = evaluate_order(ctx, OrderSpec(order.symbol, order.side.value, order.quantity), mid_price)
        return out.result, out.passed, out.failed, out.message

    async def preflight(
        self,
        orders: list[OrderSpec],
        mids: dict[str, float],
        *,
        stress: dict[str, StressParams] | None = None,
        mode: Mode = "auto",
    ) -> BatchReport:
        """Check a list of orders without sending anything (``batch.py``).

        Each order meets the position it would face if every earlier order in
        the list had filled; the checks are the same pure function as the gate.
        Nothing is recorded as an approval: the rate limit isn't consumed.
        """
        positions = self._positions.get_all_positions()
        jobs = plan_jobs(
            orders, mids, limits=self._limits, kill_switch_active=self._kill_switch.is_active,
            positions={s: p.net_position for s, p in positions.items()},
            notionals={s: abs(p.notional) for s, p in positions.items()},
            stress=stress,
        )
        return await self._batch.run(jobs, mode)

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
