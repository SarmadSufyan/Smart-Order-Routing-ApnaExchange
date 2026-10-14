"""Coinbase Advanced Trade adapter — MARKET DATA ONLY for now.

Coinbase has no real testnet. The Advanced Trade sandbox returns static,
pre-defined mocked responses (no matching engine), and the institutional
Exchange sandbox lists no USDT pairs. Sending ApnaSOR orders to a mock and
calling the reply a "fill" would be fake execution — exactly what the panel
rejected — so Coinbase contributes its REAL order book to the NBBO but never
receives an order until mainnet execution is built and tested (PLAN.md
Phase 8). The router shows it as a candidate with the reason it was skipped.

Market data: public ``level2`` + ``ticker`` + ``heartbeats`` channels on one
WebSocket (no API key needed), kept correct by ``CoinbaseBookSynchronizer``.
Trading rules and the health ping use Coinbase's public REST API.
"""

import asyncio
import json
import time

import httpx
from websockets.asyncio.client import connect

from backend.exchanges.base import (
    BalanceSnapshot,
    ExchangeAdapter,
    ExecutionResult,
    FeedStatus,
    SymbolRules,
    TickerStats,
)
from backend.exchanges.coinbase.book_sync import CoinbaseBookSynchronizer, SeqResult
from backend.exchanges.orderbook import LocalOrderBook
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import OrderSide, OrderType
from backend.shared.utils.logger import get_logger

logger = get_logger("coinbase_adapter")

WS_URL = "wss://advanced-trade-ws.coinbase.com"
REST_BASE = "https://api.coinbase.com/api/v3/brokerage"
CHANNELS = ("level2", "ticker", "heartbeats")

NO_TESTNET_NOTE = (
    "Coinbase offers no usable sandbox (its sandbox only returns mocked responses) - "
    "market data only until mainnet execution"
)


def to_product_id(symbol: str) -> str:
    return symbol.replace("/", "-")


