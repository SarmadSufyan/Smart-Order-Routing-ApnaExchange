"""Durable store for the audit log and the portfolio book of record.

SQLite, embedded: zero setup for every team member, one file per exchange
mode (testnet and mainnet records never mix). The SQL is deliberately plain
(TEXT timestamps, JSON as TEXT, ``ON CONFLICT`` upserts) so a PostgreSQL /
TimescaleDB backend can implement the same interface later.

Writes never block the trading path: they are queued in the order they
happen and applied by ONE background writer, so a later snapshot of an
order can never be overwritten by an earlier one. Reads go straight to the
database (call ``flush()`` first when a read must see the latest writes).
"""

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from backend.shared.models.audit import AuditEvent
from backend.shared.models.automation import BotState, LabSample
from backend.shared.models.order import Order
from backend.shared.models.portfolio import FillRecord, VenueBaseline
from backend.shared.models.strategy import TwapState
from backend.shared.utils.logger import get_logger

logger = get_logger("audit_store")

T = TypeVar("T")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor      TEXT,
    order_id   TEXT,
    venue_id   TEXT,
    symbol     TEXT,
    summary    TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_order ON audit_events(order_id);
CREATE INDEX IF NOT EXISTS ix_audit_type ON audit_events(event_type, seq);

CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    status       TEXT NOT NULL,
    submitted_by TEXT,
    snapshot     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_created ON orders(created_at);

