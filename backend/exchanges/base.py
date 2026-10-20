"""The exchange adapter contract.

Every venue ApnaSOR routes to implements ``ExchangeAdapter``. Market data,
venue health, routing and the wallet talk ONLY to this interface, so adding
Coinbase / Kraken / Bybit / OKX means writing one adapter — no service code
changes.
"""

import asyncio
import random
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from backend.exchanges.orderbook import LocalOrderBook
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import OrderSide, OrderType


class SymbolRules(BaseModel):
    """Exchange trading rules for one symbol (from the venue's market info)."""

    symbol: str  # unified, e.g. "BTC/USDT"
    venue_symbol: str  # exchange-native, e.g. "BTCUSDT"
    base: str
    quote: str
    amount_step: float
    min_amount: float
    price_tick: float
    min_notional: float
    # Some venues reject limit prices too far from their reference price
    # (Bybit: priceLimitRatioX = 0.5%). The engine never sets its price
    # protection band wider than this.
    max_limit_deviation_bps: float | None = None


class TickerStats(BaseModel):
    last_price: float
    volume_base: float = 0.0  # rolling 24h
    volume_quote: float = 0.0


class ExecutionResult(BaseModel):
    """What happened to one child order at one venue."""

    exec_type: ExecType
    requested_qty: float
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0
    fee_asset: str | None = None
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    exchange_status: str | None = None
    latency_ms: float = 0.0
    reason: str | None = None
    # True only when the VENUE misbehaved (timeout, exchange error). Client
    # mistakes — below min lot, insufficient balance, missing keys — are not
    # the venue's fault and must not lower its health score.
    venue_fault: bool = False
    # The venue refused the order before executing any of it (bad key, no
    # permission, balance, exchange error, or a lost request confirmed "not
    # executed"), so the router may send that quantity to another venue. An
    # IOC that EXPIRED at its limit is a price decision, not a refusal.
    retry_elsewhere: bool = False
    # The refusal is about our API key's right to trade, not this order: every
    # order to this venue would fail the same way, so stop routing there.
    blocks_trading: bool = False


class AssetBalance(BaseModel):
    asset: str
    free: float = 0.0
    used: float = 0.0
    total: float = 0.0


class BalanceSnapshot(BaseModel):
    assets: list[AssetBalance]
    other_nonzero_assets: int = 0
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedStatus(BaseModel):
    connected: bool = False
    last_message_age_ms: float | None = None
    books_synced: int = 0
    books_total: int = 0
    resyncs: int = 0
    reconnects: int = 0


class Candle(BaseModel):
    """One OHLCV candle of a venue's REAL market (public data, never a testnet book)."""

    ts_ms: int  # candle start
    open: float
    high: float
    low: float
    close: float
    volume: float  # base asset


class ExchangeAdapter(ABC):
    """Base class for all venue adapters."""

    exchange_id: str = ""

    def __init__(self, *, venue_id: str, name: str, mode: str, data_mode: str | None = None) -> None:
        self.venue_id = venue_id
        self.name = name
        self.mode = mode  # EXECUTION mode: where orders go ("testnet" | "mainnet")
        self.data_mode = data_mode or mode  # where the order book comes from
        self._degraded = False

    # ── capabilities ────────────────────────────────────────────────
    # A venue can stream real market data without being able to execute
    # (e.g. Coinbase has no real testnet). Such a venue still counts in the
    # NBBO, but the router must never send it an order.
    @property
    def execution_available(self) -> bool:
        return True

    @property
    def execution_note(self) -> str | None:
        """Why this venue can't trade right now, or None if it can."""
        if not self.execution_available:
            return f"{self.name}: execution not available"
        if not self.has_credentials:
            return f"no {self.name} {self.display_environment} API keys configured"
        return None

    @property
    def can_trade(self) -> bool:
        return self.execution_available and self.has_credentials

    @property
    def key_note(self) -> str | None:
        """Real money: what the exchange says our API key may do (None where it isn't checked)."""
        return None

    @property
    def account_taker_fee_bps(self) -> float | None:
        """Real money: the taker fee the exchange reports for our account (None = not read; the public rate applies)."""
        return None

    @property
    def fee_note(self) -> str | None:
        """Real money: the account's own fee, in one line for the venue page."""
        return None

    # ── lifecycle ───────────────────────────────────────────────────
    @abstractmethod
    async def start(self) -> None:
        """Begin streaming market data. Must never raise — retry internally."""

    @abstractmethod
    async def stop(self) -> None: ...

    # ── market data ─────────────────────────────────────────────────
    @property
    @abstractmethod
    def symbols(self) -> list[str]: ...

    @abstractmethod
    def get_book(self, symbol: str) -> LocalOrderBook | None: ...

    @abstractmethod
    def get_ticker(self, symbol: str) -> TickerStats | None: ...

    @abstractmethod
    def rules(self, symbol: str) -> SymbolRules | None: ...

    @abstractmethod
    def feed_status(self) -> FeedStatus: ...

    # ── trading ─────────────────────────────────────────────────────
    @abstractmethod
    async def place_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType,
        client_order_id: str,
        limit_price: float | None = None,
        reference_price: float | None = None,
    ) -> ExecutionResult:
        """Send one child order. Never raises — failures become REJECT results."""

    @abstractmethod
    async def cancel_all_open_orders(self) -> int:
        """Cancel every open order on the venue (kill-switch sweep)."""

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1m", since_ms: int | None = None, limit: int = 1000,
    ) -> list[Candle]:
        """Historical candles of the venue's real market (public data). Empty where unsupported."""
        return []

    async def execution_tops(self, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
        """Best bid/ask on the EXECUTION venue per symbol (for coherence checks).
        Venues that can't execute have nothing to report."""
        return {}

    # ── account ─────────────────────────────────────────────────────
    @abstractmethod
    async def fetch_balances(self, assets: set[str]) -> BalanceSnapshot: ...

    @property
    @abstractmethod
    def has_credentials(self) -> bool: ...

    # ── health ──────────────────────────────────────────────────────
    @abstractmethod
    async def ping(self) -> float:
        """REST round-trip time in milliseconds. Raises on failure."""

    @property
    @abstractmethod
    def api_host(self) -> str: ...

    @property
    def environment_name(self) -> str:
        """The exchange's own name for where orders go: testnet / demo / mainnet."""
        return self.mode

    @property
    def display_environment(self) -> str:
        """How the execution environment is named on screen: the exchange's sandbox, or mainnet."""
        return "sandbox" if self.mode == "testnet" else self.mode

    @property
    def display_label(self) -> str:
        execution = self.display_environment if self.execution_available else "no execution"
        data = "live market" if self.data_mode == "mainnet" else self.data_mode
        return f"data: {data} · exec: {execution}"

    # ── demo control ────────────────────────────────────────────────
    # A real exchange can't be forced into a degraded state, so for demos
    # we degrade OUR connection to it: every REST call gets extra latency,
    # which the health monitor then measures and scores honestly.
    @property
    def degraded(self) -> bool:
        return self._degraded

    def set_degraded(self, on: bool) -> None:
        self._degraded = on

    async def _degrade_delay(self) -> None:
        if self._degraded:
            await asyncio.sleep(random.uniform(0.35, 0.65))
