"""Order books whose snapshot arrives IN the stream, chained by a sequence id.

Bybit and OKX both send a full snapshot as the first message after you
subscribe, then deltas. Each delta carries an id that must continue from the
previous one:

    Bybit  ``u``         — next delta's u == previous u + 1
    OKX    ``prevSeqId`` — next delta's prevSeqId == previous seqId

The adapter decides whether a delta is chained (the rule differs per venue);
this class applies it or, on a break, invalidates the book so the adapter can
reconnect for a fresh snapshot. (OKX also defines a CRC32 checksum, but its
public ``books`` channel currently sends ``checksum: 0`` — observed
2026-09-28 — so the sequence chain is the only integrity check available.)
"""

from enum import Enum

from backend.exchanges.orderbook import Level, LocalOrderBook


class ChainResult(str, Enum):
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"  # delta before the snapshot; nothing to apply it to
    GAP = "GAP"  # chain broken -> book invalidated -> reconnect/resubscribe


class InStreamSnapshotSync:
    def __init__(self, book: LocalOrderBook) -> None:
        self.book = book
        self.gaps = 0

    @property
    def last_id(self) -> int:
        return self.book.last_update_id

    def on_snapshot(self, bids: list[Level], asks: list[Level], seq: int) -> None:
        self.book.load_snapshot(bids, asks, seq)

    def on_delta(self, bids: list[Level], asks: list[Level], seq: int, chained: bool) -> ChainResult:
        if not self.book.synced:
            return ChainResult.IGNORED
        if not chained:
            self.gaps += 1
            self.book.invalidate()
            return ChainResult.GAP
        self.book.apply_levels(bids, asks, seq)
        return ChainResult.APPLIED

    def reset(self) -> None:
        self.book.invalidate()
