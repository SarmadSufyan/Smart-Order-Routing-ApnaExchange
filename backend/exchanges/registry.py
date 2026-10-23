"""Which exchanges exist, which are implemented, and which are enabled.

Every venue was checked against its real testnet before being added
(2026-09-28, see DEVELOPMENT_JOURNAL.md):

    V1 Binance   testnet executes; book tracks the real market (~1 bps)
    V2 Coinbase  NO real testnet (sandbox = mocked responses) -> data-only
    V3 Kraken    NO spot testnet at all (futures demo only)   -> not built
    V4 Bybit     DEMO TRADING executes and tracks the real market (<1 bps);
                 its separate TESTNET does not (BTC ~+170 bps) -> we use demo
    V5 OKX       demo trading executes; tracks the real market (~3 bps)
    V6 Gate      testnet executes; tracks the real market (0-10 bps)
    V7 HTX       NO sandbox at all -> data-only on testnet, trades on real money
                 only (added 2026-10-24 for Phase D: PVARA NOC in Pakistan)
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from backend.exchanges.base import ExchangeAdapter
from backend.shared.utils.logger import get_logger

logger = get_logger("exchange_registry")


@dataclass(frozen=True)
class ExchangeSpec:
    venue_id: str
    name: str
    phase: int  # PLAN.md phase in which the adapter landed
    factory: Callable[..., ExchangeAdapter] | None = None
    note: str = ""


def _ccxt_venue_kwargs(settings, exchange_id: str, venue_id: str, name: str) -> dict:
    return {
        "venue_id": venue_id,
        "name": name,
        "mode": settings.exchange_mode,
        "data_mode": settings.market_data_mode,
        "symbols": settings.symbol_list,
        "credentials": settings.exchange_credentials(exchange_id),
        # Real money only (the check runs on mainnet): strict unless .env says otherwise.
        "require_ip_lock": getattr(settings, "mainnet_require_ip_lock", True),
    }


def _binance(settings) -> ExchangeAdapter:
    from backend.exchanges.binance import BinanceAdapter

    return BinanceAdapter(
        snapshot_depth=settings.orderbook_snapshot_depth,
        **_ccxt_venue_kwargs(settings, "binance", "V1", "Binance"),
    )


def _coinbase(settings) -> ExchangeAdapter:
    from backend.exchanges.coinbase import CoinbaseAdapter

    return CoinbaseAdapter(venue_id="V2", name="Coinbase", mode=settings.exchange_mode, symbols=settings.symbol_list)


def _bybit(settings) -> ExchangeAdapter:
    from backend.exchanges.bybit import BybitAdapter

    return BybitAdapter(**_ccxt_venue_kwargs(settings, "bybit", "V4", "Bybit"))


def _okx(settings) -> ExchangeAdapter:
    from backend.exchanges.okx import OkxAdapter

    return OkxAdapter(**_ccxt_venue_kwargs(settings, "okx", "V5", "OKX"))


def _gate(settings) -> ExchangeAdapter:
    from backend.exchanges.gate import GateAdapter

    return GateAdapter(**_ccxt_venue_kwargs(settings, "gate", "V6", "Gate"))


def _htx(settings) -> ExchangeAdapter:
    from backend.exchanges.htx import HtxAdapter

    return HtxAdapter(**_ccxt_venue_kwargs(settings, "htx", "V7", "HTX"))


EXCHANGE_CATALOG: dict[str, ExchangeSpec] = {
    "binance": ExchangeSpec("V1", "Binance", phase=1, factory=_binance),
    "coinbase": ExchangeSpec("V2", "Coinbase", phase=2, factory=_coinbase, note="data-only: no usable sandbox"),
    "kraken": ExchangeSpec("V3", "Kraken", phase=3, note="no spot sandbox exists (futures only)"),
    "bybit": ExchangeSpec("V4", "Bybit", phase=3, factory=_bybit),
    "okx": ExchangeSpec("V5", "OKX", phase=3, factory=_okx),
    "gate": ExchangeSpec("V6", "Gate", phase=3, factory=_gate),
    "htx": ExchangeSpec("V7", "HTX", phase=8, factory=_htx, note="real money only: no sandbox (data-only on testnet)"),
}


class ExchangeRegistry:
    def __init__(self, adapters: list[ExchangeAdapter]) -> None:
        self._by_venue: dict[str, ExchangeAdapter] = {a.venue_id: a for a in adapters}

    @classmethod
    def from_settings(cls, settings) -> "ExchangeRegistry":
        adapters: list[ExchangeAdapter] = []
        for exchange_id in settings.enabled_exchange_list:
            spec = EXCHANGE_CATALOG.get(exchange_id)
            if spec is None:
                logger.warning("unknown_exchange", exchange=exchange_id)
            elif spec.factory is None:
                logger.warning("exchange_not_available", exchange=exchange_id, reason=spec.note)
            else:
                adapters.append(spec.factory(settings))
        if not adapters:
            logger.error("no_exchanges_enabled", enabled=settings.enabled_exchange_list)
        return cls(adapters)

    def get(self, venue_id: str) -> ExchangeAdapter | None:
        return self._by_venue.get(venue_id)

    def all(self) -> list[ExchangeAdapter]:
        return list(self._by_venue.values())

    async def start_all(self) -> None:
        await asyncio.gather(*(a.start() for a in self.all()))

    async def stop_all(self) -> None:
        await asyncio.gather(*(a.stop() for a in self.all()), return_exceptions=True)

    async def cancel_all_open_orders(self) -> int:
        """Kill-switch sweep across every venue. Never raises."""
        results = await asyncio.gather(
            *(a.cancel_all_open_orders() for a in self.all()), return_exceptions=True
        )
        return sum(r for r in results if isinstance(r, int))
