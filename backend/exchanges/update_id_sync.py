"""Keeps one LocalOrderBook in sync with an update-id-chained diff stream.

Used by exchanges that publish (a) a REST depth snapshot carrying an update
id and (b) a WebSocket diff stream whose events carry a first/last update id
``[U, u]`` — Binance and Gate both work this way. The procedure is Binance's
documented "manage a local order book correctly":

  1. Open the diff stream and BUFFER events.
  2. Fetch a REST snapshot (``lastUpdateId`` = L).
  3. If the stream already moved past L (first buffered U > L + 1), the
     snapshot is too old: re-fetch.
  4. Drop buffered events with u <= L (already contained in the snapshot).
  5. Apply the rest. Every event must continue the chain: U <= local_id + 1.
     If U > local_id + 1 an update was missed -> the book is wrong -> resync.

Levels are absolute quantities, so re-applying an overlapping event is
harmless — only a *hole* in the id sequence corrupts the book.
"""

from enum import Enum
from typing import Any

from backend.exchanges.orderbook import Level, LocalOrderBook

MAX_BUFFERED_EVENTS = 10_000


class DiffResult(str, Enum):
    BUFFERED = "BUFFERED"  # book not seeded yet; event kept for later
    APPLIED = "APPLIED"
    STALE = "STALE"  # already contained in the book; ignored
    GAP = "GAP"  # missed updates; caller must re-fetch a snapshot


def _parse_levels(raw: list[list[str]]) -> list[Level]:
    return [(float(level[0]), float(level[1])) for level in raw]


class UpdateIdChainSynchronizer:
    def __init__(self, book: LocalOrderBook) -> None:
        self.book = book
        self._buffer: list[dict[str, Any]] = []
        self.resyncs = 0

    @property
    def needs_snapshot(self) -> bool:
        return not self.book.synced

    def on_diff(self, event: dict[str, Any]) -> DiffResult:
        """Feed one diff event (keys ``U``, ``u``, ``b``, ``a``)."""
        if not self.book.synced:
            self._buffer.append(event)
            if len(self._buffer) > MAX_BUFFERED_EVENTS:
                # Dropping the oldest is safe: if the snapshot turns out to be
                # older than what remains, load_snapshot() reports GAP.
                del self._buffer[0]
            return DiffResult.BUFFERED
        return self._apply(event)

    def load_snapshot(self, snapshot: dict[str, Any]) -> DiffResult:
        """Seed the book from ``{"lastUpdateId", "bids", "asks"}``.

        Returns APPLIED when the book is live, or GAP when the caller must
        fetch a newer snapshot and try again.
        """
        last_id = int(snapshot["lastUpdateId"])
        if self._buffer and last_id < int(self._buffer[0]["U"]) - 1:
            return DiffResult.GAP  # step 3: the stream already moved past this snapshot

        self.book.load_snapshot(
            _parse_levels(snapshot["bids"]), _parse_levels(snapshot["asks"]), last_id
        )
        pending, self._buffer = self._buffer, []
        for event in pending:
            if int(event["u"]) <= last_id:  # step 4
                continue
            if self._apply(event) is DiffResult.GAP:  # step 5
                self.reset()
                return DiffResult.GAP
        return DiffResult.APPLIED

    def reset(self) -> None:
        """Throw the book away; the next snapshot re-seeds it."""
        self.book.invalidate()
        self._buffer.clear()
        self.resyncs += 1

    def reset_for_reconnect(self) -> None:
        """Same as reset(), but a reconnect is not counted as a resync."""
        self.book.invalidate()
        self._buffer.clear()

    def _apply(self, event: dict[str, Any]) -> DiffResult:
        first_id, last_id = int(event["U"]), int(event["u"])
        local_id = self.book.last_update_id
        if last_id <= local_id:
            return DiffResult.STALE
        if first_id > local_id + 1:
            return DiffResult.GAP
        self.book.apply_levels(_parse_levels(event["b"]), _parse_levels(event["a"]), last_id)
        return DiffResult.APPLIED
