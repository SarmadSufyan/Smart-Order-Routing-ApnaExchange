"""Best-price routing by walking the consolidated order book, fees included.

Every eligible venue's price levels are merged into one ladder, sorted
best-first by their ALL-IN price — the price plus that venue's taker fee
(minus it for a sell) — and consumed greedily until the order is filled:

    cost(v)   = taker_fee_bps(v) [+ extra_cost_bps(v) for smart routing]
    all_in(p) = p * (1 + cost/1e4)  for a BUY,   p * (1 - cost/1e4)  for a SELL
    levels    = [(price, qty, venue) for each eligible venue's book side]
    sort levels by all_in(price) best-first     # O(L log L)
    for price, qty, venue in levels:            # O(L)
        take = floor_to_lot(min(remaining, qty))
        allocate take to venue; remaining -= take
    while some venue's share < its minimum order (lot or value):
        drop the smallest such venue and walk again   # at most V extra walks

Without fees a venue quoting $1 less always wins; with them, a venue whose
fee is 10 bps higher loses unless it is at least 10 bps cheaper — the price
the trader really pays. The result is one child order per venue whose size
is exactly the liquidity that venue offers at all-in prices better than any
alternative — the textbook SOR allocation. With a single venue it
degenerates to walking that venue's book, which still yields an honest
expected VWAP and slippage estimate.

The minimum-size pass matters for small orders (algo slices): a split must
never hand a venue a sliver it will reject.

Subclasses change one thing each: ``SmartStrategy`` adds an expected cost of
a venue missing (``extra_costs``), ``LiquiditySweepStrategy`` replaces the
greedy walk with a proportional sweep under a price cap (``allocate``).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from uuid import uuid4

from backend.exchanges.precision import floor_to_step
from backend.shared.models.market_data import NBBO, split_symbol
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderType
from backend.shared.models.routing import (
    RoutingDecision,
    RoutingResult,
    RoutingStatus,
    VenueCandidate,
)

from .base import RoutingStrategy, VenueDepth

EPS = 1e-12


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    return f"${p:.6f}"


def fmt_qty(q: float) -> str:
    return f"{q:.8f}".rstrip("0").rstrip(".") or "0"


def fee_bps_for(fees: dict[str, float], venue_id: str) -> float:
    """Taker fee of a venue in bps. With no fee table every venue is 0 (plain
    best price); a venue missing from a table gets the highest listed fee — an
    unknown fee must never make a venue look cheaper than it is."""
    if not fees:
        return 0.0
    return fees.get(venue_id, max(fees.values()))


def all_in_price(price: float, cost_bps: float, is_buy: bool) -> float:
    """What one unit really costs (BUY) or brings in (SELL) after ``cost_bps``."""
    return price * (1 + cost_bps / 1e4) if is_buy else price * (1 - cost_bps / 1e4)


def _side_levels(
    depth: VenueDepth | None, venue_id: str, is_buy: bool, top_price: float, top_size: float
) -> list[tuple[float, float]]:
    book = (depth or {}).get(venue_id)
    if book:
        levels = book["asks"] if is_buy else book["bids"]
        if levels:
            return list(levels)
    return [(top_price, top_size)]  # no depth available: top-of-book only


def _standalone_vwap(levels: list[tuple[float, float]], qty: float) -> float | None:
    """VWAP of filling ``qty`` on one venue alone (None if it can't)."""
    remaining, cost = qty, 0.0
    for price, size in levels:
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining <= EPS:
            return cost / qty
    return None


@dataclass
class Plan:
    """Everything an allocation needs, prepared once per routing decision."""

    order: Order
    is_buy: bool
    eligible: list[dict]  # candidate rows, best all-in first
    per_venue_levels: dict[str, list[tuple[float, float]]]  # raw book side, best first, within any limit
    ladder: list[tuple[float, float, str]]  # (price, qty, venue) sorted best all-in first
    cost_bps: dict[str, float]  # fee (+ extra) per venue
    lot_steps: dict[str, float]
    capacity: dict[str, float]  # base-asset units each venue can fund (missing = no cap)


@dataclass
class Allocation:
    alloc: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    notional: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    worst_level: dict[str, float] = field(default_factory=dict)  # deepest raw price consumed per venue
    remaining: float = 0.0
    levels_used: int = 0
    cap_left: dict[str, float] = field(default_factory=dict)
    child_limits: dict[str, float] = field(default_factory=dict)  # strategy-set price cap per venue
    price_cap: float | None = None  # all-in cap (sweep)
    notes: list[str] = field(default_factory=list)


class BestPriceStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "best_price"

    # ── hooks for the other strategies ───────────────────────────────
    def extra_costs(
        self, rows: list[dict], is_buy: bool, nbbo: NBBO, fill_ratios: dict[str, tuple[float, int]],
    ) -> tuple[dict[str, float], list[str]]:
        """Extra cost per venue in bps on top of its fee, with explanations. None here."""
        return {}, []

    def allocate(self, plan: Plan, skip: set[str]) -> Allocation:
        """Greedy walk of the consolidated ladder (best all-in level first)."""
        out = Allocation(cap_left=dict(plan.capacity))
        remaining = plan.order.quantity
        for price, size, vid in plan.ladder:
            if remaining <= EPS:
                break
            if vid in skip:
                continue
            # A venue can only sell the coins / spend the USDT it actually holds.
            take = min(remaining, size, out.cap_left.get(vid, float("inf")))
            step = plan.lot_steps.get(vid)
            if step:
                take = floor_to_step(take, step)
            if take <= 0:
                continue
            if vid in out.cap_left:
                out.cap_left[vid] = round(out.cap_left[vid] - take, 12)
            out.alloc[vid] = round(out.alloc[vid] + take, 12)  # keep sums on the lot grid
            out.notional[vid] += take * price
            out.worst_level[vid] = price  # the venue's levels are best-first, so the last is the worst
            remaining = max(round(remaining - take, 12), 0.0)
            out.levels_used += 1
        out.remaining = remaining
        return out

    # ── the decision ────────────────────────────────────────────────
    async def route(
        self,
        order: Order,
        nbbo: NBBO,
        routable_venues: list[str],
        depth: VenueDepth | None = None,
        lot_steps: dict[str, float] | None = None,
        venue_names: dict[str, str] | None = None,
        exclusion_reasons: dict[str, str] | None = None,
        capacity: dict[str, float] | None = None,
        min_sizes: dict[str, tuple[float, float]] | None = None,
        fees_bps: dict[str, float] | None = None,
        fill_ratios: dict[str, tuple[float, int]] | None = None,
    ) -> RoutingResult:
        is_buy = order.side == OrderSide.BUY
        names = venue_names or {}
        exclusions = exclusion_reasons or {}
        minimums = min_sizes or {}
        fees = fees_bps or {}
        base, quote = split_symbol(order.symbol)
        funding_asset = quote if is_buy else base
        cap_left = dict(capacity or {})  # base-asset units each venue can still fund

        def label(vid: str) -> str:
            return f"{names[vid]} ({vid})" if vid in names else vid

        # ── 1. Every quoting venue becomes a candidate row (eligible or not)
        rows: list[dict] = []
        for vid, q in nbbo.venue_quotes.items():
            price, size = (q.ask_price, q.ask_size) if is_buy else (q.bid_price, q.bid_size)
            reason = None
            if vid not in routable_venues:
                reason = exclusions.get(vid, "venue not routable (blacklisted/critical/disconnected)")
            elif size <= 0:
                reason = "no size at top-of-book"
            elif price <= 0:
                reason = "invalid price"
            elif vid in cap_left and cap_left[vid] <= ((lot_steps or {}).get(vid) or EPS):
                reason = f"no {funding_asset} balance on this venue to fund the order"
            fee = fee_bps_for(fees, vid)
            rows.append({"venue_id": vid, "price": price, "size": size, "eligible": reason is None,
                         "excluded_reason": reason, "allocated_qty": 0.0, "is_winner": False,
                         "fee_bps": fee, "extra_cost_bps": 0.0, "fill_ratio": None,
                         "effective_price": all_in_price(price, fee, is_buy) if price > 0 else 0.0})

        extra, extra_notes = self.extra_costs([r for r in rows if r["eligible"]], is_buy, nbbo, fill_ratios or {})
        for r in rows:
            r["extra_cost_bps"] = round(extra.get(r["venue_id"], 0.0), 4)
            ratio = (fill_ratios or {}).get(r["venue_id"])
            r["fill_ratio"] = round(ratio[0], 4) if ratio else None
            if r["price"] > 0:
                r["effective_price"] = all_in_price(r["price"], r["fee_bps"] + r["extra_cost_bps"], is_buy)
        cost = {r["venue_id"]: r["fee_bps"] + r["extra_cost_bps"] for r in rows}

        eligible = sorted((r for r in rows if r["eligible"]), key=lambda r: r["effective_price"], reverse=not is_buy)
        ineligible = sorted((r for r in rows if not r["eligible"]), key=lambda r: r["price"] or 0, reverse=not is_buy)
        ordered = eligible + ineligible
        for i, r in enumerate(ordered, start=1):
            r["rank"] = i

        def candidates() -> list[VenueCandidate]:
            return [VenueCandidate(**{k: r[k] for k in VenueCandidate.model_fields}) for r in ordered]

        arrival_mid = (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo.best_bid and nbbo.best_ask else 0.0

        def rejected(reason: str, note: str | None = None) -> RoutingResult:
            return RoutingResult(
                status=RoutingStatus.REJECTED, rejection_reason=reason,
                decision=RoutingDecision(
                    side=order.side.value, requested_quantity=order.quantity, total_allocated=0.0,
                    candidates=candidates(), winning_venues=[], blended_avg_price=0.0, worst_price=0.0,
                    savings_per_share=0.0, total_savings=0.0, is_split=False, notes=[note or reason],
                    base_asset=base, quote_asset=quote, arrival_mid=arrival_mid, strategy=self.name,
                ),
            )

        if not eligible:
            return rejected("No eligible venues with liquidity", f"All {len(ordered)} quoting venues are ineligible")

        # ── 2. Consolidated ladder across all eligible venues, best all-in price first
        venue_rank = {r["venue_id"]: r["rank"] for r in eligible}
        per_venue_levels = {
            r["venue_id"]: _side_levels(depth, r["venue_id"], is_buy, r["price"], r["size"]) for r in eligible
        }
        if order.order_type == OrderType.LIMIT and order.limit_price:
            lim = order.limit_price  # the user's limit is a raw exchange price
            per_venue_levels = {
                vid: [lv for lv in levels if (lv[0] <= lim if is_buy else lv[0] >= lim)]
                for vid, levels in per_venue_levels.items()
            }
        ladder = [(p, q, vid) for vid, lv in per_venue_levels.items() for p, q in lv if q > 0 and p > 0]

        def ladder_key(level: tuple[float, float, str]) -> tuple[float, int]:
            eff = all_in_price(level[0], cost[level[2]], is_buy)
            return (eff if is_buy else -eff, venue_rank[level[2]])

        ladder.sort(key=ladder_key)
        plan = Plan(order=order, is_buy=is_buy, eligible=eligible, per_venue_levels=per_venue_levels,
                    ladder=ladder, cost_bps=cost, lot_steps=dict(lot_steps or {}), capacity=dict(cap_left))

        # ── 3. Allocate. A venue rejects an order below its own minimum lot or
        # value, so a share that small must go elsewhere: drop the smallest
        # offender and allocate again (at most once per venue).
        too_small: dict[str, str] = {}
        while True:
            result = self.allocate(plan, set(too_small))
            offenders: dict[str, str] = {}
            for vid, qty in result.alloc.items():
                if qty <= 0:
                    continue
                min_qty, min_value = minimums.get(vid, (0.0, 0.0))
                if min_qty and qty < min_qty - EPS:
                    offenders[vid] = f"its share {fmt_qty(qty)} {base} is below its minimum order {fmt_qty(min_qty)} {base}"
                elif min_value and result.notional[vid] < min_value - 1e-9:
                    offenders[vid] = (f"its share ({fmt_qty(qty)} {base}, {fmt_price(result.notional[vid])}) is below "
                                      f"its minimum order value {fmt_price(min_value)}")
            if not offenders:
                break
            smallest = min(offenders, key=lambda v: result.notional[v])
            too_small[smallest] = offenders[smallest]
        for r in rows:
            if r["venue_id"] in too_small:
                r["eligible"] = False
                r["excluded_reason"] = f"order too small for this venue: {too_small[r['venue_id']]}"
        alloc, notional = {v: q for v, q in result.alloc.items() if q > 0}, result.notional

        if not alloc:
            reason = ("No liquidity at or better than the limit price"
                      if order.order_type == OrderType.LIMIT else "No available liquidity across venues")
            if result.price_cap is not None:
                reason = f"No liquidity within the sweep's price cap ({fmt_price(result.price_cap)} all-in)"
            if too_small and len(too_small) == len(eligible):
                reason = ("Order is below every venue's minimum order size: "
                          + "; ".join(f"{vid} {why}" for vid, why in too_small.items()))
            return rejected(reason)

        # ── 4. One child order per winning venue, in best all-in order
        child_orders: list[ChildOrder] = []
        for r in eligible:
            vid = r["venue_id"]
            if alloc.get(vid, 0) <= 0:
                continue
            vwap = notional[vid] / alloc[vid]
            is_limit = order.order_type == OrderType.LIMIT and order.limit_price
            strategy_cap = result.child_limits.get(vid)
            child_orders.append(ChildOrder(
                id=uuid4(), parent_order_id=order.id, venue_id=vid, quantity=alloc[vid],
                price=vwap,
                # A strategy's own cap (sweep) is final. Otherwise LIMIT parents use the
                # user's limit and MARKET parents the worst price we planned to pay,
                # which the engine widens by the protection band.
                limit_price=strategy_cap if strategy_cap is not None
                else order.limit_price if is_limit else result.worst_level[vid],
                limit_final=strategy_cap is not None,
            ))
            r["allocated_qty"] = alloc[vid]
            r["is_winner"] = True

        total_allocated = sum(alloc.values())
        blended = sum(notional[v] for v in alloc) / total_allocated
        fees_paid = sum(notional[v] * fee_bps_for(fees, v) / 1e4 for v in alloc)
        all_in = (sum(notional[v] for v in alloc) + (fees_paid if is_buy else -fees_paid)) / total_allocated
        usable = [r for r in eligible if r["venue_id"] not in too_small]  # never empty here
        best_touch = (min if is_buy else max)(r["price"] for r in usable)
        cheapest = usable[0]

        # "Savings" = SOR all-in price vs sending the whole order to the worst venue alone.
        standalone = {}
        for vid, lv in per_venue_levels.items():
            vwap_alone = _standalone_vwap(lv, total_allocated)
            if vwap_alone is not None:
                standalone[vid] = all_in_price(vwap_alone, fee_bps_for(fees, vid), is_buy)
        if standalone:
            worst = max(standalone.values()) if is_buy else min(standalone.values())
        else:
            worst = eligible[-1]["effective_price"]
        savings_per_unit = max(0.0, (worst - all_in) if is_buy else (all_in - worst))
        slippage_bps = ((blended - best_touch) if is_buy else (best_touch - blended)) / best_touch * 1e4

        # ── 5. Human-readable explanation
        winners = [c.venue_id for c in child_orders]
        side_word = "ask" if is_buy else "bid"
        notes = list(result.notes)
        if fees:
            notes.insert(0,
                f"{label(cheapest['venue_id'])} is the best all-in: {side_word} {fmt_price(cheapest['price'])} "
                f"{'+' if is_buy else '-'} {cheapest['fee_bps'] + cheapest['extra_cost_bps']:g} bps = "
                f"{fmt_price(cheapest['effective_price'])}")
            raw_best = (min if is_buy else max)(usable, key=lambda r: r["price"])
            if raw_best["venue_id"] != cheapest["venue_id"] and raw_best["price"] != cheapest["price"]:
                notes.insert(1,
                    f"{label(raw_best['venue_id'])} quoted the better raw {side_word} ({fmt_price(raw_best['price'])}) "
                    f"but its costs ({raw_best['fee_bps'] + raw_best['extra_cost_bps']:g} bps) make it worse all-in "
                    f"({fmt_price(raw_best['effective_price'])})")
        else:
            notes.insert(0, f"{label(cheapest['venue_id'])} had the best executable {side_word} at "
                            f"{fmt_price(cheapest['price'])}")
        notes.extend(extra_notes)
        for vid, why in too_small.items():
            notes.append(f"{label(vid)} skipped for this order: {why} — re-routed to the other venues")
        # Be explicit when a venue we can't trade on quoted a better price.
        better_elsewhere = [
            r for r in ineligible
            if r["price"] > 0 and r["size"] > 0
            and (r["price"] < best_touch if is_buy else r["price"] > best_touch)
        ]
        for r in better_elsewhere:
            gap_bps = abs(r["price"] - best_touch) / best_touch * 1e4
            notes.append(
                f"{label(r['venue_id'])} quoted a better {side_word} ({fmt_price(r['price'])}, "
                f"{gap_bps:.1f} bps better) but was skipped: {r['excluded_reason']}"
            )
        if len(eligible) == 1 and not too_small:
            notes.append(
                "Only one venue can execute this order"
                + (f" ({len(ineligible)} more quoting but not executable)" if ineligible else "")
            )
        if result.levels_used > 1:
            notes.append(
                f"Walked {result.levels_used} price levels of the consolidated book: expected VWAP "
                f"{fmt_price(blended)} ({slippage_bps:.2f} bps from the best price)"
            )
        if len(winners) > 1:
            notes.append(
                f"Split across {len(winners)} venues: "
                + ", ".join(f"{c.venue_id} {fmt_qty(c.quantity)} @ ~{fmt_price(c.price)}" for c in child_orders)
            )
        else:
            notes.append(f"All {fmt_qty(total_allocated)} {base} routed to {label(winners[0])}")
        if fees_paid > 0:
            notes.append(f"Expected taker fees {fmt_price(fees_paid)} {quote}; all-in price {fmt_price(all_in)}")
        if savings_per_unit * total_allocated > 0.005:
            notes.append(
                f"Saves {fmt_price(savings_per_unit * total_allocated)} vs sending the whole order to the "
                f"worst venue ({fmt_price(worst)} all-in)"
            )
        for vid in winners:
            step = (lot_steps or {}).get(vid) or EPS
            if vid in result.cap_left and result.cap_left[vid] < step and capacity:
                notes.append(
                    f"{label(vid)} capped at {fmt_qty(alloc[vid])} {base} by its available "
                    f"{funding_asset} balance"
                )
        if result.remaining > EPS:
            capped = any(vid in result.cap_left and result.cap_left[vid] < ((lot_steps or {}).get(vid) or EPS)
                         for vid in alloc)
            why = ("venue balances exhausted" if capped
                   else "liquidity within the price cap exhausted" if result.price_cap is not None
                   else "visible depth exhausted")
            notes.append(f"{why.capitalize()}: {fmt_qty(result.remaining)} {base} left unallocated")

        decision = RoutingDecision(
            side=order.side.value,
            requested_quantity=order.quantity,
            total_allocated=total_allocated,
            candidates=candidates(),
            winning_venues=winners,
            blended_avg_price=round(blended, 8),
            worst_price=round(worst, 8),
            savings_per_share=round(savings_per_unit, 8),
            total_savings=round(savings_per_unit * total_allocated, 4),
            is_split=len(winners) > 1,
            notes=notes,
            base_asset=base,
            quote_asset=quote,
            arrival_mid=round(arrival_mid, 8),
            best_price=best_touch,
            expected_slippage_bps=round(slippage_bps, 3),
            levels_used=result.levels_used,
            strategy=self.name,
            expected_fees_quote=round(fees_paid, 6),
            all_in_avg_price=round(all_in, 8),
            price_cap=round(result.price_cap, 8) if result.price_cap is not None else None,
        )
        status = RoutingStatus.PARTIAL if result.remaining > EPS else RoutingStatus.SUCCESS
        return RoutingResult(
            status=status, child_orders=child_orders, remaining_quantity=result.remaining, decision=decision
        )
