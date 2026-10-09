"""The bots and the practice lab at a glance, straight from the database - read-only, gateway running or not.

    python scripts/lab_status.py                       # this machine's database
    python scripts/lab_status.py --db <copy>.db        # e.g. a backup pulled from the lab server (gunzip it first)

Prints the fund, every bot (status, trades, profit at real-market fees), what the lab has collected, the
last trade, gateway restarts, and an ATTENTION list of what needs a person (a bot paused by a person or a
safety rule, no trades for hours while bots run, ...). The lab server's status (deploy.py status) shows it.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.gateway.config import settings  # noqa: E402
from backend.services.automation.lab import outcome_model  # noqa: E402
from backend.shared.models.automation import (  # noqa: E402
    BotState,
    BotStatus,
    FundState,
    LabSample,
    LabSampleKind,
    LotStatus,
)

QUIET_HOURS = 6  # running bots that made no trade for this long are worth a look


def ago(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "never"
    minutes = int((now - ts).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h ago"
    return f"{minutes / 1440:.1f} days ago"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=settings.resolved_database_path)
    db = Path(parser.parse_args().db)
    if not db.exists():
        print(f"No database at {db} yet.")
        return
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    now = datetime.now(UTC)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if "bots" not in tables:
        print("This database has no bots table yet (made before Phase C).")
        return

    bots = [BotState.model_validate_json(r[0]) for r in con.execute("select snapshot from bots order by created_at")]
    row = con.execute("select value from kv where key='automation_fund'").fetchone()
    fund = FundState.model_validate(json.loads(row[0])) if row else None
    samples = [LabSample.model_validate_json(r[0]) for r in con.execute("select snapshot from lab_samples")]
    attention: list[str] = []

    print(f"Database {db}  (read {now:%Y-%m-%d %H:%M} UTC)")
    if fund is not None:
        in_pockets = sum(p.cash_usdt for p in fund.pockets.values())
        print(f"Fund: ${fund.allocated_usdt:,.2f} set aside - ${fund.unassigned_usdt:,.2f} unassigned, "
              f"${in_pockets:,.2f} cash in bots' pockets (the rest is in coins)")

    by_status = Counter(b.status.value for b in bots)
    print(f"\nBots: {len(bots)} ({', '.join(f'{n} {s.lower()}' for s, n in by_status.items()) or 'none'})")
    for b in bots:
        if b.status == BotStatus.STOPPED:
            continue
        pocket = fund.pockets.get(b.id) if fund else None
        closed = [lot for lot in b.lots if lot.status == LotStatus.CLOSED]
        realized = pocket.realized_market_usdt if pocket else sum(lot.profit_market_usdt or 0 for lot in closed)
        last = max((lot.closed_at or lot.opened_at for lot in b.lots), default=None)
        print(f"  {b.id:14} {b.label[:28]:28} {b.status.value:8} buys {b.stats.buys:3}  sells {b.stats.sells:3}  "
              f"open lots {len(b.open_lots):2}  profit at market fees {realized:+8.4f} USDT  last trade {ago(last, now)}")
        if b.status in (BotStatus.PAUSED, BotStatus.FAILED):
            print(f"  {'':14} {b.status.value.lower()}: {b.status_reason}")
            if not b.resume_after_restart:  # paused by a restart = resumes by itself on the lab server
                attention.append(f"{b.id} ({b.label}) is {b.status.value.lower()}: {b.status_reason}")
    stopped = by_status.get(BotStatus.STOPPED.value, 0)
    if stopped:
        print(f"  (+ {stopped} stopped)")

    kinds = Counter(s.kind.value for s in samples)
    closed_lots = sum(1 for s in samples if s.kind == LabSampleKind.CYCLE_LOT and s.closed_at)
    closed_trades = sum(1 for s in samples if s.kind == LabSampleKind.ADVISOR_TRADE and s.closed_at)
    first = min((s.opened_at for s in samples), default=None)
    print(f"\nLab: {kinds.get('EXECUTION', 0)} orders measured, {kinds.get('CYCLE_LOT', 0)} cycle lots "
          f"({closed_lots} closed), {kinds.get('ADVISOR_TRADE', 0)} advisor trades ({closed_trades} closed)"
          + (f", collecting since {first:%Y-%m-%d %H:%M} UTC ({(now - first).total_seconds() / 86400:.1f} days)"
             if first else ""))
    for kind, title in ((LabSampleKind.CYCLE_LOT, "cycle-lot model"), (LabSampleKind.ADVISOR_TRADE, "advisor model")):
        try:
            print(f"  {title}: {outcome_model(samples, kind, now)['status']}")
        except Exception as exc:  # noqa: BLE001 — a status line, never a crash
            print(f"  {title}: not available ({type(exc).__name__})")

    last_trade = parse_ts((con.execute("select max(ts) from audit_events where event_type in ('bot_buy','bot_sell')")
                           .fetchone() or [None])[0])
    starts = [parse_ts(r[0]) for r in con.execute(
        "select ts from audit_events where event_type='gateway_started' and ts >= ?",
        ((now - timedelta(hours=24)).isoformat(),))]
    print(f"\nLast bot trade: {ago(last_trade, now)}   Gateway starts in the last 24 h: {len(starts)}"
          + (f" (last {ago(max(starts), now)})" if starts else ""))
    running = by_status.get(BotStatus.RUNNING.value, 0)
    if running and (last_trade is None or now - last_trade > timedelta(hours=QUIET_HOURS)):
        attention.append(f"{running} bots run but none traded for over {QUIET_HOURS} h (quiet market, or look at "
                         "their waiting reason on the Bots & Lab page)")
    if len(starts) > 3:
        attention.append(f"the gateway started {len(starts)} times in 24 h - look at the log (deploy.py logs)")

    print("\nATTENTION:" if attention else "\nNothing needs attention.")
    for line in attention:
        print(f"  - {line}")


if __name__ == "__main__":
    main()