class CoinbaseAdapter(ExchangeAdapter):
    exchange_id = "coinbase"

    def __init__(self, *, venue_id: str, name: str, mode: str, symbols: list[str]) -> None:
        # Only real-market data exists for Coinbase, whatever the execution mode.
        super().__init__(venue_id=venue_id, name=name, mode=mode, data_mode="mainnet")
        self._requested_symbols = list(symbols)
        self._http: httpx.AsyncClient | None = None
        self._rules: dict[str, SymbolRules] = {}
        self._by_product: dict[str, str] = {}  # "BTC-USDT" -> "BTC/USDT"
        self._books: dict[str, LocalOrderBook] = {s: LocalOrderBook(s) for s in symbols}
        self._sync: CoinbaseBookSynchronizer | None = None
        self._tickers: dict[str, TickerStats] = {}

        self._running = False
        self._connected = False
        self._last_msg_at: float | None = None
        self._reconnects = 0
        self._main_task: asyncio.Task | None = None

    # ── capabilities ────────────────────────────────────────────────
    @property
    def execution_available(self) -> bool:
        return False

    @property
    def execution_note(self) -> str | None:
        return NO_TESTNET_NOTE

    @property
    def has_credentials(self) -> bool:
        return False

    # ── lifecycle ───────────────────────────────────────────────────
    async def start(self) -> None:
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=15.0)
        self._running = True
        self._main_task = asyncio.create_task(self._run(), name=f"{self.venue_id}-coinbase")
        logger.info("adapter_starting", venue_id=self.venue_id, data_mode=self.data_mode,
                    symbols=self._requested_symbols, trading_enabled=False)

    async def stop(self) -> None:
        self._running = False
        if self._main_task:
            self._main_task.cancel()
            await asyncio.gather(self._main_task, return_exceptions=True)
        if self._http:
            await self._http.aclose()
        logger.info("adapter_stopped", venue_id=self.venue_id)

    async def _load_products_with_retry(self) -> None:
        delay = 1.0
        while self._running:
            try:
                resp = await self._http.get("/market/products", params={"product_type": "SPOT", "limit": 1000})
                resp.raise_for_status()
                products = {p["product_id"]: p for p in resp.json().get("products", [])}
                self._build_rules(products)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("load_products_failed", venue_id=self.venue_id, error=str(exc)[:200])
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    def _build_rules(self, products: dict[str, dict]) -> None:
        for symbol in self._requested_symbols:
            product_id = to_product_id(symbol)
            p = products.get(product_id)
            if not p or p.get("status") != "online" or p.get("trading_disabled"):
                logger.warning("symbol_not_listed", venue_id=self.venue_id, symbol=symbol)
                self._books.pop(symbol, None)
                continue
            base, _, quote = symbol.partition("/")
            self._rules[symbol] = SymbolRules(
                symbol=symbol,
                venue_symbol=product_id,
                base=base,
                quote=quote,
                amount_step=float(p["base_increment"]),
                min_amount=float(p["base_min_size"]),
                price_tick=float(p["quote_increment"]),
                min_notional=float(p.get("quote_min_size") or 0.0),
            )
            self._by_product[product_id] = symbol
        self._sync = CoinbaseBookSynchronizer(
            {to_product_id(s): self._books[s] for s in self._rules}
        )

    async def _run(self) -> None:
        await self._load_products_with_retry()
        product_ids = list(self._by_product)
        backoff = 1.0
        while self._running:
            try:
                async with connect(
                    WS_URL, open_timeout=10, ping_interval=20, ping_timeout=20,
                    close_timeout=2, max_size=2**25,
                ) as ws:
                    self._sync.reset()
                    for channel in CHANNELS:
                        await ws.send(json.dumps({"type": "subscribe", "product_ids": product_ids, "channel": channel}))
                    self._connected = True
                    self._last_msg_at = time.monotonic()
                    backoff = 1.0
                    logger.info("ws_connected", venue_id=self.venue_id, products=product_ids)
                    async for raw in ws:
                        self._last_msg_at = time.monotonic()
                        if self._on_message(raw) is SeqResult.GAP:
                            # A lost message could have been any product's
                            # update: no book on this connection is trustworthy.
                            logger.warning("sequence_gap_reconnecting", venue_id=self.venue_id)
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws_disconnected", venue_id=self.venue_id, error=str(exc)[:200])
            finally:
                self._connected = False
                if self._sync:
                    self._sync.reset()
            if not self._running:
                break
            self._reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _on_message(self, raw: str | bytes) -> SeqResult:
        try:
            msg = json.loads(raw)
        except ValueError:
            return SeqResult.OK
        if msg.get("type") == "error":
            logger.warning("ws_error_message", venue_id=self.venue_id, message=str(msg.get("message"))[:200])
            return SeqResult.OK
        if self._sync.check_sequence(msg.get("sequence_num")) is SeqResult.GAP:
            return SeqResult.GAP

        channel = msg.get("channel")
        if channel == "l2_data":
            for event in msg.get("events", []):
                self._sync.on_l2_event(event, msg["sequence_num"])
        elif channel == "ticker":
            for event in msg.get("events", []):
                for t in event.get("tickers", []):
                    symbol = self._by_product.get(t.get("product_id", ""))
                    if symbol and t.get("price"):
                        self._tickers[symbol] = TickerStats(
                            last_price=float(t["price"]),
                            volume_base=float(t.get("volume_24_h") or 0.0),
                        )
        return SeqResult.OK

    # ── market data reads ───────────────────────────────────────────
    @property
    def symbols(self) -> list[str]:
        return list(self._rules)

    def get_book(self, symbol: str) -> LocalOrderBook | None:
        return self._books.get(symbol)

    def get_ticker(self, symbol: str) -> TickerStats | None:
        return self._tickers.get(symbol)

    def rules(self, symbol: str) -> SymbolRules | None:
        return self._rules.get(symbol)

    def feed_status(self) -> FeedStatus:
        age = (time.monotonic() - self._last_msg_at) * 1000 if self._last_msg_at else None
        return FeedStatus(
            connected=self._connected,
            last_message_age_ms=round(age, 1) if age is not None else None,
            books_synced=sum(1 for b in self._books.values() if b.synced),
            books_total=len(self._books),
            resyncs=self._sync.gaps if self._sync else 0,
            reconnects=self._reconnects,
        )

    # ── trading (not available — see module docstring) ──────────────
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
        return ExecutionResult(
            exec_type=ExecType.REJECT,
            requested_qty=quantity,
            client_order_id=client_order_id,
            reason=NO_TESTNET_NOTE,
        )

    async def cancel_all_open_orders(self) -> int:
        return 0

    async def fetch_balances(self, assets: set[str]) -> BalanceSnapshot:
        return BalanceSnapshot(assets=[])

    # ── health ──────────────────────────────────────────────────────
    async def ping(self) -> float:
        started = time.monotonic()
        await self._degrade_delay()
        resp = await self._http.get("/time")
        resp.raise_for_status()
        return (time.monotonic() - started) * 1000

    @property
    def api_host(self) -> str:
        return "api.coinbase.com"
