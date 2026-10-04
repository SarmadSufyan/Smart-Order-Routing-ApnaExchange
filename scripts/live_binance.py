"""Live Binance prices in the terminal, straight from Binance's public WebSocket.

No API key is needed: this is Binance's PUBLIC market feed (the same real-market
feed ApnaSOR reads its order books from). Every time a trader somewhere places
or cancels an order at the top of the book, Binance pushes the new best prices
to us and the table below redraws.

Run (from the project root):
    .venv/Scripts/python.exe scripts/live_binance.py
    .venv/Scripts/python.exe scripts/live_binance.py --seconds 30   # stop by itself
Stop: Ctrl+C
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

import websockets

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

# Two live streams per coin, all in ONE connection:
#   <coin>usdt@bookTicker  -> best bid / best ask, pushed on every change (real time)
#   <coin>usdt@miniTicker  -> last price + 24-hour stats, pushed every second
STREAMS = [f"{c.lower()}usdt@bookTicker" for c in COINS] + [f"{c.lower()}usdt@miniTicker" for c in COINS]
URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(STREAMS)

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

prices: dict[str, dict] = {coin: {} for coin in COINS}  # the latest data for each coin
stats = {"total": 0, "window_start": time.monotonic(), "window_count": 0, "per_second": 0}


def money(value: float) -> str:
    """Big prices with 2 decimals, small ones (XRP) with 4."""
    return f"{value:,.2f}" if value >= 100 else f"{value:,.4f}"


def draw() -> None:
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    lines = [
        f"{BOLD}LIVE  Binance real market{RESET}   {now}   "
        f"{stats['per_second']} updates/s   {stats['total']:,} received   {DIM}(Ctrl+C to stop){RESET}",
        "",
        f"{'COIN':<6}{'BID (buyers pay)':>18}{'ASK (sellers want)':>20}{'SPREAD':>10}{'LAST':>14}{'24h':>9}",
        "-" * 77,
    ]
    for coin in COINS:
        p = prices[coin]
        if "bid" not in p:
            lines.append(f"{coin:<6}{DIM}waiting for the first update...{RESET}")
            continue
        colour = GREEN if p.get("move", 0) > 0 else RED if p.get("move", 0) < 0 else ""
        arrow = "▲" if p.get("move", 0) > 0 else "▼" if p.get("move", 0) < 0 else " "
        spread = p["ask"] - p["bid"]
        change = ""
        if "last" in p and p.get("open"):
            pct = (p["last"] / p["open"] - 1) * 100
            change = f"{GREEN if pct >= 0 else RED}{pct:+8.2f}%{RESET}"
        last = money(p["last"]) if "last" in p else "-"
        lines.append(
            f"{coin:<6}{colour}{money(p['bid']):>18}{money(p['ask']):>20}{RESET}"
            f"{money(spread):>10}{last:>14} {change} {colour}{arrow}{RESET}"
        )
    lines += [
        "",
        f"{DIM}BID = the highest price a buyer is offering right now; ASK = the lowest price a seller accepts.",
        f"Green ▲ / red ▼ = the price just moved up / down. Every change is a real trader's order on Binance.{RESET}",
    ]
    # Move the cursor to the top-left and redraw in place (no endless scrolling).
    sys.stdout.write("\033[H\033[J" + "\n".join(lines) + "\n")
    sys.stdout.flush()


def handle(message: str) -> None:
    msg = json.loads(message)
    stream, data = msg["stream"], msg["data"]
    coin = data["s"].removesuffix("USDT")  # "BTCUSDT" -> "BTC"
    p = prices[coin]
    if stream.endswith("@bookTicker"):
        bid, ask = float(data["b"]), float(data["a"])
        mid = (bid + ask) / 2
        if "mid" in p and mid != p["mid"]:
            p["move"] = 1 if mid > p["mid"] else -1
        p.update(bid=bid, ask=ask, mid=mid)
    else:  # miniTicker: last trade price and the price 24 hours ago
        p.update(last=float(data["c"]), open=float(data["o"]))

    stats["total"] += 1
    stats["window_count"] += 1
    if time.monotonic() - stats["window_start"] >= 1.0:  # count updates per second
        stats["per_second"] = stats["window_count"]
        stats["window_count"], stats["window_start"] = 0, time.monotonic()


async def main(seconds: float) -> None:
    os.system("")  # switches on colours in the classic Windows console
    deadline = time.monotonic() + seconds if seconds else None
    while True:  # reconnect if the internet drops
        try:
            async with websockets.connect(URL, ping_interval=20) as ws:
                last_draw = 0.0
                async for message in ws:
                    handle(message)
                    if time.monotonic() - last_draw >= 0.1:  # redraw at most 10x a second
                        draw()
                        last_draw = time.monotonic()
                    if deadline and time.monotonic() >= deadline:
                        return
        except (OSError, websockets.ConnectionClosed) as exc:
            print(f"\nConnection lost ({exc}); reconnecting in 2 s...")
            await asyncio.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Binance prices in the terminal")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = until Ctrl+C)")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.seconds))
    except KeyboardInterrupt:
        pass
    print("\nStopped.")
