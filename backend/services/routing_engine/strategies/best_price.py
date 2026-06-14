from uuid import uuid4

from backend.shared.models.market_data import NBBO
from backend.shared.models.order import ChildOrder, Order, OrderSide
from backend.shared.models.routing import (
    RoutingDecision,
    RoutingResult,
    RoutingStatus,
    VenueCandidate,
)

from .base import RoutingStrategy


class BestPriceStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "best_price"

    async def route(
        self,
        order: Order,
        nbbo: NBBO,
        routable_venues: list[str],
    ) -> RoutingResult:
        is_buy = order.side == OrderSide.BUY

        # ── 1. Collect every venue's quote, even the unreachable ones,
        #       so we can show the panel a full decision matrix.
        rows: list[dict] = []
        for vid, quote in nbbo.venue_quotes.items():
            if is_buy:
                price, size = quote.ask_price, quote.ask_size
            else:
                price, size = quote.bid_price, quote.bid_size

            eligible = True
            reason: str | None = None
            if vid not in routable_venues:
                eligible, reason = False, "venue not routable (blacklisted/degraded)"
            elif size <= 0:
                eligible, reason = False, "no size at top-of-book"
            elif price <= 0:
                eligible, reason = False, "invalid price"

            rows.append({
                "venue_id": vid,
                "price": price,
                "size": size,
                "eligible": eligible,
                "excluded_reason": reason,
            })

        # ── 2. Rank by price (best first). For BUY = lowest ask; SELL = highest bid.
        eligible_rows = [r for r in rows if r["eligible"]]
        eligible_rows.sort(key=lambda r: r["price"], reverse=not is_buy)

        # Ineligible rows go after eligible ones, but we still give them a rank
        # so the UI can list them in a consistent order (worst → bottom).
        ineligible_rows = [r for r in rows if not r["eligible"]]
        ineligible_rows.sort(key=lambda r: r["price"] or 0, reverse=not is_buy)

        ordered = eligible_rows + ineligible_rows
        for i, r in enumerate(ordered, start=1):
            r["rank"] = i

        # ── 3. No eligible venues → reject with a decision snapshot anyway
        if not eligible_rows:
            candidates = [
                VenueCandidate(
                    venue_id=r["venue_id"],
                    price=r["price"],
                    size=r["size"],
                    rank=r["rank"],
                    eligible=r["eligible"],
                    excluded_reason=r["excluded_reason"],
                )
                for r in ordered
            ]
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No eligible venues with liquidity",
                decision=RoutingDecision(
                    side=order.side.value,
                    requested_quantity=order.quantity,
                    total_allocated=0.0,
                    candidates=candidates,
                    winning_venues=[],
                    blended_avg_price=0.0,
                    worst_price=0.0,
                    savings_per_share=0.0,
                    total_savings=0.0,
                    is_split=False,
                    notes=[f"All {len(ordered)} venues ineligible"],
                ),
            )

        # ── 4. Walk eligible venues cheapest-first, allocating min(remaining, size).
        child_orders: list[ChildOrder] = []
        remaining = order.quantity
        for r in eligible_rows:
            if remaining <= 0:
                break
            alloc = min(remaining, r["size"])
            if alloc <= 0:
                continue
            child_orders.append(
                ChildOrder(
                    id=uuid4(),
                    parent_order_id=order.id,
                    venue_id=r["venue_id"],
                    quantity=alloc,
                    price=r["price"],
                )
            )
            r["allocated_qty"] = alloc
            r["is_winner"] = True
            remaining -= alloc

        if not child_orders:
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No available liquidity across venues",
            )

        # ── 5. Build the decision snapshot
        total_allocated = sum(c.quantity for c in child_orders)
        blended_avg_price = (
            sum(c.quantity * c.price for c in child_orders) / total_allocated
            if total_allocated > 0 else 0.0
        )
        worst_price = eligible_rows[-1]["price"]  # last (worst) eligible venue

        if is_buy:
            savings_per_share = max(0.0, worst_price - blended_avg_price)
        else:
            savings_per_share = max(0.0, blended_avg_price - worst_price)
        total_savings = savings_per_share * total_allocated

        winning_venues = [c.venue_id for c in child_orders]
        is_split = len(winning_venues) > 1

        candidates = [
            VenueCandidate(
                venue_id=r["venue_id"],
                price=r["price"],
                size=r["size"],
                rank=r["rank"],
                eligible=r["eligible"],
                excluded_reason=r["excluded_reason"],
                allocated_qty=r.get("allocated_qty", 0.0),
                is_winner=r.get("is_winner", False),
            )
            for r in ordered
        ]

        notes: list[str] = []
        best_venue = eligible_rows[0]["venue_id"]
        best_price = eligible_rows[0]["price"]
        notes.append(
            f"{best_venue} had the best {'ask' if is_buy else 'bid'} at ${best_price:.2f}"
        )
        if is_split:
            notes.append(
                f"Top-of-book at {best_venue} was {eligible_rows[0]['size']:.0f} shares — "
                f"smaller than requested {order.quantity:.0f}, so SOR swept down the book"
            )
            notes.append(
                f"Order split across {len(winning_venues)} venues: " +
                ", ".join(f"{c.venue_id}({c.quantity:.0f}@${c.price:.2f})" for c in child_orders)
            )
        else:
            notes.append(
                f"Full {order.quantity:.0f} shares fit at {best_venue} top-of-book "
                f"({eligible_rows[0]['size']:.0f} available)"
            )
        if total_savings > 0:
            notes.append(
                f"Saved ${total_savings:.2f} vs routing the whole order to the worst venue at ${worst_price:.2f}"
            )

        decision = RoutingDecision(
            side=order.side.value,
            requested_quantity=order.quantity,
            total_allocated=total_allocated,
            candidates=candidates,
            winning_venues=winning_venues,
            blended_avg_price=round(blended_avg_price, 4),
            worst_price=round(worst_price, 4),
            savings_per_share=round(savings_per_share, 4),
            total_savings=round(total_savings, 2),
            is_split=is_split,
            notes=notes,
        )

        if remaining > 0:
            return RoutingResult(
                status=RoutingStatus.PARTIAL,
                child_orders=child_orders,
                remaining_quantity=remaining,
                decision=decision,
            )

        return RoutingResult(
            status=RoutingStatus.SUCCESS,
            child_orders=child_orders,
            decision=decision,
        )
