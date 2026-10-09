"""Copy the gateway's databases safely while it runs, gzip them and keep the last N days.

    python scripts/backup_databases.py [--dest data/backups] [--keep-days 14]

Uses SQLite's online backup (a consistent copy even while the gateway writes), checks the copy
(PRAGMA quick_check) and gzips it into <dest>/<UTC date_time>/. Folders older than --keep-days are
deleted, but the newest three always stay (a machine that was off for weeks keeps its last copies).
The lab server runs it daily and before every release (deploy/lab-server); it works on a laptop too.
Copies the audit database (orders, fills, bots, the lab's samples) and the market history - never a
key file.
"""

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.gateway.config import PROJECT_ROOT, settings  # noqa: E402

STAMP = "%Y-%m-%d_%H%M%S"
ALWAYS_KEEP = 3


def backup_one(source: Path, folder: Path) -> tuple[str, int]:
    """One database -> <folder>/<name>.gz. Returns (name, compressed bytes)."""
    plain = folder / source.name
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(plain)
    try:
        src.backup(dst)
        verdict = dst.execute("PRAGMA quick_check").fetchone()[0]
        if verdict != "ok":
            raise RuntimeError(f"the copy of {source.name} failed its check: {verdict}")
    finally:
        dst.close()
        src.close()
    packed = folder / f"{source.name}.gz"
    with open(plain, "rb") as fin, gzip.open(packed, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    plain.unlink()
    return packed.name, packed.stat().st_size


def prune(dest: Path, keep_days: int, now: datetime) -> list[str]:
    """Delete backup folders older than keep_days, never the newest ALWAYS_KEEP."""
    folders = []
    for f in dest.iterdir():
        try:
            folders.append((datetime.strptime(f.name, STAMP).replace(tzinfo=UTC), f))
        except ValueError:
            continue  # not ours
    folders.sort(reverse=True)
    removed = []
    for when, folder in folders[ALWAYS_KEEP:]:
        if now - when > timedelta(days=keep_days):
            shutil.rmtree(folder)
            removed.append(folder.name)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dest", default=str(PROJECT_ROOT / "data" / "backups"))
    parser.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args()

    sources = [Path(p) for p in (settings.resolved_database_path, settings.resolved_market_history_path)
               if p != ":memory:" and Path(p).exists()]
    if not sources:
        print("nothing to back up: no database yet")
        return
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    while True:  # a folder of its own, even when two backups start in the same second
        now = datetime.now(UTC)
        folder = dest / now.strftime(STAMP)
        try:
            folder.mkdir()
            break
        except FileExistsError:
            time.sleep(1)
    try:
        for source in sources:
            name, size = backup_one(source, folder)
            print(f"{folder.name}/{name}  {size / 1e6:.1f} MB")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)  # never leave a half backup that looks complete
        raise
    for name in prune(dest, args.keep_days, now):
        print(f"removed old backup {name}")


if __name__ == "__main__":
    main()
