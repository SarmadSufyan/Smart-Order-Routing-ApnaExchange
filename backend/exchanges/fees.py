"""Your account's own trading fee, as the exchange reports it (Phase D, real money).

The router, the bots and the profit numbers start from each exchange's public base-tier taker fee
(``ARB_TAKER_FEES_BPS``). A real account can pay less (a VIP level) or, rarely, more. On real money each adapter
asks its exchange for the account's rate (one read-only call, ccxt ``fetch_trading_fees`` — or
``fetch_trading_fee`` per symbol where only that exists, as on OKX, or where the all-markets call refuses spot, as on
Bybit) and :class:`LiveFees` then answers with that
rate instead of the public one, so routing and the bots judge with the fee you actually pay.

What this can't see: Binance's BNB discount (applied when the fee is charged) — the fill reports, which are
recorded, stay the truth for what was paid.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from ccxt.base.errors import NotSupported


def taker_bps_from_ccxt(raw: Any, symbols: list[str]) -> float | None:
    """The taker fee in bps from a ccxt fee answer (``{symbol: {"taker": 0.001, ...}}``).

    The highest rate among our symbols, so a mixed schedule is never read as cheaper than it is. None when the
    answer carries no usable rate for any of them.
    """
    if not isinstance(raw, dict):
        return None
    rates = []
    for symbol in symbols:
        entry = raw.get(symbol)
        taker = entry.get("taker") if isinstance(entry, dict) else None
        if isinstance(taker, int | float) and not isinstance(taker, bool) and 0 <= taker < 0.05:
            rates.append(float(taker))
    return round(max(rates) * 1e4, 4) if rates else None


async def fetch_account_taker_bps(client: Any, symbols: list[str]) -> float | None:
    """Ask the exchange for this account's taker fee (read-only). Raises whatever the call raises."""
    has = getattr(client, "has", {}) or {}
    if has.get("fetchTradingFees"):
        try:
            return taker_bps_from_ccxt(await client.fetch_trading_fees(), symbols)
        except NotSupported:
            pass  # Bybit answers the all-markets call for derivatives only; its per-symbol call covers spot
    if has.get("fetchTradingFee"):
        return taker_bps_from_ccxt({s: await client.fetch_trading_fee(s) for s in symbols}, symbols)
    return None


def account_fee_note(account_bps: float | None, exchange: str) -> str | None:
    """One line for the venue page."""
    if account_bps is None:
        return None
    return f"Your {exchange} account's taker fee: {account_bps / 100:.3f}% (read from {exchange}; used for routing and bots)"


class LiveFees(Mapping[str, float]):
    """Taker fee in bps per venue id: the account's own rate where the exchange reported one, else the public table.

    One shared, read-only view: the router, the bots and the lab hold it and see an account rate as soon as an
    adapter has read it — no restart, no copies going stale.
    """

    def __init__(self, public_bps: Mapping[str, float], account_bps: Callable[[str], float | None]) -> None:
        self._public = dict(public_bps)
        self._account = account_bps

    def __getitem__(self, venue_id: str) -> float:
        own = self._account(venue_id)
        if own is not None:
            return own
        return self._public[venue_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._public)

    def __len__(self) -> int:
        return len(self._public)

    @property
    def public(self) -> dict[str, float]:
        return dict(self._public)
