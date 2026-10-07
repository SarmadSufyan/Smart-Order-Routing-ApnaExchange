"""Shared core for every venue we can EXECUTE on (via ccxt).

Binance, Bybit, OKX and Gate trade identically from ApnaSOR's point of view:
IOC child orders, lot/notional validation, idempotent client order ids,
reconciliation after network errors, fee parsing, balances. Only the
market-data feed differs, so a venue subclass implements ``_run_feed()``
(its WebSocket protocol + book synchronisation) and inherits the rest.

Two ccxt clients may exist: the EXECUTION client (``mode``: testnet/demo or
mainnet, carries the API keys) and a public DATA client (``data_mode``),
never given keys.
"""

import asyncio
import re
import time
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import ccxt.async_support as ccxt_async
from ccxt.base.errors import (
    AuthenticationError,
    ExchangeError,
    InsufficientFunds,
    InvalidOrder,
    NetworkError,
    OrderNotFound,
)
from websockets.asyncio.client import ClientConnection, connect

from backend.exchanges.base import (
    AssetBalance,
    BalanceSnapshot,
    Candle,
    ExchangeAdapter,
    ExecutionResult,
    FeedStatus,
    SymbolRules,
    TickerStats,
)
from backend.exchanges.orderbook import LocalOrderBook
from backend.exchanges.precision import floor_to_step, is_close
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import OrderSide, OrderType
from backend.shared.utils.logger import get_logger

logger = get_logger("ccxt_venue")

FINAL_STATUSES = {"closed", "canceled", "expired", "rejected"}
_MSG_RE = re.compile(r'"(?:msg|message|ret_msg|sMsg)"\s*:\s*"([^"]+)"')
# Venues word "your key may not trade" in their own way, often with codes ccxt
# doesn't map yet — found live: OKX 50124 "This API Key does not have trading
# permission for the market" arrives as a plain ExchangeError.
_PERMISSION_RE = re.compile(r"permission|not allowed to trade|trading (?:is )?(?:not allowed|disabled)", re.I)


def venue_client_id(child_order_id: str) -> str:
    """Client order id every venue accepts: 27 alphanumeric chars.

    Limits: Binance <=36 [A-Za-z0-9._:/-], Bybit <=36, OKX <=32 alphanumeric,
    Gate <=28 after its mandatory ``t-`` prefix. The first 24 hex chars of the
    child UUID (96 bits) keep it unique for our purposes.
    """
    return "sor" + child_order_id.replace("-", "")[:24]


def short_error(exc: Exception) -> str:
    """Pull the venue's human message out of a ccxt exception string."""
    text = str(exc)
    match = _MSG_RE.search(text)
    return match.group(1) if match else text[:160]


def is_permission_error(exc: Exception) -> bool:
    """The venue refuses our API key's right to trade — not this particular order."""
    return isinstance(exc, AuthenticationError) or bool(_PERMISSION_RE.search(short_error(exc)))


def _step(value: Any, precision_mode: int) -> float:
    """ccxt precision -> step size, whichever precision mode the venue uses."""
    if precision_mode == ccxt_async.DECIMAL_PLACES:
        return 10 ** -int(value)
    return float(value)


