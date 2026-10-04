"""Arbitrage detector (ROADMAP 5c): fee-aware, detection only.

Every scan (4 per second by default: the rate our books are sampled at) and
for every symbol, each pair of venues is checked in both directions — buy at
venue B's best ask, sell at venue A's best bid:

    gross edge = (bid_A - ask_B) / ask_B
    net edge   = (bid_A x (1 - fee_A) - ask_B x (1 + fee_B)) / ask_B

The fees are each exchange's base-tier TAKER fee (``ARB_TAKER_FEES_BPS``):
both legs would have to be IOC orders, and an IOC always pays taker.

A direction that stays crossed (gross > 0) over consecutive scans is one
*window*. We record how long it lasted, how big it got and whether it was
ever profitable after fees. Measured live on 2026-10-04: the real-market
books of Binance, Bybit and Gate are crossed by ~1 bps most of the time,
while a taker round trip costs 20+ bps. That measured gap — "windows exist
but are smaller than the fees" — is the honest result.

Detection only: nothing here can place an order (no adapter, router or
OrderEntry is passed in). Trading an edge would need coins and USDT already
waiting on both venues, and both legs would go through ``OrderEntry`` and
pre-trade risk like every other order — never "risk-free".

Honest limits: books are sampled, so part of a crossing can be one
exchange's feed arriving later than another's; sizes are the top level
only; prices are the real market, "fundable" uses our testnet/demo balances.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from backend.exchanges.registry import EXCHANGE_CATALOG
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.models.arbitrage import (
    ArbEdge,
    ArbStatus,
    ArbSymbolStats,
    ArbSymbolStatus,
    ArbWindow,
    ArbWindowList,
)
from backend.shared.models.market_data import VenueQuote, split_symbol
from backend.shared.utils.logger import get_logger

logger = get_logger("arbitrage_detector")

DETECTOR_ACTOR = "arbitrage-detector"
STATS_KEY = "arbitrage_stats"

# (upper bound, label): a value goes in the first bucket it is below.
DURATION_BUCKETS: tuple[tuple[float, str], ...] = (
    (1, "<1 s"), (5, "1-5 s"), (30, "5-30 s"), (300, "30 s-5 min"), (math.inf, "5 min+"),
)
GROSS_BUCKETS: tuple[tuple[float, str], ...] = (
    (1, "0-1 bps"), (2, "1-2 bps"), (5, "2-5 bps"), (10, "5-10 bps"), (20, "10-20 bps"), (math.inf, "20+ bps"),
)


# ── pure logic ──────────────────────────────────────────────────────
def bucket(value: float, buckets: tuple[tuple[float, str], ...]) -> str:
    """Label of the first bucket whose upper bound ``value`` is below."""
    for upper, label in buckets:
        if value < upper:
            return label
    return buckets[-1][1]


def parse_fee_schedule(text: str) -> dict[str, float]:
    """``"binance:10,bybit:10"`` -> ``{"V1": 10.0, "V4": 10.0}`` (taker fee in bps per venue id).

    Accepts exchange names or venue ids (``V1:10``).

    Raises:
        ValueError: an entry can't be read — the gateway refuses to start
            rather than detect with a wrong fee.
    """
    by_name = {name: spec.venue_id for name, spec in EXCHANGE_CATALOG.items()}
    venue_ids = set(by_name.values())
    fees: dict[str, float] = {}
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        name, sep, value = item.partition(":")
        key = name.strip()
        venue = by_name.get(key.lower()) or (key.upper() if key.upper() in venue_ids else None)
        try:
            bps = float(value)
        except ValueError:
            bps = math.nan
        if not sep or venue is None or not 0 <= bps < 1_000:
            raise ValueError(f"ARB_TAKER_FEES_BPS: can't read {item!r} - use exchange:bps, e.g. binance:10")
        fees[venue] = bps
    if not fees:
        raise ValueError("ARB_TAKER_FEES_BPS is empty - give each exchange's taker fee, e.g. binance:10,bybit:10")
    return fees


def fee_for(fees: dict[str, float], venue_id: str) -> float:
    """Taker fee of a venue in bps. One without a configured fee gets the
    highest configured one: an unknown fee must never flatter an edge."""
    return fees.get(venue_id, max(fees.values(), default=0.0))


def edge_between(symbol: str, sell: VenueQuote, buy: VenueQuote, sell_fee_bps: float,
                 buy_fee_bps: float) -> ArbEdge:
    """Buy at ``buy``'s best ask, sell at ``sell``'s best bid, both legs paying taker."""
    bid, ask = sell.bid_price, buy.ask_price
    net_per_unit = bid * (1 - sell_fee_bps / 10_000) - ask * (1 + buy_fee_bps / 10_000)
    gross_bps = (bid - ask) / ask * 10_000
    net_bps = net_per_unit / ask * 10_000
    size = min(sell.bid_size, buy.ask_size)
    return ArbEdge(
        symbol=symbol, buy_venue=buy.venue_id, sell_venue=sell.venue_id, buy_price=ask, sell_price=bid,
        gross_bps=round(gross_bps, 4), fee_bps=round(gross_bps - net_bps, 4), net_bps=round(net_bps, 4),
        size=size, net_usdt=round(net_per_unit * size, 6),
    )


def cross_venue_edges(symbol: str, quotes: dict[str, VenueQuote], fees: dict[str, float]) -> list[ArbEdge]:
    """Every direction between every two venues quoting ``symbol``, best net edge first."""
    live = [q for q in quotes.values() if q.bid_price > 0 and q.ask_price > 0]
    edges = [
        edge_between(symbol, sell, buy, fee_for(fees, sell.venue_id), fee_for(fees, buy.venue_id))
        for sell in live for buy in live if sell.venue_id != buy.venue_id
    ]
    edges.sort(key=lambda e: e.net_bps, reverse=True)
    return edges


def fundable_size(
    edge: ArbEdge,
    *,
    base_free_on_sell: float | None,
    quote_free_on_buy: float | None,
    buy_fee_bps: float,
    blocked: dict[str, str],
    names: dict[str, str],
) -> tuple[float | None, list[str]]:
    """How much of ``edge`` our own accounts could trade right now, and why not more.

    Inventory is per exchange (found live in routing): the buy leg needs
    USDT on the buy venue, the sell leg needs the coin on the sell venue.

    Returns:
        (size, reasons). ``size`` is None when a balance is unknown; 0 when a
        venue can't execute this symbol at all (``blocked``: venue -> reason).
    """
    base, quote = split_symbol(edge.symbol)
    buy_name, sell_name = names.get(edge.buy_venue, edge.buy_venue), names.get(edge.sell_venue, edge.sell_venue)
    reasons = [f"{names.get(v, v)}: {blocked[v]}" for v in (edge.buy_venue, edge.sell_venue) if v in blocked]
    if reasons:
        return 0.0, reasons
    size, unknown = edge.size, False
    if quote_free_on_buy is None:
        unknown = True
        reasons.append(f"{buy_name} {quote} balance unknown")
    else:
        can_buy = quote_free_on_buy / (edge.buy_price * (1 + buy_fee_bps / 10_000))
        if can_buy < size:
            size = can_buy
            reasons.append(f"{buy_name} has {quote_free_on_buy:,.2f} {quote} to buy with")
    if base_free_on_sell is None:
        unknown = True
        reasons.append(f"{sell_name} {base} balance unknown")
    elif base_free_on_sell < size:
        size = base_free_on_sell
        reasons.append(f"{sell_name} holds {base_free_on_sell:.8g} {base} to sell")
    return (None if unknown else max(size, 0.0)), reasons


class WindowTracker:
    """Turns scans into windows and per-symbol statistics.

    Pure (no clock, no I/O): the caller passes the time of every scan, so
    tests can replay a synthetic market second by second.
    """

    def __init__(self, min_net_bps: float = 0.0, keep: int = 500, keep_profitable: int = 200,
                 since: datetime | None = None) -> None:
        self.min_net_bps = min_net_bps
        self.since = since or datetime.now(UTC)
        self.stats: dict[str, ArbSymbolStats] = {}
        self._open: dict[tuple[str, str, str], ArbWindow] = {}
        # Crossings flicker, so the full list only covers the last minutes;
        # the rare profitable windows get their own list so they aren't pushed out.
        self.recent: deque[ArbWindow] = deque(maxlen=keep)
        self.recent_profitable: deque[ArbWindow] = deque(maxlen=keep_profitable)

    def observe(self, symbol: str, edges: list[ArbEdge], now: datetime) -> list[ArbWindow]:
        """Record one scan of ``symbol`` (all its directions); return the windows it closed."""
        st = self.stats.setdefault(symbol, ArbSymbolStats(symbol=symbol))
        if edges:
            best_net = max(edges, key=lambda e: e.net_bps)
            best_gross = max(edges, key=lambda e: e.gross_bps)
            st.scans += 1
            st.sum_best_gross_bps += best_gross.gross_bps
            st.sum_best_net_bps += best_net.net_bps
            st.crossed_scans += best_gross.gross_bps > 0
            st.profitable_scans += best_net.net_bps > self.min_net_bps
            if st.max_gross is None or best_gross.gross_bps > st.max_gross.gross_bps:
                st.max_gross, st.max_gross_at = best_gross, now
            if st.max_net is None or best_net.net_bps > st.max_net.net_bps:
                st.max_net, st.max_net_at = best_net, now

        crossed = {(symbol, e.buy_venue, e.sell_venue): e for e in edges if e.gross_bps > 0}
        for key, e in crossed.items():
            w = self._open.get(key)
            if w is None:
                self._open[key] = ArbWindow(
                    symbol=symbol, buy_venue=e.buy_venue, sell_venue=e.sell_venue, opened_at=now,
                    peak_gross_bps=e.gross_bps, peak=e, profitable=e.net_bps > self.min_net_bps,
                )
                continue
            w.scans += 1
            w.duration_s = (now - w.opened_at).total_seconds()
            w.peak_gross_bps = max(w.peak_gross_bps, e.gross_bps)
            if e.net_bps > w.peak.net_bps:
                w.peak = e
            w.profitable = w.profitable or e.net_bps > self.min_net_bps
        # A direction no longer crossed — or whose venue stopped quoting — closes.
        ended = [key for key in self._open if key[0] == symbol and key not in crossed]
        return [self._close(key, now) for key in ended]

    def _close(self, key: tuple[str, str, str], now: datetime) -> ArbWindow:
        w = self._open.pop(key)
        w.closed_at = now
        w.duration_s = (now - w.opened_at).total_seconds()
        st = self.stats.setdefault(w.symbol, ArbSymbolStats(symbol=w.symbol))
        st.windows += 1
        st.window_seconds += w.duration_s
        for counts, label in ((st.duration_buckets, bucket(w.duration_s, DURATION_BUCKETS)),
                              (st.peak_gross_buckets, bucket(w.peak_gross_bps, GROSS_BUCKETS))):
            counts[label] = counts.get(label, 0) + 1
        if w.profitable:
            st.profitable_windows += 1
            self.recent_profitable.append(w)
        self.recent.append(w)
        return w

    def close_all(self, now: datetime) -> list[ArbWindow]:
        """Close every open window (shutdown), so each one is counted."""
        return [self._close(key, now) for key in list(self._open)]

    def open_windows(self, symbol: str | None = None, now: datetime | None = None) -> list[ArbWindow]:
        """Copies of the open windows, durations measured up to ``now``."""
        return [
            w.model_copy(update={"duration_s": (now - w.opened_at).total_seconds()} if now else {})
            for key, w in self._open.items() if symbol is None or key[0] == symbol
        ]

    def snapshot(self) -> dict:
        """What survives a restart: the statistics (not the open windows)."""
        return {"since": self.since.isoformat(),
                "stats": {s: st.model_dump(mode="json") for s, st in self.stats.items()}}

    def restore(self, saved: dict) -> None:
        self.since = datetime.fromisoformat(saved["since"])
        self.stats = {s: ArbSymbolStats.model_validate(v) for s, v in saved.get("stats", {}).items()}


def describe_symbol(st: ArbSymbolStats) -> str:
    """One plain-English line about a symbol's statistics."""
    if st.scans == 0:
        return f"{st.symbol}: waiting for live quotes from at least two venues."
    text = (f"{st.symbol}: crossed in {100 * st.crossed_scans / st.scans:.0f}% of scans. On average the widest "
            f"venue pair was {st.sum_best_gross_bps / st.scans:+.1f} bps apart before fees; the best pair "
            f"after taker fees was {st.sum_best_net_bps / st.scans:+.1f} bps.")
    if st.windows:
        short = st.duration_buckets.get("<1 s", 0) + st.duration_buckets.get("1-5 s", 0)
        text += (f" {st.profitable_windows:,} of {st.windows:,} crossed windows were profitable after fees; "
                 f"{100 * short / st.windows:.0f}% lasted under 5 s.")
    return text


