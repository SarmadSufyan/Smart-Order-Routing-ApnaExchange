import asyncio
import time
from datetime import UTC, datetime

import httpx

from backend.gateway.config import VENUE_CONFIGS, settings
from backend.shared.events.event_bus import event_bus
from backend.shared.models.market_data import DEFAULT_SYMBOLS, VenueQuote
from backend.shared.utils.logger import get_logger

logger = get_logger("market_data_collector")


class MarketDataCollector:
    def __init__(self) -> None:
        self._quotes: dict[str, dict[str, VenueQuote]] = {}
        self._running = False
        self._http_client: httpx.AsyncClient | None = None
        self._venue_urls: dict[str, str] = settings.get_venue_urls()
        self._symbols = [s.symbol for s in DEFAULT_SYMBOLS]

    def get_venue_quote(self, venue_id: str, symbol: str) -> VenueQuote | None:
        return self._quotes.get(venue_id, {}).get(symbol)

    def get_all_quotes_for_symbol(self, symbol: str) -> dict[str, VenueQuote]:
        result = {}
        for vid, sym_quotes in self._quotes.items():
            if symbol in sym_quotes and not sym_quotes[symbol].is_stale:
                result[vid] = sym_quotes[symbol]
        return result

    def get_all_quotes(self) -> dict[str, dict[str, VenueQuote]]:
        return self._quotes

    async def _fetch_venue_quotes(self, venue_id: str) -> None:
        url = self._venue_urls.get(venue_id)
        if not url:
            return

        for symbol in self._symbols:
            try:
                resp = await self._http_client.get(
                    f"{url}/quote", params={"symbol": symbol}, timeout=3.0
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                quote = VenueQuote(
                    venue_id=venue_id,
                    symbol=symbol,
                    bid_price=data.get("bid_price", 0),
                    ask_price=data.get("ask_price", 0),
                    bid_size=data.get("bid_size", 0),
                    ask_size=data.get("ask_size", 0),
                    last_price=data.get("last_price", 0),
                    volume=data.get("volume", 0),
                    timestamp=datetime.now(UTC),
                )
                if venue_id not in self._quotes:
                    self._quotes[venue_id] = {}
                self._quotes[venue_id][symbol] = quote

                await event_bus.publish(
                    "market_data", quote.model_dump(mode="json")
                )
            except Exception:
                if venue_id in self._quotes and symbol in self._quotes.get(venue_id, {}):
                    self._quotes[venue_id][symbol].is_stale = True

    def _mark_stale(self) -> None:
        threshold_ms = settings.data_staleness_threshold_ms
        now = datetime.now(UTC)
        for vid_quotes in self._quotes.values():
            for quote in vid_quotes.values():
                age_ms = (now - quote.timestamp).total_seconds() * 1000
                if age_ms > threshold_ms:
                    quote.is_stale = True

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient()
        logger.info("market_data_collector_started", symbols=self._symbols)

        while self._running:
            tasks = [self._fetch_venue_quotes(vc["venue_id"]) for vc in VENUE_CONFIGS]
            await asyncio.gather(*tasks, return_exceptions=True)
            self._mark_stale()
            await asyncio.sleep(settings.market_data_poll_interval_ms / 1000)

    async def stop(self) -> None:
        self._running = False
        if self._http_client:
            await self._http_client.aclose()
        logger.info("market_data_collector_stopped")
