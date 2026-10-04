"""Watch ApnaSOR's arbitrage detector (5c) live in the terminal.

Reads GET /api/arbitrage/status from the running gateway once a second and
shows, per coin, the best venue pair right now: how far apart the two books
are before fees (gross) and after both exchanges' taker fees (net). Below the
table: the statistics since the detector started counting, in plain English.
Detection only — nothing is traded.

Run (gateway running: python start_all.py):
    .venv/Scripts/python.exe scripts/arb_watch.py
    .venv/Scripts/python.exe scripts/arb_watch.py --seconds 30   # stop by itself
    .venv/Scripts/python.exe scripts/arb_watch.py --once         # print once (logs, reports)
Stop: Ctrl+C
"""

import argparse
import os
import sys
import time
from datetime import datetime

import httpx

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def login(http: httpx.Client) -> None:
    r = http.post("/api/auth/login", json={"username": "admin", "password": "admin"})  # POC login
    r.raise_for_status()
    http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def money(value: float) -> str:
    return f"{value:,.2f}" if value >= 100 else f"{value:,.4f}"


def coloured(bps: float, text: str) -> str:
    return f"{GREEN if bps > 0 else RED}{text}{RESET}"


def draw(status: dict, once: bool) -> None:
    names = status["venue_names"]
    now = datetime.now().strftime("%H:%M:%S")
    fees = ", ".join(f"{names.get(v, v)} {bps:g}" for v, bps in status["fees_bps"].items())
    lines = [
        f"{BOLD}ARBITRAGE DETECTOR{RESET}  real-market books, taker fees (bps): {fees}   {now}"
        + ("" if once else f"   {DIM}(Ctrl+C to stop){RESET}"),
        "",
        f"{'COIN':<10}{'BUY ON':<10}{'SELL ON':<10}{'BUY AT':>13}{'SELL AT':>13}{'GROSS':>9}{'FEES':>7}"
        f"{'NET':>9}{'SIZE':>12}{'CROSSED':>9}{'WINDOWS':>9}{'PROFIT':>8}",
        "-" * 119,
    ]
    for s in status["symbols"]:
        best, st = s["best"], s["stats"]
        if best is None:
            lines.append(f"{s['symbol']:<10}{DIM}waiting for quotes from two venues...{RESET}")
            continue
        crossed = f"{100 * st['crossed_scans'] / st['scans']:.0f}%" if st["scans"] else "-"
        lines.append(
            f"{s['symbol']:<10}{names.get(best['buy_venue'], best['buy_venue']):<10}"
            f"{names.get(best['sell_venue'], best['sell_venue']):<10}"
            f"{money(best['buy_price']):>13}{money(best['sell_price']):>13}"
            + coloured(best["gross_bps"], f"{best['gross_bps']:>+9.2f}")
            + f"{best['fee_bps']:>7.1f}"
            + coloured(best["net_bps"], f"{best['net_bps']:>+9.2f}")
            + f"{best['size']:>12.6g}{crossed:>9}{st['windows']:>9,}{st['profitable_windows']:>8,}"
        )
    lines += ["", status["summary"], ""]
    lines += [f"{DIM}{s['summary']}{RESET}" for s in status["symbols"]]
    lines += [
        "",
        f"{DIM}GROSS = best bid on the SELL venue minus best ask on the BUY venue, in bps of the buy price "
        "(> 0 = the two books are crossed).",
        "NET = what is left after paying both taker fees. WINDOWS = times a pair stayed crossed; "
        "PROFIT = windows still positive after fees.",
        f"{status['note']}{RESET}",
    ]
    sys.stdout.write(("" if once else "\033[H\033[J") + "\n".join(lines) + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the arbitrage detector live")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="gateway address")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = until Ctrl+C)")
    parser.add_argument("--once", action="store_true", help="print the status once and exit")
    args = parser.parse_args()
    os.system("")  # switches on colours in the classic Windows console
    sys.stdout.reconfigure(encoding="utf-8")
    deadline = time.monotonic() + args.seconds if args.seconds else None
    with httpx.Client(base_url=args.url, timeout=10) as http:
        try:
            login(http)
        except httpx.ConnectError:
            sys.exit(f"No gateway at {args.url} - start it first: python start_all.py")
        while True:
            r = http.get("/api/arbitrage/status")
            if r.status_code == 401:  # the login token expired: log in again
                login(http)
                continue
            r.raise_for_status()
            draw(r.json(), args.once)
            if args.once or (deadline and time.monotonic() >= deadline):
                return
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