def describe_overall(stats: list[ArbSymbolStats], since: datetime) -> str:
    """The headline: how often the books cross, and how that compares with the fees."""
    scans = sum(st.scans for st in stats)
    if scans == 0:
        return "Waiting for live quotes from at least two venues."
    crossed = sum(st.crossed_scans for st in stats)
    windows = sum(st.windows for st in stats)
    profitable = sum(st.profitable_windows for st in stats)
    avg_gross = sum(st.sum_best_gross_bps for st in stats) / scans
    avg_net = sum(st.sum_best_net_bps for st in stats) / scans
    text = (f"Since {since:%d %b %H:%M} UTC: the books were crossed in {100 * crossed / scans:.0f}% of scans, "
            f"the widest pair by {avg_gross:+.1f} bps on average - but after taker fees the best pair averaged "
            f"{avg_net:+.1f} bps. {profitable:,} of {windows:,} crossed windows were profitable after fees.")
    best = max((st.max_net for st in stats if st.max_net), key=lambda e: e.net_bps, default=None)
    if best and best.net_bps > 0:
        text += (f" Best seen: {best.symbol} {best.net_bps:+.1f} bps after fees "
                 f"(${best.net_usdt:,.2f} at the top of both books).")
    return text


def describe_window(w: ArbWindow, names: dict[str, str]) -> str:
    """Audit-log summary of a closed window."""
    e = w.peak
    base = split_symbol(w.symbol)[0]
    return (f"{w.symbol}: buy on {names.get(e.buy_venue, e.buy_venue)} at {e.buy_price:,.8g}, sell on "
            f"{names.get(e.sell_venue, e.sell_venue)} at {e.sell_price:,.8g} - {e.gross_bps:+.1f} bps gross, "
            f"{e.net_bps:+.1f} bps after taker fees ({e.fee_bps:.0f} bps), ${e.net_usdt:,.4f} on "
            f"{e.size:.8g} {base} at the top of both books; lasted {w.duration_s:.1f} s. "
            f"Detector only - nothing was traded.")


