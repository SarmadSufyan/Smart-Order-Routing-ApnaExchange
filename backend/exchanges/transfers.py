"""Can money leave an exchange? What each exchange itself says about deposits and withdrawals, per coin.

An order book can't answer this, and that is the danger: when withdrawals stop, arbitrage can't close the gap to
the other exchanges, so the exchange in trouble can show the best price on the screen and a router that chases best
price walks straight into it. The incident guard (``services/venue_monitor/incident_guard.py``) reads this instead.

A coin counts as closed only when NONE of its networks is open: one network under maintenance is an ordinary day.
Measured 2026-10-30, a day after the Binance incident (our 20 coins + USDT):

    Binance  withdrawals closed on 21 of 21 (990 of 990 on its whole list), deposits open
    Gate     1 of 21 (TRX)      HTX  3 of 21 (SUI, HBAR, ARB)     <- what a normal day looks like

Where each answer comes from:

  Binance  public list on its website (GET /bapi/capital/v1/public/capital/getNetworkCoinAll, not a documented API,
           so it may change); with a real-money key, the documented GET /sapi/v1/capital/config/getall
           (same fields: coin, networkList[].withdrawEnable / depositEnable)
  Gate     public GET /api/v4/spot/currencies     chains[].withdraw_disabled / deposit_disabled
  HTX      public GET /v2/reference/currencies    chains[].withdrawStatus / depositStatus == "allowed"
  Bybit    real-money key only, GET /v5/asset/coin/query-info   chains[].chainWithdraw / chainDeposit == "1"
  OKX      real-money key only, GET /api/v5/asset/currencies    one row per chain: ccy, canWd, canDep

The lists are read directly and parsed in a worker thread, not through ccxt's ``fetch_currencies``: measured, ccxt
held the event loop for ~250 ms on Gate's and HTX's lists (every order and book update waits for that), this ~20 ms.

``None`` means "this exchange doesn't say here", and an unreadable list proves nothing: neither is ever treated as
an incident, and neither as an all-clear.
"""

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from backend.shared.utils.logger import get_logger

logger = get_logger("exchange_transfers")

TIMEOUT_S = 20.0
HEADERS = {"User-Agent": "ApnaSOR", "Accept": "application/json"}


@dataclass(frozen=True)
class CoinTransfers:
    coin: str
    withdraw_open: bool
    deposit_open: bool


@dataclass(frozen=True)
class TransferStatus:
    coins: tuple[CoinTransfers, ...]
    source: str = ""

    @property
    def withdrawals_closed(self) -> list[str]:
        return sorted(c.coin for c in self.coins if not c.withdraw_open)

    @property
    def deposits_closed(self) -> list[str]:
        return sorted(c.coin for c in self.coins if not c.deposit_open)


def _row(coin: str, chains: Iterable[Any], withdraw: Callable[[dict], bool],
         deposit: Callable[[dict], bool]) -> CoinTransfers | None:
    """One coin from its networks: open if ANY network is open. No networks = the answer doesn't say."""
    usable = [c for c in chains if isinstance(c, dict)]
    if not usable:
        return None
    return CoinTransfers(coin, any(withdraw(c) for c in usable), any(deposit(c) for c in usable))


def _rows(raw: Any) -> list[dict]:
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def parse_binance(raw: Any, coins: set[str]) -> list[CoinTransfers]:
    """Binance's coin list: the website wraps it in ``data``, the signed endpoint answers the list itself."""
    rows = _rows(raw.get("data") if isinstance(raw, dict) else raw)
    found = (_row(str(r.get("coin")), r.get("networkList") or [], lambda c: bool(c.get("withdrawEnable")),
                  lambda c: bool(c.get("depositEnable"))) for r in rows if r.get("coin") in coins)
    return [c for c in found if c]


def parse_gate(raw: Any, coins: set[str]) -> list[CoinTransfers]:
    """Gate ``GET /spot/currencies``. A coin without a ``chains`` list stands for its one chain."""
    found = (_row(str(r.get("currency")), r.get("chains") or [r], lambda c: c.get("withdraw_disabled") is False,
                  lambda c: c.get("deposit_disabled") is False) for r in _rows(raw) if r.get("currency") in coins)
    return [c for c in found if c]


