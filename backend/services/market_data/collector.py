"""Turns each adapter's live order book into per-venue top-of-book quotes.

The adapters maintain full L2 books from their WebSocket feeds; this service
samples the top of every book on a fixed cadence, stores the latest
``VenueQuote`` per (venue, symbol), and publishes a ``market_data`` event
only when the quote actually changed.

Staleness is judged on the FEED, not the price: a quiet market with no book
changes is still live data. A quote is stale only if its venue's connection
is down, the book is re-syncing, or no message has arrived for
``feed_staleness_threshold_ms``.
"""

import asyncio
from datetime import UTC, datetime

from backend.exchanges.orderbook import Level
from backend.exchanges.registry import ExchangeRegistry
from backend.shared.events.event_bus import event_bus
from backend.shared.models.market_data import VenueQuote
from backend.shared.utils.logger import get_logger

logger = get_logger("market_data_collector")


class MarketDataCollector:
    def __init__(
        self,
        registry: ExchangeRegistry,
        symbols: list[str],
        publish_interval_ms: int = 250,
        staleness_threshold_ms: int = 10_000,
    ) -> None:
        self._registry = registry
        self._symbols = list(symbols)
        self._interval_s = publish_interval_ms / 1000
        self._staleness_ms = staleness_threshold_ms
        self._quotes: dict[str, dict[str, VenueQuote]] = {}
        self._last_sent: dict[tuple[str, str], tuple] = {}
        self._running = False

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    # ── reads used by the aggregator, routing and wallet ────────────
    def get_venue_quote(self, venue_id: str, symbol: str) -> VenueQuote | None:
        return self._quotes.get(venue_id, {}).get(symbol)

    def get_all_quotes_for_symbol(self, symbol: str) -> dict[str, VenueQuote]:
        return {
            vid: quotes[symbol]
            for vid, quotes in self._quotes.items()
            if symbol in quotes and not quotes[symbol].is_stale
        }

    def get_all_quotes(self) -> dict[str, dict[str, VenueQuote]]:
        return self._quotes

    def get_depth(
        self, venue_id: str, symbol: str, levels: int
    ) -> dict[str, list[Level]] | None:
        """Top ``levels`` of a venue's live book, or None if not synced."""
        adapter = self._registry.get(venue_id)
        book = adapter.get_book(symbol) if adapter else None
        if book is None or not book.synced:
            return None
        return {"bids": book.top_bids(levels), "asks": book.top_asks(levels)}

    # ── sampling loop ───────────────────────────────────────────────
    def _sample(self) -> list[VenueQuote]:
        now = datetime.now(UTC)
        changed: list[VenueQuote] = []
        for adapter in self._registry.all():
            feed = adapter.feed_status()
            feed_dead = (
                not feed.connected
                or feed.last_message_age_ms is None
                or feed.last_message_age_ms > self._staleness_ms
            )
            venue_quotes = self._quotes.setdefault(adapter.venue_id, {})
            for symbol in self._symbols:
                book = adapter.get_book(symbol)
                best_bid = book.best_bid() if book and book.synced else None
                best_ask = book.best_ask() if book and book.synced else None
                if best_bid is None or best_ask is None:
                    if symbol in venue_quotes and not venue_quotes[symbol].is_stale:
                        venue_quotes[symbol].is_stale = True
                        changed.append(venue_quotes[symbol])
                    continue

                ticker = adapter.get_ticker(symbol)
                mid = (best_bid[0] + best_ask[0]) / 2
                quote = VenueQuote(
                    venue_id=adapter.venue_id,
                    symbol=symbol,
                    bid_price=best_bid[0],
                    bid_size=best_bid[1],
                    ask_price=best_ask[0],
                    ask_size=best_ask[1],
                    last_price=ticker.last_price if ticker else mid,
                    volume=ticker.volume_base if ticker else 0.0,
                    timestamp=now,
                    is_stale=feed_dead,
                )
                venue_quotes[symbol] = quote

                key = (adapter.venue_id, symbol)
                fingerprint = (quote.bid_price, quote.bid_size, quote.ask_price, quote.ask_size, quote.is_stale)
                if self._last_sent.get(key) != fingerprint:
                    self._last_sent[key] = fingerprint
                    changed.append(quote)
        return changed

    async def start(self) -> None:
        self._running = True
        logger.info("market_data_collector_started", symbols=self._symbols)
        while self._running:
            try:
                for quote in self._sample():
                    await event_bus.publish("market_data", quote.model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001 — keep sampling no matter what
                logger.error("market_data_sample_failed", error=str(exc))
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        self._running = False
        logger.info("market_data_collector_stopped")