CREATE TABLE IF NOT EXISTS fills (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    order_id          TEXT NOT NULL,
    venue_id          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    quantity          REAL NOT NULL,
    price             REAL NOT NULL,
    fee               REAL NOT NULL,
    fee_asset         TEXT,
    fee_usdt          REAL,
    exchange_order_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_fills_ts ON fills(ts);

CREATE TABLE IF NOT EXISTS baselines (
    venue_id TEXT NOT NULL,
    ts       TEXT NOT NULL,
    set_by   TEXT NOT NULL,
    balances TEXT NOT NULL,
    marks    TEXT NOT NULL,
    PRIMARY KEY (venue_id, ts)
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS algos (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    snapshot   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_algos_created ON algos(created_at);

CREATE TABLE IF NOT EXISTS bots (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    snapshot   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_samples (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    bot_id    TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    snapshot  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lab_opened ON lab_samples(opened_at);
"""

MAX_BATCH = 500


def _iso(ts: datetime) -> str:
    # Fixed-width, UTC: string order == time order.
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


class AuditStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()  # one connection, used from worker threads
        self._queue: asyncio.Queue[tuple[str, tuple]] | None = None
        self._writer_task: asyncio.Task | None = None
        self._seq = 0

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    # ── lifecycle ───────────────────────────────────────────────────
    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._writer())
        logger.info("audit_store_opened", path=self._path, last_seq=self._seq)

    def _open_sync(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        if self._path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        self._seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM audit_events").fetchone()[0]
        self._conn = conn

    async def flush(self) -> None:
        """Wait until every queued write is on disk."""
        if self._queue is not None:
            await self._queue.join()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self.flush()
        if self._writer_task:
            self._writer_task.cancel()
            await asyncio.gather(self._writer_task, return_exceptions=True)
        with self._lock:
            self._conn.close()
        self._conn = None
        logger.info("audit_store_closed", path=self._path)

    # ── writer ──────────────────────────────────────────────────────
    def _enqueue(self, sql: str, params: tuple) -> None:
        if self._queue is None:
            raise RuntimeError("AuditStore is not open")
        self._queue.put_nowait((sql, params))

    async def _writer(self) -> None:
        assert self._queue is not None
        while True:
            batch = [await self._queue.get()]
            while len(batch) < MAX_BATCH and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await asyncio.to_thread(self._write_batch, batch)
            except Exception as exc:  # noqa: BLE001 — the writer must survive
                logger.error("audit_store_write_failed", error=str(exc)[:200], batch=len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _write_batch(self, batch: list[tuple[str, tuple]]) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN")
            for sql, params in batch:
                try:
                    self._conn.execute(sql, params)
                except sqlite3.Error as exc:  # one bad row must not lose the rest
                    logger.error("audit_store_statement_failed", error=str(exc)[:200], sql=sql[:60])
            self._conn.execute("COMMIT")

    async def _read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def run() -> T:
            with self._lock:
                assert self._conn is not None
                return fn(self._conn)

        return await asyncio.to_thread(run)

    # ── audit events ────────────────────────────────────────────────
    def append_event(
        self,
        event_type: str,
        summary: str,
        *,
        actor: str | None = None,
        order_id: str | None = None,
        venue_id: str | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> AuditEvent:
        self._seq += 1
        event = AuditEvent(
            seq=self._seq, ts=ts or datetime.now(UTC), event_type=event_type, actor=actor,
            order_id=order_id, venue_id=venue_id, symbol=symbol, summary=summary,
            payload=payload or {},
        )
        self._enqueue(
            "INSERT INTO audit_events (seq, ts, event_type, actor, order_id, venue_id, symbol, summary, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.seq, _iso(event.ts), event_type, actor, order_id, venue_id, symbol, summary,
             json.dumps(event.payload, default=str)),
        )
        return event

    async def query_events(
        self,
        *,
        order_id: str | None = None,
        event_type: str | None = None,
        venue_id: str | None = None,
        before_seq: int | None = None,
        limit: int = 100,
        ascending: bool = False,
    ) -> list[AuditEvent]:
        where, params = [], []
        if order_id:
            where.append("order_id = ?")
            params.append(order_id)
        if event_type:
            types = [t.strip() for t in event_type.split(",") if t.strip()]
            where.append(f"event_type IN ({','.join('?' * len(types))})")
            params.extend(types)
        if venue_id:
            where.append("venue_id = ?")
            params.append(venue_id)
        if before_seq:
            where.append("seq < ?")
            params.append(before_seq)
        sql = "SELECT seq, ts, event_type, actor, order_id, venue_id, symbol, summary, payload FROM audit_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY seq {'ASC' if ascending else 'DESC'} LIMIT ?"
        params.append(limit)

        rows = await self._read(lambda c: c.execute(sql, params).fetchall())
        return [
            AuditEvent(
                seq=r[0], ts=datetime.fromisoformat(r[1]), event_type=r[2], actor=r[3],
                order_id=r[4], venue_id=r[5], symbol=r[6], summary=r[7], payload=json.loads(r[8]),
            )
            for r in rows
        ]

    async def recent_payloads(self, event_type: str, limit: int) -> list[dict[str, Any]]:
        """Payloads of the latest ``limit`` events of a type, oldest first."""
        rows = await self._read(lambda c: c.execute(
            "SELECT payload FROM audit_events WHERE event_type = ? ORDER BY seq DESC LIMIT ?",
            (event_type, limit),
        ).fetchall())
        return [json.loads(r[0]) for r in reversed(rows)]

    # ── orders ──────────────────────────────────────────────────────
    def upsert_order(self, order: Order) -> None:
        self._enqueue(
            "INSERT INTO orders (id, created_at, updated_at, symbol, side, status, submitted_by, snapshot)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at,"
            " status = excluded.status, snapshot = excluded.snapshot",
            (str(order.id), _iso(order.created_at), _iso(order.updated_at), order.symbol,
             order.side.value, order.status.value, order.submitted_by, order.model_dump_json()),
        )

    async def load_orders(self, limit: int = 500) -> list[Order]:
        """The most recent ``limit`` orders, oldest first."""
        rows = await self._read(lambda c: c.execute(
            "SELECT snapshot FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall())
        return [Order.model_validate_json(r[0]) for r in reversed(rows)]

    # ── fills ───────────────────────────────────────────────────────
    def record_fill(self, fill: FillRecord) -> None:
        self._enqueue(
            "INSERT OR REPLACE INTO fills (id, ts, order_id, venue_id, symbol, side, quantity, price,"
            " fee, fee_asset, fee_usdt, exchange_order_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fill.id, _iso(fill.ts), fill.order_id, fill.venue_id, fill.symbol, fill.side.value,
             fill.quantity, fill.price, fill.fee, fill.fee_asset, fill.fee_usdt, fill.exchange_order_id),
        )

    async def load_fills(self) -> list[FillRecord]:
        rows = await self._read(lambda c: c.execute(
            "SELECT id, ts, order_id, venue_id, symbol, side, quantity, price, fee, fee_asset,"
            " fee_usdt, exchange_order_id FROM fills ORDER BY ts"
        ).fetchall())
        return [
            FillRecord(
                id=r[0], ts=datetime.fromisoformat(r[1]), order_id=r[2], venue_id=r[3], symbol=r[4],
                side=r[5], quantity=r[6], price=r[7], fee=r[8], fee_asset=r[9], fee_usdt=r[10],
                exchange_order_id=r[11],
            )
            for r in rows
        ]

    # ── baselines ───────────────────────────────────────────────────
    def save_baseline(self, baseline: VenueBaseline) -> None:
        self._enqueue(
            "INSERT OR REPLACE INTO baselines (venue_id, ts, set_by, balances, marks) VALUES (?, ?, ?, ?, ?)",
            (baseline.venue_id, _iso(baseline.ts), baseline.set_by,
             json.dumps(baseline.balances), json.dumps(baseline.marks)),
        )

    async def load_baselines(self) -> dict[str, VenueBaseline]:
        """The active (latest) baseline of every venue. Older ones stay as history."""
        rows = await self._read(lambda c: c.execute(
            "SELECT venue_id, ts, set_by, balances, marks FROM baselines b"
            " WHERE ts = (SELECT MAX(ts) FROM baselines WHERE venue_id = b.venue_id)"
        ).fetchall())
        return {
            r[0]: VenueBaseline(
                venue_id=r[0], ts=datetime.fromisoformat(r[1]), set_by=r[2],
                balances=json.loads(r[3]), marks=json.loads(r[4]),
            )
            for r in rows
        }

    # ── algo (parent) orders ────────────────────────────────────────
    def upsert_algo(self, algo: TwapState) -> None:
        self._enqueue(
            "INSERT INTO algos (id, kind, created_at, updated_at, status, snapshot) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at,"
            " status = excluded.status, snapshot = excluded.snapshot",
            (algo.id, algo.kind, _iso(algo.created_at), _iso(algo.updated_at), algo.status.value,
             algo.model_dump_json()),
        )

    async def load_algos(self, limit: int = 200) -> list[TwapState]:
        """The most recent ``limit`` algo orders, oldest first."""
        rows = await self._read(lambda c: c.execute(
            "SELECT snapshot FROM algos ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall())
        return [TwapState.model_validate_json(r[0]) for r in reversed(rows)]

    # ── bots and the practice lab (Phase C) ─────────────────────────
    def upsert_bot(self, bot: BotState) -> None:
        snapshot = bot.model_copy(update={"pocket": None, "value_usdt": None, "unrealized_usdt": None,
                                          "pnl_usdt": None, "pnl_market_usdt": None})
        self._enqueue(
            "INSERT INTO bots (id, kind, created_at, updated_at, status, snapshot) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at,"
            " status = excluded.status, snapshot = excluded.snapshot",
            (bot.id, bot.kind.value, _iso(bot.created_at), _iso(bot.updated_at), bot.status.value,
             snapshot.model_dump_json()),
        )

    async def load_bots(self, limit: int = 500) -> list[BotState]:
        """The most recent ``limit`` bots, oldest first."""
        rows = await self._read(lambda c: c.execute(
            "SELECT snapshot FROM bots ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall())
        return [BotState.model_validate_json(r[0]) for r in reversed(rows)]

    def upsert_lab_sample(self, sample: LabSample) -> None:
        self._enqueue(
            "INSERT INTO lab_samples (id, kind, bot_id, symbol, opened_at, closed_at, snapshot)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET closed_at = excluded.closed_at, snapshot = excluded.snapshot",
            (sample.id, sample.kind.value, sample.bot_id, sample.symbol, _iso(sample.opened_at),
             _iso(sample.closed_at) if sample.closed_at else None, sample.model_dump_json()),
        )

    async def load_lab_samples(self, limit: int = 50_000) -> list[LabSample]:
        """The most recent ``limit`` lab samples, oldest first."""
        rows = await self._read(lambda c: c.execute(
            "SELECT snapshot FROM lab_samples ORDER BY opened_at DESC LIMIT ?", (limit,)
        ).fetchall())
        return [LabSample.model_validate_json(r[0]) for r in reversed(rows)]

    # ── key/value state (kill switch, risk limits) ──────────────────
    def kv_set(self, key: str, value: dict[str, Any]) -> None:
        self._enqueue(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, default=str), _iso(datetime.now(UTC))),
        )

    async def kv_get(self, key: str) -> dict[str, Any] | None:
        row = await self._read(lambda c: c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone())
        return json.loads(row[0]) if row else None
