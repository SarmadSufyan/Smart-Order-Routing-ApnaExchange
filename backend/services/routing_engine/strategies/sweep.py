"""Liquidity sweep: hit every venue at once, in proportion to its depth under a price cap.

Best price is optimal when the books hold still. They don't: the moment one
venue's liquidity is taken, prices elsewhere can move, and a child planned on
one book can find it gone (on testnets, whose books drift, often). A sweep
trades a little price for certainty of filling NOW:

    cap      = best all-in price ± band_bps                 # the worst any child may pay
    avail(v) = liquidity on v whose all-in price is within the cap (capped by v's balance)
    if sum(avail) <= Q:  take everything in the band       # partial: the rest is reported
    else:                share(v) = Q * avail(v) / sum(avail) (on v's lot grid; leftover lots
                         go to the best all-in venues first)
    every child is an IOC LIMIT at v's cap: it may walk v's book up to the cap, never past it

Spreading the order by depth means no single venue's book moving can sink
most of it, and every child carries the same hard price cap. The expected
price is a little worse than best price's (it also uses deeper levels); the
routing proof shows both numbers so the trade-off is visible.
"""

from backend.exchanges.precision import floor_to_step
from backend.shared.models.order import OrderType

from .best_price import EPS, Allocation, BestPriceStrategy, Plan, fmt_price, fmt_qty

DEFAULT_BAND_BPS = 20.0


def proportional_shares(
    quantity: float, available: dict[str, float], steps: dict[str, float], priority: list[str],
) -> dict[str, float]:
    """Split ``quantity`` across venues in proportion to ``available``, on each venue's lot grid.

    Pure and unit-tested. Each share is floored to its venue's step; the lots
    lost to flooring are handed out one at a time to venues in ``priority``
    order (best all-in first) while they still have room.
    """
    total = sum(available.values())
    if total <= EPS:
        return {v: 0.0 for v in available}
    if total <= quantity + EPS:
        return {v: floor_to_step(a, steps.get(v, 0.0)) if steps.get(v) else a for v, a in available.items()}
    shares = {}
    for v, a in available.items():
        raw = quantity * a / total
        shares[v] = floor_to_step(raw, steps[v]) if steps.get(v) else raw
    leftover = round(quantity - sum(shares.values()), 12)
    progress = True
    while leftover > EPS and progress:
        progress = False
        for v in priority:
            if v not in available:
                continue
            step = steps.get(v) or leftover
            if step <= leftover + EPS and shares[v] + step <= available[v] + EPS:
                shares[v] = round(shares[v] + step, 12)
                leftover = round(leftover - step, 12)
                progress = True
                if leftover <= EPS:
                    break
    return shares


class LiquiditySweepStrategy(BestPriceStrategy):
    def __init__(self, band_bps: float = DEFAULT_BAND_BPS) -> None:
        if not 0 < band_bps <= 500:
            raise ValueError(f"sweep band must be between 0 and 500 bps, got {band_bps}")
        self.band_bps = band_bps

    @property
    def name(self) -> str:
        return "liquidity_sweep"

    def allocate(self, plan: Plan, skip: set[str]) -> Allocation:
        out = Allocation(cap_left=dict(plan.capacity))
        order, is_buy = plan.order, plan.is_buy
        rows = [r for r in plan.eligible if r["venue_id"] not in skip]
        if not rows:
            out.remaining = order.quantity
            return out

        # The cap: best all-in top-of-book, widened by the band.
        best = rows[0]["effective_price"]
        cap_all_in = best * (1 + self.band_bps / 1e4) if is_buy else best * (1 - self.band_bps / 1e4)
        out.price_cap = cap_all_in
        raw_cap: dict[str, float] = {}
        available: dict[str, float] = {}
        for r in rows:
            vid = r["venue_id"]
            cost = plan.cost_bps[vid] / 1e4
            cap = cap_all_in / (1 + cost) if is_buy else cap_all_in / (1 - cost)
            if order.order_type == OrderType.LIMIT and order.limit_price:
                cap = min(cap, order.limit_price) if is_buy else max(cap, order.limit_price)
            raw_cap[vid] = cap
            depth = sum(q for p, q in plan.per_venue_levels.get(vid, []) if (p <= cap if is_buy else p >= cap))
            depth = min(depth, plan.capacity.get(vid, float("inf")))
            step = plan.lot_steps.get(vid)
            available[vid] = floor_to_step(depth, step) if step else depth

        shares = proportional_shares(order.quantity, available, plan.lot_steps, [r["venue_id"] for r in rows])

        # Each venue fills its share from its own book, best level first, inside its cap.
        for vid, qty in shares.items():
            if qty <= EPS:
                continue
            left = qty
            for price, size in plan.per_venue_levels.get(vid, []):
                if left <= EPS or (price > raw_cap[vid] if is_buy else price < raw_cap[vid]):
                    break
                take = min(left, size)
                out.notional[vid] += take * price
                out.worst_level[vid] = price
                left = round(left - take, 12)
                out.levels_used += 1
            out.alloc[vid] = qty
            out.child_limits[vid] = raw_cap[vid]
            if vid in out.cap_left:
                out.cap_left[vid] = round(out.cap_left[vid] - qty, 12)
        out.remaining = max(round(order.quantity - sum(out.alloc.values()), 12), 0.0)

        total_available = sum(available.values())
        out.notes.append(
            f"Liquidity sweep: every venue at once, in proportion to its liquidity within {self.band_bps:g} bps "
            f"of the best all-in price — no child may trade beyond {fmt_price(cap_all_in)} all-in"
        )
        if total_available > EPS:
            out.notes.append(
                f"{fmt_qty(total_available)} available inside the cap: "
                + ", ".join(f"{vid} {available[vid] / total_available:.0%}" for vid in available if available[vid] > 0)
            )
        return out
