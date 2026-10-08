"""Profit-cycle bot: the decisions, as pure functions (the engine runs them every few seconds).

    buy a lot  ->  its target = what it cost (incl. the buy fee) x (1 + threshold)
                               / (coins held x (1 - sell fee))
    sell a lot only when the best bid reaches its target  ->  repeat

So every COMPLETED cycle earns at least the threshold after both fees. What it can't
promise: that the price comes back after a buy. A lot bought before a fall waits — the
bot never sells it below its target (the owner can, from the page).

Fees: the higher of what the exchange charged and the real-market taker fee
(``ARB_TAKER_FEES_BPS``), on both sides — so a profit made on a fee-free testnet is
also a profit at real-market fees.

More lots: only after the price falls ``spacing_pct`` under the cheapest open lot (and
a cooldown after the last buy), so the budget isn't spent at one price; at most
``max_lots`` lots wait to sell at once. No new buys while the open lots are more than
``max_drawdown_pct`` under what they cost.
"""

from dataclasses import dataclass
from datetime import datetime

from backend.exchanges.precision import floor_to_step
from backend.shared.models.automation import CycleParams, Lot


def target_price(market_cost_usdt: float, qty: float, sell_fee_rate: float, threshold_pct: float) -> float:
    """Lowest price at which selling ``qty`` returns ``market_cost_usdt`` x (1 + threshold) after the sell fee."""
    if qty <= 0:
        return float("inf")
    return market_cost_usdt * (1 + threshold_pct / 100) / (qty * (1 - sell_fee_rate))


def drawdown_pct(lots: list[Lot], bid: float) -> float:
    """How far the open lots are under what they cost, in % (0 when above)."""
    cost = sum(lot.cost_usdt for lot in lots)
    if cost <= 0:
        return 0.0
    return max(0.0, (cost - sum(lot.qty for lot in lots) * bid) / cost * 100)


@dataclass
class BuyDecision:
    buy: bool
    reason: str


def buy_decision(p: CycleParams, open_lots: list[Lot], cash_usdt: float, ask: float, bid: float,
                 last_buy_at: datetime | None, now: datetime) -> BuyDecision:
    """Buy another lot now? With the reason either way (shown on the page)."""
    if len(open_lots) >= p.lots_allowed:
        return BuyDecision(False, f"all {p.lots_allowed} lots are waiting to sell")
    if cash_usdt + 1e-9 < p.buy_usdt:
        return BuyDecision(False, f"budget in use: ${cash_usdt:,.2f} left, a buy needs ${p.buy_usdt:,.2f}")
    if last_buy_at is not None and (wait := p.cooldown_s - (now - last_buy_at).total_seconds()) > 0:
        return BuyDecision(False, f"cooling down after the last buy ({wait:.0f} s left)")
    if open_lots:
        dd = drawdown_pct(open_lots, bid)
        if dd >= p.max_drawdown_pct:
            return BuyDecision(False, f"open lots are {dd:.2f}% under cost (limit {p.max_drawdown_pct:g}%): "
                                      "no new buys until they recover")
        lowest = min(lot.buy_price for lot in open_lots)
        trigger = lowest * (1 - p.spacing_pct / 100)
        if ask > trigger:
            return BuyDecision(False, f"next buy at {trigger:,.6g} or lower ({p.spacing_pct:g}% under the "
                                      f"cheapest open lot, {lowest:,.6g}); ask now {ask:,.6g}")
        return BuyDecision(True, f"ask {ask:,.6g} is {p.spacing_pct:g}%+ under the cheapest open lot ({lowest:,.6g})")
    return BuyDecision(True, "no open lot: start a cycle")


def buy_quantity(buy_usdt: float, ask: float, protection_rate: float, step: float) -> float:
    """Coins for one buy, so the worst price the router may pay still fits in ``buy_usdt``."""
    if ask <= 0:
        return 0.0
    return floor_to_step(buy_usdt / (ask * (1 + protection_rate)), step)


def lots_to_sell(lots: list[Lot], bid: float, min_qty: float, now: datetime) -> list[Lot]:
    """Open lots whose target the bid has reached (not waiting after an unfilled try).

    Sold together in one order whose limit is the highest of their targets, so each lot
    gets at least its own target. Lots too small to sell alone (dust after partial fills)
    only go out together with others.
    """
    ready = [lot for lot in lots if lot.target_price <= bid and (lot.retry_after is None or lot.retry_after <= now)]
    if sum(lot.qty for lot in ready) + 1e-12 < min_qty:
        return []
    return sorted(ready, key=lambda lot: lot.target_price)


def allocate_sale(lots: list[Lot], sold_qty: float) -> list[tuple[Lot, float]]:
    """Split a (possibly partial) sale over the lots it was for, cheapest target first."""
    out, left = [], sold_qty
    for lot in sorted(lots, key=lambda x: x.target_price):
        take = min(lot.qty, left)
        if take > 0:
            out.append((lot, take))
            left -= take
        if left <= 1e-15:
            break
    return out
