"""P&L service: the portfolio book of record.

    exchange balances ──(first readable snapshot per venue)──> baseline
    every FILL / PARTIAL execution report ──────────────────> fills
    baseline + fills ──(ledger.py)──> expected holdings, cost, P&L
    expected holdings vs. exchange balances ────────────────> reconciliation

Baselines and fills are persisted, so P&L and the risk engine's positions
survive a restart. A reconciliation break means balances changed outside
ApnaSOR (a testnet reset, a manual trade, a deposit): the operator can accept
the exchange's balances as a new baseline for that venue.
"""

from datetime import datetime, timedelta

from backend.exchanges.registry import ExchangeRegistry
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.execution_report import ExecType
from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import OrderSide
from backend.shared.models.portfolio import (
    FillRecord,
    PortfolioPnL,
    ReconItem,
    VenueBaseline,
    VenueReconciliation,
)
from backend.shared.models.wallet import WalletSnapshot
from backend.shared.utils.logger import get_logger

from .ledger import STABLE, Ledger, build_ledger, fill_base_change, value_ledger

logger = get_logger("pnl_service")

# Balances fetched this soon after a fill may not include it yet.
SETTLE_WINDOW = timedelta(seconds=3)
# Differences worth less than this are rounding, not a break.
RECON_TOLERANCE_USDT = 0.01


class BaselineBusyError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "BASELINE_BUSY")