class CcxtVenueAdapter(ExchangeAdapter):
    ccxt_id: str = ""
    ccxt_options: dict[str, Any] = {}
    # How this venue's non-real-money environment is reached in ccxt:
    #   "sandbox" -> set_sandbox_mode (Binance/Gate testnet, OKX demo header)
    #   "demo"    -> enable_demo_trading (Bybit Demo Trading, which tracks
    #                the real market; Bybit's testnet does not)
    test_environment: str = "sandbox"
    test_environment_name: str = "testnet"  # what the exchange calls it (OKX/Bybit: "demo")

    @property
    def environment_name(self) -> str:
        return self.test_environment_name if self.mode == "testnet" else self.mode

    def __init__(
        self,
        *,
        venue_id: str,
        name: str,
        mode: str,
        symbols: list[str],
        credentials: dict[str, str] | None,
        data_mode: str | None = None,
    ) -> None:
        super().__init__(venue_id=venue_id, name=name, mode=mode, data_mode=data_mode)
        self._requested_symbols = list(symbols)
        self._credentials = credentials
        self._client: Any = None  # execution + account (has keys)
        self._data_client: Any = None  # public market data (never has keys)
        self._rules: dict[str, SymbolRules] = {}
        self._by_venue_symbol: dict[str, str] = {}  # venue id ("BTCUSDT") -> "BTC/USDT"
        self._books: dict[str, LocalOrderBook] = {s: LocalOrderBook(s) for s in symbols}
        self._tickers: dict[str, TickerStats] = {}
        self._running = False
        self._connected = False
        self._last_msg_at: float | None = None
        self._reconnects = 0
        self._main_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        # Set when the venue rejects our API key; the venue stops being
        # tradable until an authenticated call succeeds again.
        self._auth_error: str | None = None
        # The public data client loads its markets on first use (Gate's list takes
        # ~15 s): shared and shielded, so a caller's timeout never restarts it.
        self._history_markets: asyncio.Task | None = None

    # ── credentials health ──────────────────────────────────────────
    # Keys that are present but WRONG (bad secret, wrong environment) must
    # not make a venue look tradable — found live: the router sent a child to
    # a venue whose key was being rejected.
    @property
    def can_trade(self) -> bool:
        return super().can_trade and self._auth_error is None

    @property
    def execution_note(self) -> str | None:
        if self._auth_error:
            return f"{self.name} rejected the API key: {self._auth_error}"
        return super().execution_note

    def _record_auth(self, error: str | None) -> None:
        if error != self._auth_error:
            if error:
                logger.error("api_key_rejected", venue_id=self.venue_id, error=error)
            else:
                logger.info("api_key_accepted", venue_id=self.venue_id)
        self._auth_error = error

    async def _verify_credentials(self) -> None:
        if not self.has_credentials:
            return
        try:
            await self._client.fetch_balance()
            self._record_auth(None)
        except AuthenticationError as exc:
            self._record_auth(short_error(exc))
        except Exception as exc:  # noqa: BLE001 — network trouble says nothing about the key
            logger.warning("credential_check_failed", venue_id=self.venue_id, error=str(exc)[:160])

    # ── lifecycle ───────────────────────────────────────────────────
    def _make_client(self, *, sandbox: bool, with_keys: bool) -> Any:
        config: dict[str, Any] = {"enableRateLimit": True, "options": dict(self.ccxt_options)}
        if with_keys and self._credentials:
            config.update(self._credentials)
        client = getattr(ccxt_async, self.ccxt_id)(config)
        if sandbox:
            if self.test_environment == "demo":
                client.enable_demo_trading(True)
            else:
                client.set_sandbox_mode(True)
        return client

    async def start(self) -> None:
        self._client = self._make_client(sandbox=self.mode == "testnet", with_keys=True)
        if self.data_mode == self.mode:
            self._data_client = self._client
        else:
            self._data_client = self._make_client(sandbox=self.data_mode == "testnet", with_keys=False)
        self._running = True
        self._main_task = asyncio.create_task(self._run(), name=f"{self.venue_id}-{self.exchange_id}")
        logger.info(
            "adapter_starting",
            venue_id=self.venue_id,
            exchange=self.exchange_id,
            mode=self.mode,
            data_mode=self.data_mode,
            symbols=self._requested_symbols,
            trading_enabled=self.has_credentials,
        )

    async def stop(self) -> None:
        self._running = False
        tasks = [t for t in [self._main_task, *self._tasks] if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in {id(c): c for c in (self._client, self._data_client) if c}.values():
            await client.close()
        logger.info("adapter_stopped", venue_id=self.venue_id)

    def _spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self) -> None:
        await self._load_markets_with_retry()
        await self._verify_credentials()
        await self._run_feed()

    @abstractmethod
    async def _run_feed(self) -> None:
        """Stream market data forever (reconnecting), keeping books synced."""

    async def _load_markets_with_retry(self) -> None:
        delay = 1.0
        while self._running:
            try:
                await self._client.load_markets()
                self._build_rules()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("load_markets_failed", venue_id=self.venue_id, error=str(exc)[:200])
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    def _build_rules(self) -> None:
        mode = self._client.precisionMode
        for symbol in self._requested_symbols:
            market = self._client.markets.get(symbol)
            if not market or not market.get("spot") or market.get("active") is False:
                logger.warning("symbol_not_listed", venue_id=self.venue_id, symbol=symbol)
                self._books.pop(symbol, None)
                continue
            limits, precision = market["limits"], market["precision"]
            amount_step = _step(precision["amount"], mode)
            self._rules[symbol] = SymbolRules(
                symbol=symbol,
                venue_symbol=market["id"],
                base=market["base"],
                quote=market["quote"],
                amount_step=amount_step,
                min_amount=float((limits.get("amount") or {}).get("min") or amount_step),
                price_tick=_step(precision["price"], mode),
                min_notional=float((limits.get("cost") or {}).get("min") or 0.0),
                max_limit_deviation_bps=self._max_limit_deviation_bps(market),
            )
            self._by_venue_symbol[market["id"]] = symbol
        self._on_rules_built()

    def _on_rules_built(self) -> None:
        """Hook for subclasses that need per-symbol state once symbols are known."""

    def _max_limit_deviation_bps(self, market: dict) -> float | None:
        """Venue's own cap on how far a limit price may sit from its reference
        price, if it publishes one. None = no known cap."""
        return None

    # ── WebSocket plumbing shared by every feed ─────────────────────
    async def _ws_loop(
        self,
        url: str,
        on_open: Callable[[ClientConnection], Awaitable[None]],
        on_message: Callable[[str | bytes], bool],
        app_ping: str | None = None,
        ping_every_s: float = 20.0,
    ) -> None:
        """Connect, subscribe, dispatch; reconnect with backoff forever.

        ``on_message`` returns False when the feed lost integrity (sequence
        gap) and the connection must be rebuilt for fresh snapshots.
        """
        backoff = 1.0
        while self._running:
            pinger: asyncio.Task | None = None
            try:
                async with connect(
                    url, open_timeout=10, ping_interval=20, ping_timeout=20,
                    close_timeout=2, max_size=2**25,
                ) as ws:
                    self._reset_books()
                    self._connected = True
                    self._last_msg_at = time.monotonic()
                    await on_open(ws)
                    backoff = 1.0
                    logger.info("ws_connected", venue_id=self.venue_id)
                    if app_ping:
                        pinger = asyncio.create_task(self._ping_forever(ws, app_ping, ping_every_s))
                    async for raw in ws:
                        self._last_msg_at = time.monotonic()
                        if on_message(raw) is False:
                            logger.warning("feed_integrity_lost_reconnecting", venue_id=self.venue_id)
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — any failure means reconnect
                logger.warning("ws_disconnected", venue_id=self.venue_id, error=str(exc)[:200])
            finally:
                if pinger:
                    pinger.cancel()
                self._connected = False
                self._reset_books()
            if not self._running:
                break
            self._reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    @staticmethod
    async def _ping_forever(ws: ClientConnection, payload: str, every_s: float) -> None:
        while True:
            await asyncio.sleep(every_s)
            await ws.send(payload)

    def _reset_books(self) -> None:
        for book in self._books.values():
            book.invalidate()

    def _gap_count(self) -> int:
        return 0

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
            resyncs=self._gap_count(),
            reconnects=self._reconnects,
        )

    # ── trading ─────────────────────────────────────────────────────
    @property
    def has_credentials(self) -> bool:
        return bool(self._credentials)

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
        cid = venue_client_id(client_order_id)

        # Every path through reject() means nothing was executed, so the router
        # may send the quantity elsewhere.
        def reject(reason: str, latency_ms: float = 0.0, qty: float = quantity, venue_fault: bool = False,
                   blocks_trading: bool = False) -> ExecutionResult:
            return ExecutionResult(
                exec_type=ExecType.REJECT, requested_qty=qty, client_order_id=cid,
                latency_ms=round(latency_ms, 2), reason=reason, venue_fault=venue_fault,
                retry_elsewhere=True, blocks_trading=blocks_trading,
            )

        rules = self._rules.get(symbol)
        if rules is None or self._client is None:
            return reject(f"{symbol} is not tradable on {self.name}")
        if not self.has_credentials:
            return reject(f"no {self.name} {self.environment_name} API keys configured - trading disabled")

        qty = floor_to_step(quantity, rules.amount_step)
        if qty < rules.min_amount:
            return reject(f"quantity {quantity} is below {self.name}'s minimum lot {rules.min_amount}")
        ref = limit_price or reference_price
        if ref and qty * ref < rules.min_notional:
            return reject(
                f"order value ${qty * ref:,.2f} is below {self.name}'s minimum ${rules.min_notional:,.2f}", qty=qty
            )

        params: dict[str, Any] = {"clientOrderId": cid}
        started = time.monotonic()
        await self._degrade_delay()
        try:
            if order_type == OrderType.MARKET:
                order = await self._client.create_order(symbol, "market", side.value.lower(), qty, None, params)
            else:
                params["timeInForce"] = "IOC"
                price = float(self._client.price_to_precision(symbol, limit_price))
                order = await self._client.create_order(symbol, "limit", side.value.lower(), qty, price, params)
            order = await self._await_final(order, symbol)
        except InsufficientFunds as exc:
            return reject(f"insufficient balance on {self.name}: {short_error(exc)}", self._since(started), qty)
        except AuthenticationError as exc:
            self._record_auth(short_error(exc))
            return reject(f"{self.name} rejected the API key: {short_error(exc)}", self._since(started), qty,
                          blocks_trading=True)
        except InvalidOrder as exc:
            return reject(f"{self.name} rejected the order: {short_error(exc)}", self._since(started), qty,
                          blocks_trading=is_permission_error(exc))
        except NetworkError as exc:
            # The request may or may not have reached the matching engine.
            # Never resend blindly — look the order up by our client id.
            logger.warning("order_network_error", venue_id=self.venue_id, client_order_id=cid, error=str(exc)[:200])
            order = await self._reconcile(symbol, cid)
            if order is None:
                return reject(
                    f"network error; order not found on {self.name} (not executed)",
                    self._since(started), qty, venue_fault=True,
                )
        except ExchangeError as exc:
            if is_permission_error(exc):
                # Our key's fault, not the venue's: block routing, keep its health score.
                return reject(f"{self.name} refused this API key's trading permission: {short_error(exc)}",
                              self._since(started), qty, blocks_trading=True)
            return reject(f"{self.name} error: {short_error(exc)}", self._since(started), qty, venue_fault=True)

        result = self._to_result(order, qty, self._since(started), cid)
        logger.info(
            "order_executed",
            venue_id=self.venue_id, symbol=symbol, client_order_id=cid,
            exchange_order_id=result.exchange_order_id, exec_type=result.exec_type.value,
            filled=result.filled_qty, avg_price=result.avg_price, latency_ms=result.latency_ms,
        )
        return result

    async def _await_final(self, order: dict, symbol: str) -> dict:
        """Some venues (Bybit, OKX) acknowledge an order with just its id.
        IOC orders finish immediately, so poll briefly for the final state."""
        if order.get("status") in FINAL_STATUSES and order.get("filled") is not None:
            return order
        order_id = order.get("id")
        if not order_id:
            return order
        for delay in (0.15, 0.3, 0.6, 1.0, 1.5):
            await asyncio.sleep(delay)
            try:
                fetched = await self._fetch_order(order_id, symbol)
            except OrderNotFound:
                continue
            if fetched.get("status") in FINAL_STATUSES:
                return fetched
            order = fetched
        return order

    async def _fetch_order(self, order_id: str, symbol: str) -> dict:
        """Look up one order by exchange id. Overridden where ccxt needs help."""
        return await self._client.fetch_order(order_id, symbol)

    async def _reconcile(self, symbol: str, cid: str) -> dict | None:
        for _ in range(3):
            await asyncio.sleep(0.75)
            try:
                return await self._fetch_by_client_id(symbol, cid)
            except Exception:  # noqa: BLE001 — not found yet, or lookup failed
                continue
        return None

    async def _fetch_by_client_id(self, symbol: str, cid: str) -> dict:
        """Look up one order by OUR client id. Overridden per venue if needed."""
        return await self._client.fetch_order(None, symbol, {"clientOrderId": cid})

    def _to_result(self, order: dict, requested: float, latency_ms: float, cid: str) -> ExecutionResult:
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or 0.0)
        if filled and not avg and order.get("cost"):
            avg = float(order["cost"]) / filled
        status = order.get("status")

        venue_fault = False
        if filled > 0 and (filled >= requested or is_close(filled, requested)):
            exec_type, reason = ExecType.FILL, None
        elif filled > 0:
            exec_type, reason = ExecType.PARTIAL, f"IOC: {requested - filled:g} unfilled"
        else:
            exec_type = ExecType.REJECT
            reason = {
                "expired": "IOC: no liquidity at the limit price",
                "canceled": "IOC: no liquidity at the limit price",
                "rejected": f"rejected by {self.name}",
            }.get(status, f"not filled (status={status})")
            venue_fault = status == "rejected"
        retry_elsewhere = filled <= 0 and status == "rejected"  # refused outright, not expired at a price

        fee, fee_asset = 0.0, None
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if not f or f.get("cost") is None:
                continue
            if fee_asset in (None, f.get("currency")):
                fee_asset = f.get("currency")
                fee += abs(float(f["cost"]))

        return ExecutionResult(
            exec_type=exec_type,
            requested_qty=requested,
            filled_qty=filled,
            avg_price=avg,
            fee=fee,
            fee_asset=fee_asset,
            exchange_order_id=str(order.get("id")) if order.get("id") else None,
            client_order_id=cid,
            # IOC remainders come back "canceled" on some venues, "expired" on
            # others; normalise so the engine can explain price protection.
            exchange_status="expired" if status == "canceled" else status,
            latency_ms=round(latency_ms, 2),
            reason=reason,
            venue_fault=venue_fault,
            retry_elsewhere=retry_elsewhere,
        )

    async def cancel_all_open_orders(self) -> int:
        if not self.has_credentials or not self._rules:
            return 0

        async def sweep(symbol: str) -> int:
            try:
                open_orders = await self._client.fetch_open_orders(symbol)
                for o in open_orders:
                    await self._client.cancel_order(o["id"], symbol)
                return len(open_orders)
            except Exception as exc:  # noqa: BLE001
                logger.error("cancel_all_failed", venue_id=self.venue_id, symbol=symbol, error=str(exc)[:200])
                return 0

        counts = await asyncio.gather(*(sweep(s) for s in self._rules))
        return sum(counts)

    # ── account ─────────────────────────────────────────────────────
    async def fetch_balances(self, assets: set[str]) -> BalanceSnapshot:
        # Runs every wallet refresh, so it doubles as the periodic key check:
        # a fixed key makes the venue tradable again without a restart.
        try:
            raw = await self._client.fetch_balance()
        except AuthenticationError as exc:
            self._record_auth(short_error(exc))
            raise
        self._record_auth(None)
        free, used, total = raw.get("free", {}), raw.get("used", {}), raw.get("total", {})
        tracked = [
            AssetBalance(
                asset=a,
                free=float(free.get(a) or 0.0),
                used=float(used.get(a) or 0.0),
                total=float(total.get(a) or 0.0),
            )
            for a in sorted(assets)
        ]
        others = sum(1 for a, v in total.items() if v and a not in assets)
        return BalanceSnapshot(assets=tracked, other_nonzero_assets=others)

    # ── execution-book probe (for the coherence monitor) ────────────
    async def fetch_candles(
        self, symbol: str, timeframe: str = "1m", since_ms: int | None = None, limit: int = 1000,
    ) -> list[Candle]:
        """Public OHLCV candles from the market-data client (the real market when MARKET_DATA_MODE=mainnet)."""
        client = self._data_client
        if client is None or symbol not in self._requested_symbols:
            return []
        if not client.markets:
            if self._history_markets is None or (self._history_markets.done() and self._history_markets.exception()):
                self._history_markets = asyncio.create_task(client.load_markets(), name=f"{self.venue_id}-history-markets")
            await asyncio.shield(self._history_markets)
        rows = await client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        return [Candle(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                       volume=float(r[5] or 0.0)) for r in rows if r and r[4] is not None]

    async def execution_tops(self, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
        """Best bid/ask on the EXECUTION venue (testnet/demo) per symbol."""
        wanted = [s for s in symbols if s in self._rules]
        if not wanted or self._client is None:
            return {}
        tickers = await self._client.fetch_tickers(wanted)
        return {s: (tickers.get(s, {}).get("bid"), tickers.get(s, {}).get("ask")) for s in wanted}

    # ── health ──────────────────────────────────────────────────────
    async def ping(self) -> float:
        started = time.monotonic()  # before the degrade delay, so it's measured
        await self._degrade_delay()
        await self._client.fetch_time()
        return self._since(started)

    @property
    def api_host(self) -> str:
        """Host of the SPOT execution API (ccxt lists futures endpoints too)."""
        try:
            url: Any = self._client.urls["api"]
            while isinstance(url, dict):
                key = next((k for k in ("public", "spot", "rest") if k in url), next(iter(url)))
                url = url[key]
            return urlparse(url.replace("{hostname}", self._client.hostname or "")).hostname or self.exchange_id
        except Exception:  # noqa: BLE001
            return self.exchange_id

    @staticmethod
    def _since(started: float) -> float:
        return (time.monotonic() - started) * 1000
