from abc import ABC, abstractmethod

from backend.exchanges.orderbook import Level
from backend.shared.models.market_data import NBBO
from backend.shared.models.order import Order
from backend.shared.models.routing import RoutingResult

# venue_id -> {"bids": [(price, qty), ...best first], "asks": [...]}
VenueDepth = dict[str, dict[str, list[Level]]]


class RoutingStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
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
        """Decide how to split ``order`` across venues.

        Args:
            order: Parent order (quantity already normalised to lot size).
            nbbo: Current NBBO; ``venue_quotes`` lists every quoting venue.
            routable_venues: Venues that may receive orders (healthy AND able
                to execute). Other quoting venues are shown but skipped.
            depth: Live book levels per venue. When absent, strategies fall
                back to top-of-book only.
            lot_steps: Per-venue quantity step; allocations are floored to it.
            venue_names: Display names for the human-readable notes.
            exclusion_reasons: Why a quoting venue is not routable (shown in
                the routing proof), e.g. "no real testnet".
            capacity: Per venue, the most base asset it can trade given its
                balance (coins to sell, or USDT/price to buy). Missing = no cap.
            min_sizes: Per venue, its (minimum lot, minimum order value in
                quote). A venue's share must meet both or go elsewhere.
            fees_bps: Per venue, its taker fee in bps. Levels are ranked by
                price plus fee. Missing table = no fees (plain price).
            fill_ratios: Per venue, (share of our recent IOC quantity it
                filled, number of orders measured) - used by smart routing.
        """
