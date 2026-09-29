"""
Start ApnaSOR locally: API gateway (+ frontend dev server).

Run from the project root:
    python start_all.py               # gateway + frontend
    python start_all.py --no-frontend # gateway only

The gateway connects to the exchanges configured in .env (EXCHANGE_MODE,
ENABLED_EXCHANGES, *_API_KEY). The old venue simulators are retired — see
the archive/venue-simulators branch.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    parser = argparse.ArgumentParser(description="Start ApnaSOR locally")
    parser.add_argument("--no-frontend", action="store_true", help="start only the API gateway")
    args = parser.parse_args()

    processes: list[tuple[str, subprocess.Popen]] = []
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT

    print("=" * 60)
    print("  ApnaSOR - starting services")
    print("=" * 60)

    gateway = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.gateway.main:app", "--host", "127.0.0.1", "--port", "8000"],
        env=env,
        cwd=ROOT,
    )
    processes.append(("Gateway", gateway))
    print("  Gateway   http://127.0.0.1:8000   (Swagger: /docs)")

    if not args.no_frontend:
        npm = shutil.which("npm")
        if npm is None:
            print("  Frontend  skipped - npm not found on PATH")
        else:
            frontend = subprocess.Popen([npm, "run", "dev"], cwd=os.path.join(ROOT, "frontend"))
            processes.append(("Frontend", frontend))
            print("  Frontend  http://localhost:5173")

    print()
    print("  Order books take a few seconds to sync after start-up.")
    print("  Press Ctrl+C to stop everything.")
    print()

    def shutdown(*_: object) -> None:
        print("\n  Stopping services...")
        for _, proc in processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()
        print("  Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    print(f"  {name} exited with code {proc.returncode}")
                    shutdown()
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
