"""Incident guard: when an exchange is in trouble, stop sending it new orders and tell the owner.

Written after the Binance incident of 2026-10-29: ~$6.3bn taken from hot wallets, withdrawals closed on every coin.
A router that chases the best price is at its most dangerous on such a day. With withdrawals closed, arbitrage can't
close the gap to the other exchanges, so the exchange in trouble can show the best price on the screen. The health
monitor saw nothing either: Binance's books, feed and latency stayed healthy.

Two signals, neither of which depends on our own account, the exchange's sandbox or an API key:

  * **Can money leave?** What the exchange itself says, per coin (``backend/exchanges/transfers.py``).
  * **Does the price still agree?** Each exchange's mid against the median of the others.

and what they lead to:

    NORMAL   nothing to report. A few closed coins are an ordinary day (Gate 1 of 21, HTX 3 of 21, measured).
    WATCH    a quarter of the coins closed, USDT closed, most deposits closed, or 3+ coins far from the market for
             ``gap_hold_s``: the owner is told, routing is unchanged.
    GUARDED  half of the coins closed, or half of them far from the market: the venue is blacklisted through
             RiskControls, so it is replicated, survives a restart and leaves the NBBO. Orders, algos and bots keep
             running on the other exchanges through the normal order path.

One coin far from the market leaves routing for that coin alone (``blocking_reason``): that price isn't a bargain.

The rule is the kill switch's: tightening is automatic, loosening is a person's. A signal that clears is reported,
but the block stays until someone lifts it on the Venues page. If someone lifts it while the signal continues, that
decision is respected until this episode ends (a restart forgets it: fail-safe). A level rises at once and falls
only after ``clear_after_s`` of quiet, so a flickering signal doesn't flood the phone.

What this is NOT: it can't stop a hack, and it can't bring back money already on the exchange; our keys can't
withdraw, by design. It limits NEW exposure and tells you how much is parked there. Everything here is detection and
tightening: it can't place an order.
"""

import asyncio
import math
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.exchanges.registry import ExchangeRegistry
from backend.exchanges.transfers import CoinTransfers, TransferStatus
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.market_data import VenueQuote, split_symbol
from backend.shared.models.venue import IncidentLevel, VenueGuard
from backend.shared.utils.logger import get_logger

logger = get_logger("incident_guard")

ACTOR = "incident-guard"
QUOTE_COIN = "USDT"
WATCH_SHARE = 0.25  # of the coins we watch
GUARD_SHARE = 0.5
MIN_COINS = 10  # fewer coins than this can't speak for a whole exchange
MIN_GAP_COINS = 3  # one or two odd coins are those coins' story, not the exchange's
MIN_PEERS = 2  # a median of one exchange is just that exchange
STALE_AFTER_READS = 5  # a list older than this many reads no longer counts as known
WARM_UP_S = 15.0  # books and wallet prices are still arriving: found live, an alert quoted $7,696 of $96,640
RANK ={IncidentLevel.NORMAL: 0, IncidentLevel.WATCH: 1, IncidentLevel.GUARDED: 2}


class DrillRefusedError(DomainError):
    def __init__(self) -> None:
        super().__init__("An incident drill runs in the sandbox only, never on real money.", "DRILL_REFUSED")