def parse_htx(raw: Any, coins: set[str]) -> list[CoinTransfers]:
    """HTX ``GET /v2/reference/currencies``: lower-case names, ``data`` inside."""
    rows = _rows(raw.get("data") if isinstance(raw, dict) else None)
    found = (_row(str(r.get("currency")).upper(), r.get("chains") or [],
                  lambda c: c.get("withdrawStatus") == "allowed", lambda c: c.get("depositStatus") == "allowed")
             for r in rows if str(r.get("currency")).upper() in coins)
    return [c for c in found if c]


def parse_bybit(raw: Any, coins: set[str]) -> list[CoinTransfers]:
    """Bybit ``GET /v5/asset/coin/query-info`` (the whole answer, ``result.rows`` inside)."""
    result = raw.get("result") if isinstance(raw, dict) else None
    rows = _rows(result.get("rows") if isinstance(result, dict) else None)
    found = (_row(str(r.get("coin")), r.get("chains") or [], lambda c: str(c.get("chainWithdraw")) == "1",
                  lambda c: str(c.get("chainDeposit")) == "1") for r in rows if r.get("coin") in coins)
    return [c for c in found if c]


def parse_okx(raw: Any, coins: set[str]) -> list[CoinTransfers]:
    """OKX ``GET /api/v5/asset/currencies``: one row per coin AND chain, so they are grouped first."""
    by_coin: dict[str, list[dict]] = {}
    for row in _rows(raw.get("data") if isinstance(raw, dict) else None):
        if row.get("ccy") in coins:
            by_coin.setdefault(str(row["ccy"]), []).append(row)
    found = (_row(coin, chains, lambda c: c.get("canWd") is True, lambda c: c.get("canDep") is True)
             for coin, chains in by_coin.items())
    return [c for c in found if c]


Parser = Callable[[Any, set[str]], list[CoinTransfers]]
PUBLIC_LISTS: dict[str, tuple[str, Parser]] = {
    "binance": ("https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll", parse_binance),
    "gate": ("https://api.gateio.ws/api/v4/spot/currencies", parse_gate),
    "htx": ("https://api.huobi.pro/v2/reference/currencies", parse_htx),
}
SIGNED_LISTS: dict[str, tuple[str, Parser]] = {
    "binance": ("sapi_get_capital_config_getall", parse_binance),
    "bybit": ("private_get_v5_asset_coin_query_info", parse_bybit),
    "okx": ("private_get_asset_currencies", parse_okx),
}


async def _read_public(url: str, transport: httpx.AsyncBaseTransport | None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=HEADERS, transport=transport) as http:
        resp = await http.get(url)
    resp.raise_for_status()
    return await asyncio.to_thread(json.loads, resp.content)  # megabytes of JSON: not on the event loop


async def fetch_transfer_status(exchange_id: str, coins: Iterable[str], client: Any = None,
                                transport: httpx.AsyncBaseTransport | None = None) -> TransferStatus | None:
    """What this exchange says right now (read-only). ``client`` = the account's ccxt client on real money.

    Returns:
        The status, or None when the exchange doesn't publish it here (Bybit and OKX need a real-money key, which
        development machines and the lab server never have).

    Raises:
        Whatever the read raises, if no source answered. The caller keeps its last answer.
    """
    wanted, failure = set(coins), None
    if client is not None and exchange_id in SIGNED_LISTS:
        method, parser = SIGNED_LISTS[exchange_id]
        try:
            raw = await getattr(client, method)()
            if parsed := await asyncio.to_thread(parser, raw, wanted):
                return TransferStatus(tuple(parsed), "the exchange's account API")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the public list may still answer
            failure = exc
            logger.warning("transfer_status_signed_failed", exchange=exchange_id, error=type(exc).__name__)
    if exchange_id in PUBLIC_LISTS:
        url, parser = PUBLIC_LISTS[exchange_id]
        parsed = await asyncio.to_thread(parser, await _read_public(url, transport), wanted)
        return TransferStatus(tuple(parsed), "the exchange's public coin list") if parsed else None
    if failure is not None:
        raise failure
    return None
