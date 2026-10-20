"""Your trading records for one month as CSV files - read-only, straight from an audit database.

    python scripts/export_records.py --month 2026-11
    python scripts/export_records.py --db data/real-server/backups/<backup>/apnasor-mainnet.db --month 2026-11

Writes, in data/exports/ (gitignored):
  fills-<month>.csv   every fill an exchange reported: time (UTC), exchange, symbol, side, quantity, price,
                      value in USDT, fee as charged and in USDT, our order id, the exchange's order id
  orders-<month>.csv  every order: time, who or what submitted it, symbol, side, type, quantity, filled,
                      average price, status, routing strategy
and prints a summary: per coin, bought and sold quantity and value, and the fees paid.

The records are the exchanges' own fill reports as the gateway recorded them (rule 4: reality wins); this script
only reads. For the real-money server, copy its database first:  python deploy/real-server/deploy.py backup-pull
(Phase D plan, D1-8). Not tax advice: a record to give to whoever does your accounts.
"""

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.exchanges.registry import EXCHANGE_CATALOG  # noqa: E402

EXCHANGES = {spec.venue_id: spec.name for spec in EXCHANGE_CATALOG.values()}
FILL_COLUMNS = ["time_utc", "exchange", "symbol", "side", "quantity", "price", "value_usdt", "fee", "fee_asset",
                "fee_usdt", "order_id", "exchange_order_id"]
ORDER_COLUMNS = ["time_utc", "submitted_by", "symbol", "side", "order_type", "quantity", "filled_quantity",
                 "avg_fill_price", "status", "strategy", "order_id"]


def month_bounds(month: str) -> tuple[str, str]:
    """``"2026-11"`` -> the ISO start of that month and of the next one (UTC). Raises ValueError on a bad month."""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def fill_rows(rows: list[tuple]) -> list[dict]:
    """Rows of the ``fills`` table (ts, order_id, venue_id, symbol, side, quantity, price, fee, fee_asset, fee_usdt,
    exchange_order_id) as CSV rows. Pure."""
    out = []
    for ts, order_id, venue, symbol, side, qty, price, fee, fee_asset, fee_usdt, xid in rows:
        out.append({"time_utc": ts, "exchange": EXCHANGES.get(venue, venue), "symbol": symbol, "side": side,
                    "quantity": qty, "price": price, "value_usdt": round(qty * price, 8), "fee": fee,
                    "fee_asset": fee_asset, "fee_usdt": fee_usdt, "order_id": order_id, "exchange_order_id": xid})
    return out


def order_rows(rows: list[tuple]) -> list[dict]:
    """Rows of the ``orders`` table (created_at, submitted_by, snapshot JSON) as CSV rows. Pure."""
    out = []
    for created, submitted_by, snapshot in rows:
        o = json.loads(snapshot)
        out.append({"time_utc": created, "submitted_by": submitted_by or o.get("submitted_by"),
                    "symbol": o.get("symbol"), "side": o.get("side"), "order_type": o.get("order_type"),
                    "quantity": o.get("quantity"), "filled_quantity": o.get("filled_quantity"),
                    "avg_fill_price": o.get("avg_fill_price"), "status": o.get("status"),
                    "strategy": o.get("strategy"), "order_id": o.get("id")})
    return out


def summarize(fills: list[dict]) -> dict[str, dict[str, float]]:
    """Per symbol: bought / sold quantity and value, fees in USDT. Pure."""
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for f in fills:
        s = out[f["symbol"]]
        side = "bought" if f["side"] == "BUY" else "sold"
        s[f"{side}_qty"] += f["quantity"]
        s[f"{side}_usdt"] += f["value_usdt"]
        s["fees_usdt"] += f["fee_usdt"] or 0.0
        s["fills"] += 1
    return {k: dict(v) for k, v in out.items()}


def default_db() -> Path:
    for mode in ("mainnet", "testnet"):
        if (path := ROOT / "data" / f"apnasor-{mode}.db").exists():
            return path
    return ROOT / "data" / "apnasor-testnet.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Your trading records for one month, as CSV (read-only).")
    parser.add_argument("--db", type=Path, default=None, help="the audit database (default: this machine's)")
    parser.add_argument("--month", default=f"{datetime.now(UTC):%Y-%m}", help="YYYY-MM (default: this month)")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "exports")
    args = parser.parse_args()
    db = args.db or default_db()
    if not db.exists():
        sys.exit(f"error: no database at {db}")
    try:
        start, end = month_bounds(args.month)
    except ValueError:
        sys.exit(f"error: --month must look like 2026-11, not {args.month!r}")

    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        fills = fill_rows(con.execute(
            "SELECT ts, order_id, venue_id, symbol, side, quantity, price, fee, fee_asset, fee_usdt, exchange_order_id "
            "FROM fills WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)).fetchall())
        orders = order_rows(con.execute(
            "SELECT created_at, submitted_by, snapshot FROM orders WHERE created_at >= ? AND created_at < ? "
            "ORDER BY created_at", (start, end)).fetchall())
    finally:
        con.close()

    args.out.mkdir(parents=True, exist_ok=True)
    for name, columns, rows in (("fills", FILL_COLUMNS, fills), ("orders", ORDER_COLUMNS, orders)):
        path = args.out / f"{name}-{args.month}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {len(rows)} {name}")

    print(f"\n{args.month} from {db.name}:")
    total_fees = 0.0
    for symbol, s in sorted(summarize(fills).items()):
        total_fees += s.get("fees_usdt", 0.0)
        print(f"  {symbol:9} {int(s['fills']):4} fills  bought {s.get('bought_qty', 0):g} (${s.get('bought_usdt', 0):,.2f})"
              f"  sold {s.get('sold_qty', 0):g} (${s.get('sold_usdt', 0):,.2f})  fees ${s.get('fees_usdt', 0):,.4f}")
    print(f"  fees in total: ${total_fees:,.4f}" if fills else "  no fills this month")


if __name__ == "__main__":
    main()
