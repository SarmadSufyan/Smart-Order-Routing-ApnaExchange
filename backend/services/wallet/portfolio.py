"""Unified wallet — real balances across every connected exchange.

Reads balances with each venue's API keys, keeps only the trading universe
(quote currency + base assets of the configured symbols), and values them
in USDT using live mid prices from ApnaSOR's OWN order books.

Refreshes on a timer and immediately after any fill (``request_refresh``),
then publishes a ``wallet_update`` event.

Per-venue balances cap each venue's slice in routing (the funding gate), and
the P&L service reconciles its ledger against them.
"""

import asyncio
from datetime import UTC, datetime

from backend.exchanges.registry import ExchangeRegistry
from backend.shared.events.event_bus import event_bus
from backend.shared.models.market_data import split_symbol
from backend.shared.models.wallet import AssetTotal, VenueWallet, WalletAsset, WalletSnapshot
from backend.shared.utils.logger import get_logger

logger = get_logger("wallet_service")

STABLE_QUOTES = {"USDT"}


class WalletService:
    def __init__(
        self,
        registry: ExchangeRegistry,
        market_data_collector,
        symbols: list[str],
        mode: str,
        refresh_interval_s: float = 15.0,
    ) -> None:
        self._registry = registry
        self._collector = market_data_collector
        self._mode = mode
        self._interval_s = refresh_interval_s
        self._assets: set[str] = set()
        for sym in symbols:
            self._assets.update(split_symbol(sym))
        self._snapshot = WalletSnapshot(mode=mode, tracked_assets=sorted(self._assets))
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._running = False
        self._dirty = True  # balances may have changed since the last refresh

    def get_snapshot(self) -> WalletSnapshot:
        return self._snapshot

    def request_refresh(self) -> None:
        """A fill happened: balances are stale until the next refresh."""
        self._dirty = True
        self._wake.set()

    async def ensure_fresh(self) -> WalletSnapshot:
        """Refresh now if anything traded since the last refresh.

        Routing calls this before splitting an order, so a SELL right after a
        BUY sees the coins where they actually landed.
        """
        if self._dirty or self._snapshot.as_of is None:
            await self.refresh()
        return self._snapshot

    def available(self, venue_id: str, asset: str) -> float | None:
        """Free balance of ``asset`` on a venue, or None if unknown."""
        for venue in self._snapshot.venues:
            if venue.venue_id == venue_id:
                if venue.error:
                    return None
                for a in venue.assets:
                    if a.asset == asset:
                        return a.free
                return 0.0 if asset in self._assets else None
        return None

    def price_usdt(self, asset: str, venue_id: str) -> float | None:
        """Live mid in USDT from our own books: this venue's, else any venue's."""
        if asset in STABLE_QUOTES:
            return 1.0
        symbol = f"{asset}/USDT"
        quote = self._collector.get_venue_quote(venue_id, symbol)
        if quote is None:  # fall back to any venue quoting it
            quote = next(iter(self._collector.get_all_quotes_for_symbol(symbol).values()), None)
        if quote is None or quote.bid_price <= 0 or quote.ask_price <= 0:
            return None
        return (quote.bid_price + quote.ask_price) / 2

    async def refresh(self) -> WalletSnapshot:
        async with self._lock:
            self._dirty = False  # fills arriving during this refresh set it again
            adapters = self._registry.all()
            # Query every venue that has keys — even ones whose key was rejected,
            # so a corrected key is noticed on the next refresh.
            results = await asyncio.gather(
                *(
                    a.fetch_balances(self._assets)
                    if a.has_credentials and a.execution_available
                    else asyncio.sleep(0, result=None)
                    for a in adapters
                ),
                return_exceptions=True,
            )

            venues: list[VenueWallet] = []
            totals: dict[str, AssetTotal] = {}
            for adapter, res in zip(adapters, results, strict=True):
                wallet = VenueWallet(venue_id=adapter.venue_id, name=adapter.name, mode=adapter.mode)
                if res is None:
                    wallet.error = adapter.execution_note or f"no {adapter.mode} API keys configured"
                elif isinstance(res, BaseException):
                    wallet.error = f"balance fetch failed: {str(res)[:160]}"
                    logger.warning("wallet_fetch_failed", venue_id=adapter.venue_id, error=str(res)[:160])
                else:
                    wallet.other_assets_count = res.other_nonzero_assets
                    wallet.updated_at = res.fetched_at
                    for bal in res.assets:
                        price = self.price_usdt(bal.asset, adapter.venue_id)
                        value = bal.total * price if price is not None else None
                        wallet.assets.append(WalletAsset(
                            asset=bal.asset, free=bal.free, used=bal.used, total=bal.total,
                            price_usdt=price, value_usdt=value,
                        ))
                        if value is not None:
                            wallet.total_value_usdt += value
                        agg = totals.setdefault(bal.asset, AssetTotal(asset=bal.asset, value_usdt=0.0))
                        agg.total += bal.total
                        agg.free += bal.free
                        agg.by_venue[adapter.venue_id] = bal.total
                        if value is not None and agg.value_usdt is not None:
                            agg.value_usdt += value
                        elif bal.total > 0:
                            agg.value_usdt = None  # unpriced holding: don't fake a total
                venues.append(wallet)

            self._snapshot = WalletSnapshot(
                as_of=datetime.now(UTC),
                mode=self._mode,
                total_value_usdt=round(sum(v.total_value_usdt for v in venues), 2),
                tracked_assets=sorted(self._assets),
                venues=venues,
                assets=sorted(totals.values(), key=lambda a: -(a.value_usdt or 0)),
            )
            await event_bus.publish(
                "wallet_update",
                {"as_of": self._snapshot.as_of.isoformat(), "total_value_usdt": self._snapshot.total_value_usdt},
            )
            return self._snapshot

    async def start(self) -> None:
        self._running = True
        logger.info("wallet_service_started", tracked_assets=sorted(self._assets))
        while self._running:
            timeout = self._interval_s
            try:
                snap = await self.refresh()
                # Books still syncing at startup -> some assets unpriced -> retry soon.
                if any(a.value_usdt is None and a.total > 0 for a in snap.assets):
                    timeout = 3.0
            except Exception as exc:  # noqa: BLE001
                logger.error("wallet_refresh_failed", error=str(exc)[:200])
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
