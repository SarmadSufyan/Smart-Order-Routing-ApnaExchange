"""
Start all services for DEIRCP POC: 5 venues + API gateway.
Run from the project root: python start_all.py
"""

import os
import subprocess
import sys
import time
import signal

VENUES = [
    ("V1", "AlphaExchange", "8001", "alpha_exchange", "false"),
    ("V2", "BetaLiquidity", "8002", "beta_liquidity", "false"),
    ("V3", "GammaMarkets", "8003", "gamma_markets", "true"),
    ("V4", "DeltaPrime", "8004", "delta_prime", "false"),
    ("V5", "EpsilonPool", "8005", "epsilon_pool", "false"),
]

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    venues_dir = os.path.join(root, "venues")
    processes = []

    print("=" * 60)
    print("  DEIRCP - Starting all services")
    print("=" * 60)

    for vid, name, port, profile, degraded in VENUES:
        env = os.environ.copy()
        env["VENUE_ID"] = vid
        env["VENUE_NAME"] = name
        env["VENUE_PORT"] = port
        env["VENUE_PROFILE"] = profile
        env["VENUE_DEGRADED"] = degraded

        cmd = [
            sys.executable, "-m", "uvicorn",
            "server.venue_app:app",
            "--host", "127.0.0.1",
            "--port", port,
            "--log-level", "warning",
        ]

        proc = subprocess.Popen(cmd, env=env, cwd=venues_dir)
        processes.append(proc)
        deg = " [DEGRADED]" if degraded == "true" else ""
        print(f"  {vid} {name:20s} http://127.0.0.1:{port}{deg}")
        time.sleep(0.3)

    print()

    env = os.environ.copy()
    env["PYTHONPATH"] = root

    gateway_cmd = [
        sys.executable, "-m", "uvicorn",
        "backend.gateway.main:app",
        "--host", "127.0.0.1",
        "--port", "8000",
    ]

    gateway_proc = subprocess.Popen(gateway_cmd, env=env, cwd=root)
    processes.append(gateway_proc)
    print(f"  Gateway  http://127.0.0.1:8000")
    print(f"  Swagger  http://127.0.0.1:8000/docs")
    print()
    print(f"  Press Ctrl+C to stop all services.")
    print()

    def shutdown(sig, frame):
        print("\n  Stopping all services...")
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("  Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            for i, proc in enumerate(processes):
                if proc.poll() is not None:
                    if i < len(VENUES):
                        print(f"  {VENUES[i][0]} exited with code {proc.returncode}")
                    else:
                        print(f"  Gateway exited with code {proc.returncode}")
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
