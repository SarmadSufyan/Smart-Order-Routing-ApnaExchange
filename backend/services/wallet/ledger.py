"""Portfolio ledger: opening balances + fills -> holdings, cost and P&L.

Pure functions, no I/O — the P&L service feeds them and the tests pin them.

Accounting (average cost, per venue and asset):
    * Opening balances are lots bought at the mark price when the baseline
      was taken (testnet coins have no real cost).
    * A BUY adds to the lot at the fill price; a SELL realises
      ``qty x (price - avg_cost)``.
    * A fee paid in a coin (base asset, or BNB) is a disposal of those units
      at the fee's market value, plus a fee expense of that value.
    * A fee paid in USDT is a cash outflow and a fee expense.

This makes one identity hold exactly, and the tests check it:
    value_now - opening_value == realized + unrealized - fees == total P&L
Split further:
    hold P&L    = opening quantities x (mark now - mark at baseline)
    trading P&L = total - hold   (what our trades added vs. doing nothing)
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import OrderSide
from backend.shared.models.portfolio import AssetPnL, FillRecord, VenueBaseline

STABLE = {"USDT"}
FLAT_EPS = 1e-12
USD_DP = 8  # sums of large values leave float noise like -4.7e-11; round it away


@dataclass
class Lot:
    """Signed average-cost position in one asset on one venue."""
    qty: float = 0.0
    avg: float = 0.0
    realized: float = 0.0

    def trade(self, signed_qty: float, price: float) -> None:
        if signed_qty == 0:
            return
        prev = self.qty
        new = prev + signed_qty
        if prev == 0 or (prev > 0) == (signed_qty > 0):
            # Opening or adding: volume-weighted average cost.
            self.avg = (abs(prev) * self.avg + abs(signed_qty) * price) / abs(new)
        else:
            closed = min(abs(signed_qty), abs(prev))
            self.realized += closed * (price - self.avg) * (1 if prev > 0 else -1)
            if abs(new) < FLAT_EPS:
                new, self.avg = 0.0, 0.0
            elif (new > 0) != (prev > 0):
                self.avg = price  # flipped through zero: the rest is a new position
        self.qty = new


@dataclass
class Ledger:
    lots: dict[tuple[str, str], Lot] = field(default_factory=dict)  # (venue, asset) -> lot
    cash: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    fees_by_asset: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    applied: int = 0
    before_baseline: int = 0
    notes: list[str] = field(default_factory=list)

    def lot(self, venue_id: str, asset: str) -> Lot:
        return self.lots.setdefault((venue_id, asset), Lot())

    def quantity(self, venue_id: str, asset: str) -> float:
        if asset in STABLE:
            return self.cash.get((venue_id, asset), 0.0)
        lot = self.lots.get((venue_id, asset))
        return lot.qty if lot else 0.0


def fill_base_change(fill: FillRecord) -> float:
    """Base asset that actually moved, net of a fee charged in the base asset."""
    base, _ = split_symbol(fill.symbol)
    fee_in_base = fill.fee if fill.fee_asset == base else 0.0
    return fill.quantity - fee_in_base if fill.side == OrderSide.BUY else fill.quantity + fee_in_base


def build_ledger(baselines: dict[str, VenueBaseline], fills: list[FillRecord]) -> Ledger:
    ledger = Ledger()
    for venue_id, baseline in baselines.items():
        for asset, qty in baseline.balances.items():
            if asset in STABLE:
                ledger.cash[(venue_id, asset)] = qty
            else:
                ledger.lots[(venue_id, asset)] = Lot(qty=qty, avg=baseline.marks.get(asset, 0.0))

    for fill in sorted(fills, key=lambda f: f.ts):
        baseline = baselines.get(fill.venue_id)
        if baseline is None or fill.ts <= baseline.ts:
            ledger.before_baseline += 1  # already inside the opening balances
            continue
        base, quote = split_symbol(fill.symbol)
        if quote not in STABLE:
            ledger.notes.append(f"{fill.symbol} fill skipped: only USDT-quoted pairs are accounted")
            continue
        sign = 1 if fill.side == OrderSide.BUY else -1
        ledger.lot(fill.venue_id, base).trade(sign * fill.quantity, fill.price)
        ledger.cash[(fill.venue_id, quote)] -= sign * fill.quantity * fill.price

        if fill.fee > 0 and fill.fee_asset:
            if fill.fee_asset in STABLE:
                ledger.cash[(fill.venue_id, fill.fee_asset)] -= fill.fee
                fee_usdt = fill.fee
            else:
                fee_lot = ledger.lot(fill.venue_id, fill.fee_asset)
                if fill.fee_asset == base:
                    unit = fill.price
                elif fill.fee_usdt is not None:
                    unit = fill.fee_usdt / fill.fee
                else:
                    unit = fee_lot.avg  # unpriced third-asset fee: value at cost, no gain/loss
                fee_lot.trade(-fill.fee, unit)
                fee_usdt = fill.fee * unit
            ledger.fees_by_asset[base] += fee_usdt
        ledger.applied += 1
    return ledger


@dataclass
class Valuation:
    assets: list[AssetPnL]
    complete: bool
    unpriced: list[str]
    opening_value: float | None
    value_now: float | None
    realized: float
    unrealized: float | None
    fees: float
    hold: float | None
    total: float | None

    @property
    def trading(self) -> float | None:
        if self.total is None or self.hold is None:
            return None
        return round(self.total - self.hold, USD_DP) + 0.0  # + 0.0 turns -0.0 into 0.0


MarkFn = Callable[[str, str], float | None]  # (venue_id, asset) -> USDT per unit


def value_ledger(ledger: Ledger, baselines: dict[str, VenueBaseline], mark: MarkFn) -> Valuation:
    per_asset: dict[str, AssetPnL] = {}
    unpriced: set[str] = set()
    value_now = 0.0
    unrealized = 0.0
    hold = 0.0
    opening_value = 0.0
    cost_by_asset: dict[str, float] = defaultdict(float)

    def row(asset: str) -> AssetPnL:
        return per_asset.setdefault(asset, AssetPnL(asset=asset, quantity=0.0, opening_quantity=0.0))

    for (venue_id, asset), qty in ledger.cash.items():
        r = row(asset)
        r.quantity += qty
        r.by_venue[venue_id] = r.by_venue.get(venue_id, 0.0) + qty
        value_now += qty

    for (venue_id, asset), lot in ledger.lots.items():
        r = row(asset)
        r.quantity += lot.qty
        r.by_venue[venue_id] = r.by_venue.get(venue_id, 0.0) + lot.qty
        r.realized_pnl += lot.realized
        cost_by_asset[asset] += lot.qty * lot.avg
        if abs(lot.qty) < FLAT_EPS:
            continue
        m = mark(venue_id, asset)
        if m is None:
            unpriced.add(asset)
            continue
        value_now += lot.qty * m
        unrealized += lot.qty * (m - lot.avg)
        r.value_usdt = (r.value_usdt or 0.0) + lot.qty * m
        r.unrealized_pnl = (r.unrealized_pnl or 0.0) + lot.qty * (m - lot.avg)

    for venue_id, baseline in baselines.items():
        for asset, qty in baseline.balances.items():
            r = row(asset)
            r.opening_quantity += qty
            if asset in STABLE:
                opening_value += qty
                continue
            opened_at = baseline.marks.get(asset)
            if opened_at is None:
                if qty:
                    unpriced.add(asset)
                continue
            opening_value += qty * opened_at
            if qty:
                m = mark(venue_id, asset)
                if m is None:
                    unpriced.add(asset)
                    continue
                hold += qty * (m - opened_at)
                r.hold_pnl = (r.hold_pnl or 0.0) + qty * (m - opened_at)

    fees = sum(ledger.fees_by_asset.values())
    realized = sum(lot.realized for lot in ledger.lots.values())
    for asset, r in per_asset.items():
        if asset in STABLE:
            r.value_usdt = r.quantity
            r.mark = 1.0
            continue
        r.fees_usdt = ledger.fees_by_asset.get(asset, 0.0)
        if abs(r.quantity) >= FLAT_EPS:
            r.avg_cost = cost_by_asset[asset] / r.quantity
            if r.value_usdt is not None and asset not in unpriced:
                r.mark = r.value_usdt / r.quantity
        elif r.value_usdt is None:
            r.value_usdt = 0.0
        if asset not in unpriced:
            r.unrealized_pnl = r.unrealized_pnl or 0.0
            r.total_pnl = r.realized_pnl + r.unrealized_pnl - r.fees_usdt
            r.hold_pnl = r.hold_pnl or 0.0

    complete = not unpriced

    def usd(value: float) -> float | None:
        return round(value, USD_DP) + 0.0 if complete else None

    return Valuation(
        assets=sorted(per_asset.values(), key=lambda a: -(a.value_usdt or 0.0)),
        complete=complete,
        unpriced=sorted(unpriced),
        opening_value=usd(opening_value),
        value_now=usd(value_now),
        realized=round(realized, USD_DP) + 0.0,
        unrealized=usd(unrealized),
        fees=round(fees, USD_DP) + 0.0,
        hold=usd(hold),
        total=usd(value_now - opening_value),
    )
