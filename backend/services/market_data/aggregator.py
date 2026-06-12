from datetime import UTC, datetime

from backend.shared.events.event_bus import event_bus
from backend.shared.models.market_data import NBBO, VenueQuote
from backend.shared.utils.logger import get_logger

logger = get_logger("market_data_aggregator")


class MarketDataAggregator:
    def __init__(self, collector, venue_monitor) -> None:
        self._collector = collector
        self._venue_monitor = venue_monitor
        self._nbbo_cache: dict[str, NBBO] = {}

    def get_nbbo(self, symbol: str) -> NBBO | None:
        return self._nbbo_cache.get(symbol)

    def get_all_nbbo(self) -> dict[str, NBBO]:
        return dict(self._nbbo_cache)

    async def compute_nbbo(self, symbol: str) -> NBBO | None:
        routable = set(self._venue_monitor.get_routable_venues())
        all_quotes = self._collector.get_all_quotes_for_symbol(symbol)

        eligible: dict[str, VenueQuote] = {
            vid: q for vid, q in all_quotes.items() if vid in routable
        }

        if not eligible:
            return self._nbbo_cache.get(symbol)

        best_bid = -1.0
        best_bid_venue = ""
        best_bid_size = 0.0
        best_ask = float("inf")
        best_ask_venue = ""
        best_ask_size = 0.0

        for vid, quote in eligible.items():
            if quote.bid_price > best_bid:
                best_bid = quote.bid_price
                best_bid_venue = vid
                best_bid_size = quote.bid_size
            if 0 < quote.ask_price < best_ask:
                best_ask = quote.ask_price
                best_ask_venue = vid
                best_ask_size = quote.ask_size

        if best_bid <= 0 or best_ask == float("inf"):
            return self._nbbo_cache.get(symbol)

        nbbo = NBBO(
            symbol=symbol,
            best_bid=best_bid,
            best_bid_venue=best_bid_venue,
            best_bid_size=best_bid_size,
            best_ask=best_ask,
            best_ask_venue=best_ask_venue,
            best_ask_size=best_ask_size,
            spread=round(best_ask - best_bid, 6),
            timestamp=datetime.now(UTC),
            venue_quotes=eligible,
        )

        self._nbbo_cache[symbol] = nbbo
        await event_bus.publish("nbbo_update", nbbo.model_dump(mode="json"))
        return nbbo
