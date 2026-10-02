"""Continuous portfolio risk controls (post-trade, every few seconds).

Pre-trade risk judges one order. This watches the whole book:

* **Trading-loss limit** — when trading P&L (what our trades added vs. just
  holding the opening balances) falls below ``-max_trading_loss_usdt``, the
  kill switch is engaged automatically, and it can't be released while the
  loss is still beyond the limit (raise the limit or reset the baseline).
* **Concentration** — one non-stable asset above ``max_asset_concentration_pct``
  of the portfolio, or one exchange holding more than
  ``max_venue_concentration_pct`` of it (counterparty risk: exchanges fail).
  Alerts fire when a limit is first crossed, not on every tick.
"""

import asyncio
from datetime import UTC, datetime

from backend.shared.events.event_bus import audit, event_bus
from backend.shared.models.portfolio import ConcentrationItem, PortfolioRiskState
from backend.shared.utils.logger import get_logger

from .kill_switch import engage_kill_switch

logger = get_logger("risk_monitor")

MONITOR_ACTOR = "risk-monitor"
STABLE = {"USDT"}


def fmt_usd(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


class RiskMonitor:
    def __init__(self, risk_engine, pnl, wallet, order_manager, registry, interval_s: float = 2.0,
                 controls=None) -> None:
        self._risk = risk_engine
        self._controls = controls  # RiskControls: an automatic halt is replicated like a manual one
        self._pnl = pnl
        self._wallet = wallet
        self._orders = order_manager
        self._registry = registry
        self._interval_s = interval_s
        self._state = PortfolioRiskState()
        self._breached_keys: set[str] = set()
        self._running = False

    @property
    def state(self) -> PortfolioRiskState:
        return self._state

    def deactivation_blocker(self) -> str | None:
        """Why the kill switch must stay engaged, if the loss limit still holds it."""
        s = self._state
        if s.loss_limit_breached and s.trading_pnl is not None:
            return (f"Trading loss {fmt_usd(s.trading_pnl)} is still beyond the "
                    f"{fmt_usd(s.loss_limit_usdt)} limit. Raise the limit or reset the "
                    f"P&L baseline before releasing the kill switch.")
        return None

    async def evaluate(self) -> PortfolioRiskState:
        limits = self._risk.limits
        trading = self._pnl.trading_pnl()
        loss_limit = limits.max_trading_loss_usdt
        breached = bool(loss_limit > 0 and trading is not None and trading < -loss_limit)

        concentration = self._concentration(limits)
        alerts = [
            f"{c.name} is {c.share_pct:.1f}% of the portfolio (limit {c.limit_pct:g}%)"
            for c in concentration if c.breached
        ]
        if breached:
            alerts.insert(0, f"Trading loss {fmt_usd(trading)} beyond the {fmt_usd(loss_limit)} limit")

        self._state = PortfolioRiskState(
            as_of=datetime.now(UTC),
            trading_pnl=trading,
            loss_limit_usdt=loss_limit,
            loss_used_pct=(max(0.0, -trading) / loss_limit * 100) if (loss_limit > 0 and trading is not None) else None,
            loss_limit_breached=breached,
            concentration=concentration,
            alerts=alerts,
        )

        if breached and not self._risk.kill_switch.is_active:
            reason = (f"Automatic: trading loss {fmt_usd(trading)} breached the "
                      f"{fmt_usd(loss_limit)} limit")
            logger.warning("loss_limit_breached", trading_pnl=trading, limit=loss_limit)
            if self._controls is not None:
                await self._controls.engage_kill_switch(reason, MONITOR_ACTOR)
            else:
                await engage_kill_switch(
                    self._risk.kill_switch, self._orders, self._registry, reason=reason, operator=MONITOR_ACTOR,
                )
            await event_bus.publish("risk_alert", {"type": "LOSS_LIMIT", "message": reason})

        await self._edge_alerts(concentration)
        return self._state

    def _concentration(self, limits) -> list[ConcentrationItem]:
        snap = self._wallet.get_snapshot()
        total = snap.total_value_usdt
        if not snap.as_of or total <= 0:
            return []
        items: list[ConcentrationItem] = []
        for a in snap.assets:
            if a.asset in STABLE or not a.value_usdt:
                continue
            share = a.value_usdt / total * 100
            items.append(ConcentrationItem(
                kind="asset", key=a.asset, name=a.asset, value_usdt=a.value_usdt, share_pct=share,
                limit_pct=limits.max_asset_concentration_pct,
                breached=share > limits.max_asset_concentration_pct,
            ))
        for v in snap.venues:
            if v.error or v.total_value_usdt <= 0:
                continue
            share = v.total_value_usdt / total * 100
            items.append(ConcentrationItem(
                kind="venue", key=v.venue_id, name=v.name, value_usdt=v.total_value_usdt, share_pct=share,
                limit_pct=limits.max_venue_concentration_pct,
                breached=share > limits.max_venue_concentration_pct,
            ))
        return sorted(items, key=lambda c: (c.kind, -c.share_pct))

    async def _edge_alerts(self, items: list[ConcentrationItem]) -> None:
        now = {f"{c.kind}:{c.key}" for c in items if c.breached}
        for c in items:
            key = f"{c.kind}:{c.key}"
            if c.breached and key not in self._breached_keys:
                what = "exchange" if c.kind == "venue" else "asset"
                message = (f"Concentration: {what} {c.name} is {c.share_pct:.1f}% of the portfolio "
                           f"(limit {c.limit_pct:g}%)")
                await event_bus.publish("risk_alert", {"type": "CONCENTRATION", "message": message})
                await audit(
                    "concentration_alert", message, actor=MONITOR_ACTOR,
                    venue_id=c.key if c.kind == "venue" else None, payload=c.model_dump(),
                )
        for key in self._breached_keys - now:
            await audit(
                "concentration_cleared", f"Concentration back within limits: {key.split(':', 1)[1]}",
                actor=MONITOR_ACTOR,
            )
        self._breached_keys = now

    async def start(self) -> None:
        self._running = True
        logger.info("risk_monitor_started", interval_s=self._interval_s)
        while self._running:
            try:
                await self.evaluate()
            except Exception as exc:  # noqa: BLE001 — the monitor must keep watching
                logger.error("risk_monitor_failed", error=str(exc)[:200])
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        self._running = False
