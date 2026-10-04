"""Durable RAFT state: term, vote, log and commit index — one SQLite file per node.

RAFT is only safe if a node remembers its term, its vote and its log across
a crash (otherwise it could vote twice in a term, or "forget" an entry a
majority counted on). Every write here is a committed SQLite transaction
(``synchronous=FULL``) before the method returns, and ``RaftCore`` writes
before it sends anything that depends on the write.
"""

import json
import sqlite3
from pathlib import Path

from .core import Entry

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (idx INTEGER PRIMARY KEY, term INTEGER NOT NULL, command TEXT NOT NULL);
"""


class SqliteStorage:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._db = sqlite3.connect(self.path, isolation_level=None)  # explicit transactions below
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, items: dict[str, str]) -> None:
        with self._db:
            self._db.execute("BEGIN")
            self._db.executemany("INSERT INTO meta(key, value) VALUES(?, ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", items.items())

    def load(self) -> tuple[int, int | None, list[Entry], int]:
        term = int(self._meta("term") or 0)
        voted = self._meta("voted_for")
        commit = int(self._meta("commit") or 0)
        rows = self._db.execute("SELECT idx, term, command FROM log ORDER BY idx").fetchall()
        log = [Entry(t, json.loads(c)) for _, t, c in rows]
        if any(idx != i + 1 for i, (idx, _, _) in enumerate(rows)):
            raise RuntimeError(f"RAFT log in {self.path} has a gap; refusing to start")
        return term, (int(voted) if voted not in (None, "") else None), log, commit

    def save_term_vote(self, term: int, voted_for: int | None) -> None:
        self._set_meta({"term": str(term), "voted_for": "" if voted_for is None else str(voted_for)})

    def save_commit(self, commit_index: int) -> None:
        self._set_meta({"commit": str(commit_index)})

    def append(self, first_index: int, entries: list[Entry]) -> None:
        with self._db:
            self._db.execute("BEGIN")
            self._db.executemany(
                "INSERT OR REPLACE INTO log(idx, term, command) VALUES(?, ?, ?)",
                [(first_index + i, e.term, json.dumps(e.command, separators=(",", ":")))
                 for i, e in enumerate(entries)],
            )

    def truncate_from(self, index: int) -> None:
        with self._db:
            self._db.execute("BEGIN")
            self._db.execute("DELETE FROM log WHERE idx >= ?", (index,))


class MemoryStorage:
    """Same interface, in memory — for tests (survives a simulated crash if reused)."""

    def __init__(self) -> None:
        self.term, self.voted_for, self.log, self.commit = 0, None, [], 0

    def load(self) -> tuple[int, int | None, list[Entry], int]:
        return self.term, self.voted_for, list(self.log), self.commit

    def save_term_vote(self, term: int, voted_for: int | None) -> None:
        self.term, self.voted_for = term, voted_for

    def save_commit(self, commit_index: int) -> None:
        self.commit = commit_index

    def append(self, first_index: int, entries: list[Entry]) -> None:
        del self.log[first_index - 1:]
        self.log.extend(entries)

    def truncate_from(self, index: int) -> None:
        del self.log[index - 1:]