class PnLService:
    def __init__(
        self,
        registry: ExchangeRegistry,
        wallet,
        order_manager,
        position_tracker,
        store=None,
        mode: str = "testnet",
    ) -> None:
        self._registry = registry
        self._wallet = wallet
        self._orders = order_manager
        self._positions = position_tracker
        self._store = store
        self._mode = mode
        self._baselines: dict[str, VenueBaseline] = {}
        self._fills: list[FillRecord] = []
        self._last_fill_at: dict[str, datetime] = {}
        self._last_marks: dict[tuple[str, str], float] = {}

    # ── lifecycle ───────────────────────────────────────────────────
    async def load(self) -> None:
        """Restore baselines and fills from the store, then rebuild positions."""
        if self._store is None:
            return
        self._baselines = await self._store.load_baselines()
        self._fills = await self._store.load_fills()
        for f in self._fills:
            self._last_fill_at[f.venue_id] = max(self._last_fill_at.get(f.venue_id, f.ts), f.ts)
        self.rebuild_positions()
        logger.info("pnl_restored", baselines=sorted(self._baselines), fills=len(self._fills))

    def start(self) -> None:
        event_bus.subscribe("execution_report", self._on_execution_report)
        event_bus.subscribe("wallet_update", self._on_wallet_update)

    def stop(self) -> None:
        event_bus.unsubscribe("execution_report", self._on_execution_report)
        event_bus.unsubscribe("wallet_update", self._on_wallet_update)

    # ── inputs ──────────────────────────────────────────────────────
    async def _on_execution_report(self, data: dict) -> None:
        if data.get("exec_type") not in (ExecType.FILL.value, ExecType.PARTIAL.value):
            return
        qty = float(data.get("quantity") or 0.0)
        if qty <= 0:
            return
        fee = float(data.get("fee") or 0.0)
        fee_asset = data.get("fee_asset")
        fill = FillRecord(
            id=data.get("child_order_id") or data["order_id"],
            order_id=data["order_id"],
            venue_id=data["venue_id"],
            symbol=data["symbol"],
            side=OrderSide(data["side"]),
            quantity=qty,
            price=float(data.get("price") or 0.0),
            fee=fee,
            fee_asset=fee_asset,
            fee_usdt=self._fee_value(data["venue_id"], data["symbol"], float(data.get("price") or 0.0), fee, fee_asset),
            exchange_order_id=data.get("exchange_order_id"),
        )
        self.record_fill(fill)

    def record_fill(self, fill: FillRecord) -> None:
        self._fills.append(fill)
        self._last_fill_at[fill.venue_id] = fill.ts
        if self._store is not None:
            self._store.record_fill(fill)

    def _fee_value(self, venue_id: str, symbol: str, price: float, fee: float, fee_asset: str | None) -> float | None:
        if fee <= 0 or not fee_asset:
            return 0.0
        base, _ = split_symbol(symbol)
        if fee_asset in STABLE:
            return fee
        if fee_asset == base:
            return fee * price
        m = self.mark(venue_id, fee_asset)
        return fee * m if m is not None else None

    async def _on_wallet_update(self, _data: dict) -> None:
        await self.capture_baselines(self._wallet.get_snapshot())

    # ── baselines ───────────────────────────────────────────────────
    def _busy_reason(self, venue_id: str, fetched_at: datetime) -> str | None:
        """Why this balance snapshot can't be trusted as a baseline right now."""
        if self._orders.has_in_flight():
            return "an order is being executed"
        last = self._last_fill_at.get(venue_id)
        if last is not None and fetched_at < last + SETTLE_WINDOW:
            return "a fill has not settled into the balance yet"
        return None

    async def capture_baselines(
        self,
        snapshot: WalletSnapshot,
        venues: set[str] | None = None,
        set_by: str = "auto",
        force: bool = False,
    ) -> list[str]:
        """Take opening balances from a wallet snapshot.

        Automatic capture happens once per venue (the first time its balances
        are readable and fully priced). ``force`` replaces an existing
        baseline — the operator accepting the exchange's balances.
        """
        captured: list[str] = []
        for venue in snapshot.venues:
            vid = venue.venue_id
            if venues is not None and vid not in venues:
                continue
            if vid in self._baselines and not force:
                continue
            if venue.error or snapshot.as_of is None:
                continue
            if self._busy_reason(vid, venue.updated_at):
                continue
            balances = {a.asset: a.total for a in venue.assets}
            marks = {a.asset: a.price_usdt for a in venue.assets if a.asset not in STABLE and a.price_usdt}
            if any(q and a not in STABLE and a not in marks for a, q in balances.items()):
                continue  # books still syncing: wait until every holding has a price
            baseline = VenueBaseline(
                venue_id=vid, ts=venue.updated_at, set_by=set_by, balances=balances, marks=marks,
            )
            self._baselines[vid] = baseline
            if self._store is not None:
                self._store.save_baseline(baseline)
            captured.append(vid)
            held = ", ".join(f"{q:g} {a}" for a, q in balances.items() if q) or "no tracked balances"
            await audit(
                "baseline_set",
                f"Opening balances for {venue.name} {'accepted from the exchange' if force else 'captured'}: {held}",
                actor=set_by, venue_id=vid, payload=baseline.model_dump(mode="json"),
            )
        if captured:
            self.rebuild_positions()
            logger.info("baselines_captured", venues=captured, set_by=set_by)
        return captured

    async def rebaseline(self, venue_id: str | None, actor: str) -> list[str]:
        """Accept the exchanges' current balances as the new starting point."""
        if self._orders.has_in_flight():
            raise BaselineBusyError("An order is being executed — try again in a moment")
        snapshot = await self._wallet.refresh()
        venues = {venue_id} if venue_id else None
        readable = [v for v in snapshot.venues if not v.error and (venues is None or v.venue_id in venues)]
        if not readable:
            raise BaselineBusyError(f"No readable balances for {venue_id or 'any venue'}")
        for v in readable:
            if reason := self._busy_reason(v.venue_id, v.updated_at):
                raise BaselineBusyError(f"{v.name}: {reason} — try again in a few seconds")
        captured = await self.capture_baselines(snapshot, venues=venues, set_by=actor, force=True)
        if not captured:
            raise BaselineBusyError("Balances are not fully priced yet — try again in a few seconds")
        return captured

    # ── derived state ───────────────────────────────────────────────
    def active_fills(self) -> list[FillRecord]:
        """Fills made after their venue's baseline (the ones not already in it)."""
        return [
            f for f in self._fills
            if (b := self._baselines.get(f.venue_id)) is not None and f.ts > b.ts
        ]

    def rebuild_positions(self) -> None:
        """Risk positions = net traded since the baseline, from the same fills."""
        self._positions.reset()
        for f in sorted(self.active_fills(), key=lambda f: f.ts):
            self._positions.update_position(f.symbol, f.side, fill_base_change(f), f.price)

    def mark(self, venue_id: str, asset: str) -> float | None:
        """USDT price from our own books; the last good one if a book blips."""
        if asset in STABLE:
            return 1.0
        price = self._wallet.price_usdt(asset, venue_id)
        if price is not None:
            self._last_marks[(venue_id, asset)] = price
            return price
        return self._last_marks.get((venue_id, asset))

    def ledger(self) -> Ledger:
        return build_ledger(self._baselines, self._fills)

    def compute(self) -> PortfolioPnL:
        ledger = self.ledger()
        val = value_ledger(ledger, self._baselines, self.mark)
        notes = list(dict.fromkeys(ledger.notes))
        if not self._baselines:
            notes.append("Waiting for the first readable, fully priced exchange balances to set opening balances.")
        if val.unpriced:
            notes.append(f"No live price yet for {', '.join(val.unpriced)} — totals appear once books sync.")
        return PortfolioPnL(
            mode=self._mode,
            since=min((b.ts for b in self._baselines.values()), default=None),
            complete=val.complete and bool(self._baselines),
            unpriced_assets=val.unpriced,
            opening_value_usdt=val.opening_value,
            value_usdt=val.value_now,
            total_pnl=val.total,
            hold_pnl=val.hold,
            trading_pnl=val.trading,
            realized_pnl=val.realized,
            unrealized_pnl=val.unrealized,
            fees_usdt=val.fees,
            fills_applied=ledger.applied,
            fills_before_baseline=ledger.before_baseline,
            assets=val.assets,
            reconciliation=self._reconcile(ledger),
            notes=notes,
        )

    def trading_pnl(self) -> float | None:
        if not self._baselines:
            return None
        return value_ledger(self.ledger(), self._baselines, self.mark).trading

    def _reconcile(self, ledger: Ledger) -> list[VenueReconciliation]:
        snapshot = self._wallet.get_snapshot()
        wallets = {v.venue_id: v for v in snapshot.venues}
        out: list[VenueReconciliation] = []
        for adapter in self._registry.all():
            vid = adapter.venue_id
            baseline = self._baselines.get(vid)
            wallet = wallets.get(vid)
            rec = VenueReconciliation(
                venue_id=vid, name=adapter.name, status="OK",
                baseline_at=baseline.ts if baseline else None,
                baseline_set_by=baseline.set_by if baseline else None,
            )
            if baseline is None:
                rec.status = "NO_BASELINE"
                rec.note = (wallet.error if wallet and wallet.error
                            else "opening balances not captured yet")
                out.append(rec)
                continue
            if wallet is None or wallet.error:
                rec.status = "UNKNOWN"
                rec.note = wallet.error if wallet else "balances not read yet"
                out.append(rec)
                continue

            settling = self._busy_reason(vid, wallet.updated_at) is not None
            actual = {a.asset: a.total for a in wallet.assets}
            assets = sorted(set(actual) | set(baseline.balances)
                            | {a for (v, a) in ledger.lots if v == vid}
                            | {a for (v, a) in ledger.cash if v == vid})
            worst = "OK"
            for asset in assets:
                expected = ledger.quantity(vid, asset)
                got = actual.get(asset, 0.0)
                diff = got - expected
                m = self.mark(vid, asset)
                diff_usdt = diff * m if m is not None else None
                small = abs(diff) <= 1e-9 or (diff_usdt is not None and abs(diff_usdt) < RECON_TOLERANCE_USDT)
                status = "OK" if small else ("PENDING" if settling else "BREAK")
                if status != "OK" and worst != "BREAK":
                    worst = status
                if expected or got:
                    rec.items.append(ReconItem(
                        asset=asset, expected=expected, actual=got, difference=diff,
                        difference_usdt=diff_usdt, status=status,
                    ))
            rec.status = worst
            if worst == "BREAK":
                rec.note = ("Exchange balances changed outside ApnaSOR (sandbox reset, manual trade "
                            "or deposit). Accept the exchange balances to start a new baseline.")
            elif worst == "PENDING":
                rec.note = "A recent fill is still settling into the exchange balance."
            out.append(rec)
        return out

    @property
    def baselines(self) -> dict[str, VenueBaseline]:
        return dict(self._baselines)