# ── the service ─────────────────────────────────────────────────────
class ArbitrageDetector:
    """Scans the live books, keeps windows and statistics, audits profitable windows.

    It is given read-only lookups — books, balances, which venues can execute —
    and no adapter, router or ``OrderEntry``: it can only look.
    """

    def __init__(
        self,
        collector,
        venue_monitor,
        store,
        symbols: list[str],
        fees_bps: dict[str, float],
        *,
        venue_names: dict[str, str],
        excluded_venues: Callable[[str], dict[str, str]],
        balance: Callable[[str, str], float | None],
        interval_s: float = 0.25,
        min_net_bps: float = 0.0,
        audit_cooldown_s: float = 60.0,
        enabled: bool = True,
        persist_every_s: float = 60.0,
        publish_every_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            venue_names: venue id -> exchange name, for plain-English text.
            excluded_venues: symbol -> {venue id: why it can't execute that
                symbol now} (``RoutingEngine.excluded_venues``).
            balance: (venue id, asset) -> free balance or None if unknown
                (``WalletService.available``).
        """
        self._collector = collector
        self._venue_monitor = venue_monitor
        self._store = store
        self._symbols = list(symbols)
        self._fees = dict(fees_bps)
        self._names = dict(venue_names)
        self._excluded_venues = excluded_venues
        self._balance = balance
        self._interval_s = interval_s
        self._min_net_bps = min_net_bps
        self._audit_cooldown_s = audit_cooldown_s
        self._enabled = enabled
        self._persist_every_s = persist_every_s
        self._publish_every_s = publish_every_s
        self._clock = clock
        self.tracker = WindowTracker(min_net_bps)
        self._latest: dict[str, tuple[list[ArbEdge], list[str]]] = {}
        self._last_audit: dict[tuple[str, str, str], float] = {}
        self._last_publish = self._last_persist = clock()
        self._running = False

    async def load(self) -> None:
        """Bring back the statistics of earlier runs (the panel story spans days)."""
        saved = await self._store.kv_get(STATS_KEY)
        if not saved:
            return
        try:
            self.tracker.restore(saved)
        except (KeyError, ValueError) as exc:
            logger.warning("arbitrage_stats_unreadable", error=str(exc)[:160])

    # ── scanning ────────────────────────────────────────────────────
    def scan_once(self, now: datetime | None = None) -> list[ArbWindow]:
        """One pass over every symbol; returns the windows that closed."""
        now = now or datetime.now(UTC)
        healthy = set(self._venue_monitor.get_routable_venues())  # same venues the NBBO uses
        closed: list[ArbWindow] = []
        for symbol in self._symbols:
            quotes = {v: q for v, q in self._collector.get_all_quotes_for_symbol(symbol).items() if v in healthy}
            edges = cross_venue_edges(symbol, quotes, self._fees) if len(quotes) >= 2 else []
            self._latest[symbol] = (edges, sorted(quotes))
            closed += self.tracker.observe(symbol, edges, now)
        return closed

    async def _report(self, closed: list[ArbWindow]) -> None:
        for w in closed:
            if w.profitable:
                await self._audit_window(w)
        now = self._clock()
        if now - self._last_publish >= self._publish_every_s:
            self._last_publish = now
            await event_bus.publish("arb_update", self._live_summary())
        if now - self._last_persist >= self._persist_every_s:
            self._last_persist = now
            self._persist()

    async def _audit_window(self, w: ArbWindow) -> None:
        # Bounded: if fees were set too low every flicker would "profit";
        # one audit per direction per cooldown, the rest are still counted.
        key = (w.symbol, w.buy_venue, w.sell_venue)
        now = self._clock()
        if now - self._last_audit.get(key, -math.inf) < self._audit_cooldown_s:
            return
        self._last_audit[key] = now
        await audit("arb_opportunity", describe_window(w, self._names), actor=DETECTOR_ACTOR,
                    symbol=w.symbol, venue_id=w.buy_venue, payload=w.model_dump(mode="json"))

    def _live_summary(self) -> dict:
        symbols = {}
        for symbol, (edges, venues) in self._latest.items():
            best = edges[0] if edges else None
            symbols[symbol] = {
                "venues": venues,
                "best_net_bps": best.net_bps if best else None,
                "best_gross_bps": max((e.gross_bps for e in edges), default=None),
                "buy_venue": best.buy_venue if best else None,
                "sell_venue": best.sell_venue if best else None,
                "crossed_directions": sum(e.gross_bps > 0 for e in edges),
            }
        return {"as_of": datetime.now(UTC).isoformat(), "symbols": symbols}

    def _persist(self) -> None:
        if self._store.is_open:
            self._store.kv_set(STATS_KEY, self.tracker.snapshot())

    async def start(self) -> None:
        if not self._enabled:
            logger.info("arbitrage_detector_disabled")
            return
        self._running = True
        logger.info("arbitrage_detector_started", interval_s=self._interval_s, fees_bps=self._fees)
        while self._running:
            try:
                await self._report(self.scan_once())
            except Exception as exc:  # noqa: BLE001 — keep scanning no matter what
                logger.error("arbitrage_scan_failed", error=str(exc)[:200])
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for w in self.tracker.close_all(datetime.now(UTC)):
            if w.profitable:
                await self._audit_window(w)
        self._persist()

    # ── reads for the API ───────────────────────────────────────────
    def _with_funding(self, edge: ArbEdge) -> ArbEdge:
        base, quote = split_symbol(edge.symbol)
        legs = (edge.buy_venue, edge.sell_venue)
        blocked = {v: why for v, why in self._excluded_venues(edge.symbol).items() if v in legs}
        size, reasons = fundable_size(
            edge,
            base_free_on_sell=self._balance(edge.sell_venue, base),
            quote_free_on_buy=self._balance(edge.buy_venue, quote),
            buy_fee_bps=fee_for(self._fees, edge.buy_venue),
            blocked=blocked,
            names=self._names,
        )
        return edge.model_copy(update={"fundable_size": size, "limited_by": reasons})

    def status(self) -> ArbStatus:
        now = datetime.now(UTC)
        symbols = []
        for symbol in self._symbols:
            edges, venues = self._latest.get(symbol, ([], []))
            st = self.tracker.stats.get(symbol) or ArbSymbolStats(symbol=symbol)
            symbols.append(ArbSymbolStatus(
                symbol=symbol,
                venues=venues,
                best=self._with_funding(edges[0]) if edges else None,
                crossed_directions=sum(e.gross_bps > 0 for e in edges),
                open_windows=self.tracker.open_windows(symbol, now),
                stats=st,
                summary=describe_symbol(st),
            ))
        return ArbStatus(
            enabled=self._enabled,
            running=self._running,
            scan_interval_ms=round(self._interval_s * 1000),
            min_net_bps=self._min_net_bps,
            fees_bps={vid: fee_for(self._fees, vid) for vid in sorted(self._names)},
            venue_names=dict(self._names),
            stats_since=self.tracker.since,
            symbols=symbols,
            summary=describe_overall([s.stats for s in symbols], self.tracker.since),
        )

    def windows(self, symbol: str | None = None, profitable_only: bool = False, limit: int = 100) -> ArbWindowList:
        source = self.tracker.recent_profitable if profitable_only else self.tracker.recent
        closed = [w for w in reversed(source) if symbol is None or w.symbol == symbol]
        opened = [w for w in self.tracker.open_windows(symbol, datetime.now(UTC))
                  if not profitable_only or w.profitable]
        return ArbWindowList(open=opened, windows=closed[:limit])