# ── pure logic ──────────────────────────────────────────────────────
def price_gaps(mids: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """symbol -> venue -> mid. Answers venue -> symbol -> gap in bps to the median of the OTHER exchanges.

    The others, not everyone: with three exchanges the odd one out would pull its own yardstick toward itself.
    """
    gaps: dict[str, dict[str, float]] = {}
    for symbol, quotes in mids.items():
        for venue, mid in quotes.items():
            others = [m for v, m in quotes.items() if v != venue]
            if mid > 0 and len(others) >= MIN_PEERS:
                gaps.setdefault(venue, {})[symbol] = (mid / statistics.median(others) - 1) * 10_000
    return gaps


def assess(status: TransferStatus | None, wide_gap_coins: int, coins_compared: int) -> tuple[IncidentLevel, list[str]]:
    """The level one exchange is at, and why in plain English. Pure.

    Args:
        status: what the exchange says about its coins; None = it doesn't say, which proves nothing either way.
        wide_gap_coins: coins whose price stayed far from the other exchanges.
        coins_compared: coins with a price to compare at all.
    """
    level, reasons = IncidentLevel.NORMAL, []

    def raise_to(new: IncidentLevel, reason: str) -> None:
        nonlocal level
        level = max(level, new, key=RANK.__getitem__)
        reasons.append(reason)

    if status is not None and len(status.coins) >= MIN_COINS:
        total, closed = len(status.coins), status.withdrawals_closed
        if len(closed) >= total * GUARD_SHARE:
            raise_to(IncidentLevel.GUARDED, f"withdrawals closed on {len(closed)} of {total} coins")
        elif len(closed) >= total * WATCH_SHARE:
            raise_to(IncidentLevel.WATCH, f"withdrawals closed on {len(closed)} of {total} coins")
        elif QUOTE_COIN in closed:
            raise_to(IncidentLevel.WATCH, f"{QUOTE_COIN} withdrawals closed")
        if len(status.deposits_closed) >= total * GUARD_SHARE:
            raise_to(IncidentLevel.WATCH, f"deposits closed on {len(status.deposits_closed)} of {total} coins")

    if wide_gap_coins >= max(MIN_GAP_COINS, math.ceil(coins_compared * GUARD_SHARE)):
        raise_to(IncidentLevel.GUARDED, f"prices far from the other exchanges on {wide_gap_coins} of "
                                        f"{coins_compared} coins")
    elif wide_gap_coins >= MIN_GAP_COINS:
        raise_to(IncidentLevel.WATCH, f"prices far from the other exchanges on {wide_gap_coins} of "
                                      f"{coins_compared} coins")
    return level, reasons


def drill_status(coins: list[str]) -> TransferStatus:
    """What the day Binance closed withdrawals looked like: every coin shut, deposits open. Pure."""
    return TransferStatus(tuple(CoinTransfers(c, False, True) for c in coins), "a drill")


@dataclass
class _Record:
    level: IncidentLevel = IncidentLevel.NORMAL
    reasons: list[str] = field(default_factory=list)
    since: datetime | None = None
    quiet_since: float | None = None


class IncidentGuard:
    def __init__(
        self,
        registry: ExchangeRegistry,
        market_data_collector,
        wallet,
        risk_controls,
        symbols: list[str],
        *,
        enabled: bool = True,
        auto_block: bool = True,
        gap_bps: float = 75.0,
        gap_hold_s: float = 30.0,
        interval_s: float = 5.0,
        transfers_interval_s: float = 60.0,
        clear_after_s: float = 300.0,
        warm_up_s: float = WARM_UP_S,
        real_money: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._collector = market_data_collector
        self._wallet = wallet
        self._controls = risk_controls
        self._symbols = list(symbols)
        self._coins = sorted({split_symbol(s)[0] for s in symbols} | {QUOTE_COIN})
        self._enabled = enabled
        self._auto_block = auto_block
        self._gap_bps = gap_bps
        self._gap_hold_s = gap_hold_s
        self._interval_s = interval_s
        self._transfers_interval_s = transfers_interval_s
        self._clear_after_s = clear_after_s
        self._warm_up_s = warm_up_s
        self._real_money = real_money
        self._clock = clock
        self._transfers: dict[str, tuple[TransferStatus, float]] = {}  # venue -> (last answer, read at)
        self._gaps: dict[str, dict[str, float]] = {}
        self._wide_since: dict[tuple[str, str], float] = {}  # (venue, symbol) -> when it first went wide
        self._records: dict[str, _Record] = {}
        self._acted: set[str] = set()  # episodes the guard has answered: it never re-blocks within one
        self._held: set[str] = set()  # venues the guard took out, while they are still out
        self._drills: set[str] = set()
        self._states: dict[str, VenueGuard] = {}
        self._lock = asyncio.Lock()  # the loop and a drill request: one decision at a time
        self._running = False

    # ── reads ───────────────────────────────────────────────────────
    def state(self, venue_id: str) -> VenueGuard:
        return self._states.get(venue_id, VenueGuard())

    def venue_reason(self, venue_id: str) -> str | None:
        """Why the guard keeps this venue out of routing, or None if it doesn't."""
        if venue_id not in self._held:
            return None
        reasons = self._records[venue_id].reasons if venue_id in self._records else []
        if reasons:
            return f"incident guard: {'; '.join(reasons)}"
        return "incident guard: the signal has cleared, and a person lifts the block on the Venues page"

    def blocking_reason(self, venue_id: str, symbol: str) -> str | None:
        """Why this one coin stays away from this venue: its price stayed far from every other exchange."""
        gap = self._gaps.get(venue_id, {}).get(symbol)
        if gap is None or not self._held_wide(venue_id, symbol):
            return None
        adapter = self._registry.get(venue_id)
        name = adapter.name if adapter else venue_id
        return (f"incident guard: {name}'s {split_symbol(symbol)[0]} price is {gap:+.0f} bps from the other "
                f"exchanges (limit ±{self._gap_bps:g}), and a price that far out is a warning, not a bargain")

    def left_out_quotes(self, symbol: str) -> dict[str, VenueQuote]:
        """What the venues held out are quoting. They aren't in the NBBO, but the routing proof should show what
        was passed up: on 2026-10-30 Binance had the best sell price on 17 of 20 coins, after fees."""
        return {v: q for v, q in self._collector.get_all_quotes_for_symbol(symbol).items() if v in self._held}

    def routing_notes(self, symbol: str) -> list[str]:
        """One line per venue the guard kept out of this order, for the routing proof."""
        names = {a.venue_id: a.name for a in self._registry.all()}
        notes = [f"{names.get(v, v)} left out: {why}" for v in sorted(self._held) if (why := self.venue_reason(v))]
        notes += [why[0].upper() + why[1:] for v in names if v not in self._held
                  and (why := self.blocking_reason(v, symbol))]
        return notes

    def _held_wide(self, venue_id: str, symbol: str) -> bool:
        since = self._wide_since.get((venue_id, symbol))
        return since is not None and self._clock() - since >= self._gap_hold_s

    # ── a simulated incident (the sandbox's fire drill) ─────────────
    async def set_drill(self, venue_id: str, on: bool, actor: str) -> VenueGuard:
        """Pretend this exchange closed its withdrawals, so the whole response can be rehearsed on a calm day."""
        if self._real_money:
            raise DrillRefusedError()
        if on:
            self._drills.add(venue_id)
        else:
            self._drills.discard(venue_id)
            if record := self._records.get(venue_id):  # a drill ends when it is called off: no waiting for quiet
                record.quiet_since = self._clock() - self._clear_after_s
        await audit("incident_drill", f"{venue_id}: incident drill {'started' if on else 'ended'} by {actor} "
                    "(simulated, sandbox only)", actor=actor, venue_id=venue_id)
        await self.evaluate()
        return self.state(venue_id)

    # ── reading the signals ─────────────────────────────────────────
    async def refresh_transfers(self) -> None:
        await asyncio.gather(*(self._read_transfers(a) for a in self._registry.all()))

    async def _read_transfers(self, adapter) -> None:
        try:
            status = await adapter.fetch_transfer_status(self._coins)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — an unreadable list proves nothing: the last answer stands
            logger.warning("transfer_status_failed", venue_id=adapter.venue_id, error=type(exc).__name__)
            return
        if status is not None:
            self._transfers[adapter.venue_id] = (status, self._clock())

    def _known_transfers(self, venue_id: str) -> TransferStatus | None:
        entry = self._transfers.get(venue_id)
        if entry is None or self._clock() - entry[1] > self._transfers_interval_s * STALE_AFTER_READS:
            return None
        return entry[0]

    def _mids(self) -> dict[str, dict[str, float]]:
        """Fresh real-market mids only: the collector drops stale quotes, so a dead feed isn't a price gap,
        and a testnet book (MARKET_DATA_MODE=testnet) says nothing about an exchange."""
        real = {a.venue_id for a in self._registry.all() if a.data_mode == "mainnet"}
        out: dict[str, dict[str, float]] = {}
        for symbol in self._symbols:
            quotes = self._collector.get_all_quotes_for_symbol(symbol)
            out[symbol] = {v: (q.bid_price + q.ask_price) / 2 for v, q in quotes.items()
                           if v in real and q.bid_price > 0 and q.ask_price > 0}
        return out

    def _track_gaps(self, now: float) -> None:
        self._gaps = price_gaps(self._mids())
        wide = {(v, s) for v, gaps in self._gaps.items() for s, gap in gaps.items() if abs(gap) >= self._gap_bps}
        self._wide_since = {key: self._wide_since.get(key, now) for key in wide}

    def _funds(self, venue_id: str) -> tuple[float | None, float | None]:
        snap = self._wallet.get_snapshot()
        row = next((v for v in snap.venues if v.venue_id == venue_id and not v.error), None)
        if row is None or not snap.as_of:
            return None, None
        share = row.total_value_usdt / snap.total_value_usdt * 100 if snap.total_value_usdt > 0 else None
        return row.total_value_usdt, share

    # ── deciding ────────────────────────────────────────────────────
    async def evaluate(self) -> None:
        async with self._lock:
            now = self._clock()
            self._track_gaps(now)
            blacklisted = self._controls.local_blacklist()
            self._held &= blacklisted  # a person lifted it
            for adapter in self._registry.all():
                vid = adapter.venue_id
                drilling = vid in self._drills
                status = drill_status(self._coins) if drilling else self._known_transfers(vid)
                gaps = self._gaps.get(vid, {})
                wide = sorted(split_symbol(s)[0] for s in gaps if self._held_wide(vid, s))
                level, reasons = assess(status, len(wide), len(gaps))
                if drilling:
                    reasons = [f"DRILL: {r} (simulated)" for r in reasons]
                await self._settle(adapter, level, reasons, now, blacklisted)
                self._publish_state(adapter, status, wide, gaps, drilling, blacklisted)

    async def _settle(self, adapter, level: IncidentLevel, reasons: list[str], now: float,
                      blacklisted: set[str]) -> None:
        """A level rises at once and falls after a quiet spell; the block follows the settled level."""
        vid = adapter.venue_id
        record = self._records.setdefault(vid, _Record())
        before = record.level
        if RANK[level] >= RANK[before]:
            record.level, record.reasons, record.quiet_since = level, reasons, None
            if RANK[level] > RANK[before]:
                record.since = datetime.now(UTC)
        else:
            record.quiet_since = now if record.quiet_since is None else record.quiet_since
            if now - record.quiet_since >= self._clear_after_s:
                record.level, record.reasons, record.quiet_since = level, reasons, None

        if record.level == IncidentLevel.GUARDED and vid not in self._acted:
            await self._block(vid, blacklisted)
        if RANK[record.level] > RANK[before]:
            await self._announce(adapter, record)
        elif record.level == IncidentLevel.NORMAL and before != IncidentLevel.NORMAL:
            self._acted.discard(vid)
            record.since = None
            await self._announce_cleared(adapter)

    async def _block(self, venue_id: str, blacklisted: set[str]) -> None:
        if venue_id in blacklisted:  # already out: an operator did it, or it survived a restart
            self._acted.add(venue_id)
            self._held.add(venue_id)
        elif self._auto_block:
            await self._controls.blacklist_venue(venue_id, actor=ACTOR)  # first, in case it raises: then it retries
            self._acted.add(venue_id)
            self._held.add(venue_id)
            blacklisted.add(venue_id)

    def _action_text(self, venue_id: str, level: IncidentLevel) -> str:
        if level != IncidentLevel.GUARDED:
            return "Routing is unchanged; watch it."
        if venue_id not in self._held:
            return "Automatic blocking is off (INCIDENT_GUARD_AUTO_BLOCK): decide on the Venues page."
        out = self._controls.local_blacklist()
        if any(a.can_trade and a.venue_id not in out for a in self._registry.all()):
            return "No new orders go there; orders route through the other exchanges until a person lifts this."
        return "No new orders go there, and no other exchange can take them: orders are refused until a person lifts this."

    async def _announce(self, adapter, record: _Record) -> None:
        funds, share = self._funds(adapter.venue_id)
        parked = "" if funds is None else f" Our {'' if self._real_money else 'sandbox '}balance there: ${funds:,.2f}" + (
            "." if share is None else f" ({share:.0f}% of the portfolio).")
        message = f"{adapter.name}: {'; '.join(record.reasons)}. {self._action_text(adapter.venue_id, record.level)}{parked}"
        logger.warning("venue_incident", venue_id=adapter.venue_id, level=record.level.value, reasons=record.reasons)
        await event_bus.publish("risk_alert", {"type": "VENUE_INCIDENT", "message": message})
        await audit("venue_incident", message, actor=ACTOR, venue_id=adapter.venue_id,
                    payload={"level": record.level.value, "reasons": record.reasons, "funds_usdt": funds})

    async def _announce_cleared(self, adapter) -> None:
        held = adapter.venue_id in self._held
        message = (f"{adapter.name}: the warning signs have cleared."
                   + (" It stays blocked until a person lifts it on the Venues page." if held else ""))
        await event_bus.publish("risk_alert", {"type": "VENUE_INCIDENT", "message": message})
        await audit("venue_incident_cleared", message, actor=ACTOR, venue_id=adapter.venue_id)

    def _publish_state(self, adapter, status: TransferStatus | None, wide: list[str], gaps: dict[str, float],
                       drilling: bool, blacklisted: set[str]) -> None:
        vid = adapter.venue_id
        record = self._records[vid]
        funds, share = self._funds(vid)
        lifted = record.level == IncidentLevel.GUARDED and vid in self._acted and vid not in blacklisted
        self._states[vid] = VenueGuard(
            level=record.level,
            reasons=[*record.reasons, *(["a person lifted the block while the signal continues"] if lifted else [])],
            blocked=vid in self._held, lifted=lifted, since=record.since, checked_at=datetime.now(UTC),
            transfers_known=status is not None, transfer_source=status.source if status else None,
            coins_checked=len(status.coins) if status else 0,
            withdrawals_closed=len(status.withdrawals_closed) if status else 0,
            deposits_closed=len(status.deposits_closed) if status else 0,
            coins_compared=len(gaps), wide_gap_coins=wide,
            median_gap_bps=round(statistics.median(gaps.values()), 2) if gaps else None,
            funds_usdt=funds, funds_share_pct=round(share, 1) if share is not None else None,
            drill=drilling,
        )

    # ── running ─────────────────────────────────────────────────────
    async def start(self) -> None:
        if not self._enabled:
            logger.info("incident_guard_off")
            return
        self._running = True
        logger.info("incident_guard_started", auto_block=self._auto_block, gap_bps=self._gap_bps)
        await asyncio.sleep(self._warm_up_s)
        next_read = 0.0
        while self._running:
            try:
                if self._clock() >= next_read:
                    next_read = self._clock() + self._transfers_interval_s
                    await self.refresh_transfers()
                await self.evaluate()
            except Exception as exc:  # noqa: BLE001 — the guard must keep watching
                logger.error("incident_guard_failed", error=str(exc)[:200])
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        self._running = False
