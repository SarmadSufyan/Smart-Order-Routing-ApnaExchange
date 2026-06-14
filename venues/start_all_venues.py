"""
start_all_venues.py

Launches all 5 venue simulators in subprocesses (local dev mode).
Each subprocess gets its own env vars and Python random state.

Usage:
  cd venues
  python start_all_venues.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

VENUES = [
    {
        "VENUE_ID": "V1",
        "VENUE_NAME": "AlphaExchange",
        "VENUE_PORT": "8001",
        "VENUE_PROFILE": "alpha_exchange",
        "VENUE_DEGRADED": "false",
    },
    {
        "VENUE_ID": "V2",
        "VENUE_NAME": "BetaLiquidity",
        "VENUE_PORT": "8002",
        "VENUE_PROFILE": "beta_liquidity",
        "VENUE_DEGRADED": "false",
    },
    {
        "VENUE_ID": "V3",
        "VENUE_NAME": "GammaMarkets",
        "VENUE_PORT": "8003",
        "VENUE_PROFILE": "gamma_markets",
        "VENUE_DEGRADED": "true",     # Starts degraded - demo target
    },
    {
        "VENUE_ID": "V4",
        "VENUE_NAME": "DeltaPrime",
        "VENUE_PORT": "8004",
        "VENUE_PROFILE": "delta_prime",
        "VENUE_DEGRADED": "false",
    },
    {
        "VENUE_ID": "V5",
        "VENUE_NAME": "EpsilonPool",
        "VENUE_PORT": "8005",
        "VENUE_PROFILE": "epsilon_pool",
        "VENUE_DEGRADED": "false",
    },
]


def main() -> None:
    print("\n" + "=" * 60)
    print("  DEIRCP Venue Simulator - Starting 5 venues")
    print("=" * 60 + "\n")

    processes: list[subprocess.Popen] = []

    for venue in VENUES:
        env = os.environ.copy()
        env.update(venue)

        port = venue["VENUE_PORT"]
        vid = venue["VENUE_ID"]
        name = venue["VENUE_NAME"]
        degraded = " [DEGRADED]" if venue["VENUE_DEGRADED"] == "true" else ""

        cmd = [
            sys.executable, "-m", "uvicorn",
            "server.venue_app:app",
            "--host", "0.0.0.0",
            "--port", port,
            "--log-level", "warning",
        ]

        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        processes.append(proc)
        print(f"  +  {vid} - {name:20s} http://localhost:{port}/docs{degraded}")
        time.sleep(0.5)

    print(f"\n  All 5 venues started.")
    print(f"  V3 is running in DEGRADED mode (auto-blacklist target).\n")
    print(f"  Quick test:  curl http://localhost:8001/quote?symbol=AAPL")
    print(f"  Full docs:   http://localhost:8001/docs\n")
    print(f"  Press Ctrl+C to stop all venues.\n")

    def shutdown(sig, frame):
        print("\n\n  Stopping all venues...")
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                p.kill()
        print("  All venues stopped.\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    # Stream output from all processes
    try:
        while True:
            for i, proc in enumerate(processes):
                if proc.poll() is not None:
                    vid = VENUES[i]["VENUE_ID"]
                    print(f"  !  {vid} exited with code {proc.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
