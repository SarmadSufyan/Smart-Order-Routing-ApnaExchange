"""Smart routing: best all-in price, plus what each venue's unreliability is expected to cost.

A venue that quotes the best price but often fails to fill our IOC child is
not really the cheapest: the missed quantity has to be bought again — later
or elsewhere. Smart routing measures that and charges it to the venue:

    ratio(v)     = filled / requested over our last IOC children on v   # measured, not assumed
    miss_cost(v) = max(gap from v to the next-best venue's all-in price, one consolidated spread)
    extra(v)     = (1 - ratio(v)) * miss_cost(v)                        # bps, added to v's fee
    then rank and walk exactly like best price

A venue with no history yet (fewer than ``MIN_SAMPLES`` children) is ranked
on price and fee only, and the proof says so. With every venue filling
reliably, smart and best price route identically.
"""

from backend.shared.models.market_data import NBBO

from .best_price import BestPriceStrategy

MIN_SAMPLES = 5  # children before a venue's fill ratio is trusted


def miss_penalty_bps(ratio: float, gap_bps: float, spread_bps: float) -> float:
    """Expected extra cost in bps of routing to a venue that fills ``ratio`` of what it's sent."""
    miss = min(max(1.0 - ratio, 0.0), 1.0)
    return miss * max(gap_bps, spread_bps, 0.0)


class SmartStrategy(BestPriceStrategy):
    @property
    def name(self) -> str:
        return "smart"

    def extra_costs(
        self, rows: list[dict], is_buy: bool, nbbo: NBBO, fill_ratios: dict[str, tuple[float, int]],
    ) -> tuple[dict[str, float], list[str]]:
        mid = (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo.best_bid and nbbo.best_ask else 0.0
        spread_bps = (nbbo.best_ask - nbbo.best_bid) / mid * 1e4 if mid else 0.0
        extra: dict[str, float] = {}
        notes: list[str] = []
        new_venues: list[str] = []
        for r in rows:
            vid = r["venue_id"]
            ratio = fill_ratios.get(vid)
            if ratio is None or ratio[1] < MIN_SAMPLES:
                new_venues.append(vid)
                continue
            others = [o["effective_price"] for o in rows if o["venue_id"] != vid and o["effective_price"] > 0]
            gap = 0.0
            if others and r["effective_price"] > 0:
                fallback = min(others) if is_buy else max(others)
                gap = (fallback - r["effective_price"]) / r["effective_price"] * 1e4 * (1 if is_buy else -1)
            penalty = miss_penalty_bps(ratio[0], gap, spread_bps)
            extra[vid] = penalty
            if penalty > 0:
                notes.append(
                    f"Smart: {vid} filled {ratio[0]:.0%} of what we sent it (last {ratio[1]} orders); a miss costs "
                    f"at least {max(gap, spread_bps):.1f} bps (the next venue or one spread) — "
                    f"+{penalty:.2f} bps expected"
                )
            else:
                notes.append(f"Smart: {vid} filled {ratio[0]:.0%} of what we sent it (last {ratio[1]} orders) — no penalty")
        if new_venues:
            notes.append(
                f"Smart: no fill history yet for {', '.join(new_venues)} (needs {MIN_SAMPLES} orders) — "
                "ranked on price and fee only"
            )
        return extra, notes
