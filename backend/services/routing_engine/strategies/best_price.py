"""Best-price routing by walking the consolidated order book.

Every eligible venue's price levels are merged into one ladder, sorted
best-first (lowest ask for a BUY, highest bid for a SELL), and consumed
greedily until the order is filled:

    levels  = [(price, qty, venue) for each eligible venue's book side]
    sort levels best-first                      # O(L log L)
    for price, qty, venue in levels:            # O(L)
        take = floor_to_lot(min(remaining, qty))
        allocate take to venue; remaining -= take

The result is one child order per venue whose size is exactly the liquidity
that venue offers at prices better than any alternative — the textbook SOR
allocation. With a single venue it degenerates to walking that venue's book,
which still yields an honest expected VWAP and slippage estimate.
"""

from collections import defaultdict
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


class BestPriceStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "best_price"

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
    ) -> RoutingResult:
        is_buy = order.side == OrderSide.BUY
        names = venue_names or {}
        exclusions = exclusion_reasons or {}
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
            rows.append({"venue_id": vid, "price": price, "size": size, "eligible": reason is None,
                         "excluded_reason": reason, "allocated_qty": 0.0, "is_winner": False})

        eligible = sorted((r for r in rows if r["eligible"]), key=lambda r: r["price"], reverse=not is_buy)
        ineligible = sorted((r for r in rows if not r["eligible"]), key=lambda r: r["price"] or 0, reverse=not is_buy)
        ordered = eligible + ineligible
        for i, r in enumerate(ordered, start=1):
            r["rank"] = i

        def candidates() -> list[VenueCandidate]:
            return [VenueCandidate(**{k: r[k] for k in VenueCandidate.model_fields}) for r in ordered]

        arrival_mid = (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo.best_bid and nbbo.best_ask else 0.0

        if not eligible:
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No eligible venues with liquidity",
                decision=RoutingDecision(
                    side=order.side.value, requested_quantity=order.quantity, total_allocated=0.0,
                    candidates=candidates(), winning_venues=[], blended_avg_price=0.0, worst_price=0.0,
                    savings_per_share=0.0, total_savings=0.0, is_split=False,
                    notes=[f"All {len(ordered)} quoting venues are ineligible"],
                    base_asset=base, quote_asset=quote, arrival_mid=arrival_mid,
                ),
            )

        # ── 2. Consolidated ladder across all eligible venues, best price first
        venue_rank = {r["venue_id"]: r["rank"] for r in eligible}
        per_venue_levels = {
            r["venue_id"]: _side_levels(depth, r["venue_id"], is_buy, r["price"], r["size"]) for r in eligible
        }
        ladder = [(p, q, vid) for vid, lv in per_venue_levels.items() for p, q in lv if q > 0 and p > 0]
        ladder.sort(key=lambda x: (x[0] if is_buy else -x[0], venue_rank[x[2]]))
        if order.order_type == OrderType.LIMIT and order.limit_price:
            lim = order.limit_price
            ladder = [lv for lv in ladder if (lv[0] <= lim if is_buy else lv[0] >= lim)]

        # ── 3. Greedy walk
        alloc: dict[str, float] = defaultdict(float)
        notional: dict[str, float] = defaultdict(float)
        worst_level: dict[str, float] = {}  # deepest price consumed per venue
        remaining = order.quantity
        levels_used = 0
        for price, size, vid in ladder:
            if remaining <= EPS:
                break
            # A venue can only sell the coins / spend the USDT it actually holds.
            take = min(remaining, size, cap_left.get(vid, float("inf")))
            step = (lot_steps or {}).get(vid)
            if step:
                take = floor_to_step(take, step)
            if take <= 0:
                continue
            if vid in cap_left:
                cap_left[vid] = round(cap_left[vid] - take, 12)
            alloc[vid] = round(alloc[vid] + take, 12)  # keep sums on the lot grid
            notional[vid] += take * price
            worst_level[vid] = price  # ladder is sorted, so the last one is the worst
            remaining = max(round(remaining - take, 12), 0.0)
            levels_used += 1

        if not alloc:
            reason = ("No liquidity at or better than the limit price"
                      if order.order_type == OrderType.LIMIT else "No available liquidity across venues")
            return RoutingResult(
                status=RoutingStatus.REJECTED, rejection_reason=reason,
                decision=RoutingDecision(
                    side=order.side.value, requested_quantity=order.quantity, total_allocated=0.0,
                    candidates=candidates(), winning_venues=[], blended_avg_price=0.0, worst_price=0.0,
                    savings_per_share=0.0, total_savings=0.0, is_split=False, notes=[reason],
                    base_asset=base, quote_asset=quote, arrival_mid=arrival_mid,
                ),
            )

        # ── 4. One child order per winning venue, in best-price order
        child_orders: list[ChildOrder] = []
        for r in eligible:
            vid = r["venue_id"]
            if alloc.get(vid, 0) <= 0:
                continue
            vwap = notional[vid] / alloc[vid]
            is_limit = order.order_type == OrderType.LIMIT and order.limit_price
            child_orders.append(ChildOrder(
                id=uuid4(), parent_order_id=order.id, venue_id=vid, quantity=alloc[vid],
                price=vwap,
                # LIMIT parents: the user's limit. MARKET parents: the worst price
                # we planned to pay; the engine widens it by the protection band.
                limit_price=order.limit_price if is_limit else worst_level[vid],
            ))
            r["allocated_qty"] = alloc[vid]
            r["is_winner"] = True

        total_allocated = sum(alloc.values())
        blended = sum(notional.values()) / total_allocated
        best_price = eligible[0]["price"]

        # "Savings" = SOR VWAP vs sending the whole order to the worst venue alone.
        standalone = {
            vid: _standalone_vwap(lv, total_allocated) for vid, lv in per_venue_levels.items()
        }
        fillable = [v for v in standalone.values() if v is not None]
        if fillable:
            worst = max(fillable) if is_buy else min(fillable)
        else:
            worst = eligible[-1]["price"]
        savings_per_unit = max(0.0, (worst - blended) if is_buy else (blended - worst))
        slippage_bps = ((blended - best_price) if is_buy else (best_price - blended)) / best_price * 1e4

        # ── 5. Human-readable explanation
        winners = [c.venue_id for c in child_orders]
        side_word = "ask" if is_buy else "bid"
        notes = [f"{label(eligible[0]['venue_id'])} had the best executable {side_word} at {fmt_price(best_price)}"]
        # Be explicit when a venue we can't trade on quoted a better price.
        better_elsewhere = [
            r for r in ineligible
            if r["price"] > 0 and r["size"] > 0
            and (r["price"] < best_price if is_buy else r["price"] > best_price)
        ]
        for r in better_elsewhere:
            gap_bps = abs(r["price"] - best_price) / best_price * 1e4
            notes.append(
                f"{label(r['venue_id'])} quoted a better {side_word} ({fmt_price(r['price'])}, "
                f"{gap_bps:.1f} bps better) but was skipped: {r['excluded_reason']}"
            )
        if len(eligible) == 1:
            notes.append(
                "Only one venue can execute this order"
                + (f" ({len(ineligible)} more quoting but not executable)" if ineligible else "")
            )
        if levels_used > 1:
            notes.append(
                f"Walked {levels_used} price levels of the consolidated book: expected VWAP "
                f"{fmt_price(blended)} ({slippage_bps:.2f} bps from the best price)"
            )
        if len(winners) > 1:
            notes.append(
                f"Split across {len(winners)} venues: "
                + ", ".join(f"{c.venue_id} {fmt_qty(c.quantity)} @ ~{fmt_price(c.price)}" for c in child_orders)
            )
        else:
            notes.append(f"All {fmt_qty(total_allocated)} {base} routed to {label(winners[0])}")
        if savings_per_unit * total_allocated > 0.005:
            notes.append(
                f"Saves {fmt_price(savings_per_unit * total_allocated)} vs sending the whole order to the "
                f"worst venue (VWAP {fmt_price(worst)})"
            )
        for vid in winners:
            step = (lot_steps or {}).get(vid) or EPS
            if vid in cap_left and cap_left[vid] < step and capacity:
                notes.append(
                    f"{label(vid)} capped at {fmt_qty(alloc[vid])} {base} by its available "
                    f"{funding_asset} balance"
                )
        if remaining > EPS:
            capped = any(vid in cap_left and cap_left[vid] < ((lot_steps or {}).get(vid) or EPS) for vid in alloc)
            why = "venue balances exhausted" if capped else "visible depth exhausted"
            notes.append(f"{why.capitalize()}: {fmt_qty(remaining)} {base} left unallocated")

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
            best_price=best_price,
            expected_slippage_bps=round(slippage_bps, 3),
            levels_used=levels_used,
        )
        status = RoutingStatus.PARTIAL if remaining > EPS else RoutingStatus.SUCCESS
        return RoutingResult(
            status=status, child_orders=child_orders, remaining_quantity=remaining, decision=decision
        )
