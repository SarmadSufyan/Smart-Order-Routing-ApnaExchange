"""
Start ApnaSOR locally: API gateway (+ frontend dev server), and the 3-node
RAFT cluster first when RAFT_ENABLED=true in .env.

Run from the project root:
    python start_all.py               # gateway + frontend
    python start_all.py --no-frontend # gateway only

A RAFT node that dies (e.g. the leader, killed for the failover demo) is
restarted after --raft-restart-delay seconds, like a real supervisor would;
the rest keeps running. The gateway or frontend exiting stops everything.

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
    parser.add_argument("--raft-restart-delay", type=float, default=15.0,
                        help="seconds before a dead RAFT node is restarted (0 = never)")
    args = parser.parse_args()

    from backend.gateway.config import settings  # reads .env (RAFT_ENABLED, RAFT_CLUSTER_NODES)
    from backend.gateway.middleware.auth import demo_logins, login_setup_problems

    processes: list[tuple[str, subprocess.Popen]] = []
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT

    print("=" * 60)
    print("  ApnaSOR - starting services")
    print("=" * 60)

    # RAFT nodes first: the gateway syncs the kill switch / limits with them at start-up.
    raft: dict[int, subprocess.Popen] = {}
    raft_dead_since: dict[int, float] = {}

    def start_node(node_id: int) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "backend.services.consensus.node", "--id", str(node_id)], env=env, cwd=ROOT,
        )

    for problem in (settings.jwt_secret_problem(), settings.cors_problem(), *login_setup_problems()):
        if problem:
            print(f"\n  {problem}\n  Nothing was started.")
            sys.exit(1)
    if settings.raft_enabled and (problem := settings.raft_secret_problem()):
        print(f"\n  RAFT_ENABLED=true, but: {problem}\n  Nothing was started.")
        sys.exit(1)
    if settings.raft_enabled:
        from backend.services.consensus.client import parse_cluster

        for node_id, (host, port) in parse_cluster(settings.raft_cluster_nodes).items():
            raft[node_id] = start_node(node_id)
            print(f"  RAFT node {node_id} {host}:{port}  (pid {raft[node_id].pid})")
        time.sleep(1.5)  # let them elect a leader before the gateway asks

    gateway = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.gateway.main:app", "--host", "127.0.0.1", "--port", "8000"],
        env=env,
        cwd=ROOT,
    )
    processes.append(("Gateway", gateway))
    print("  Gateway   http://127.0.0.1:8000   (Swagger: /docs)")
    if demo := demo_logins(settings):
        print(f"  Logins    DEMO for {', '.join(demo)} (password = user name) - "
              "real passwords: python scripts/hash_password.py <user>")
    else:
        print("  Logins    real passwords only (no demo logins)")

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
        for _, proc in processes + [(f"RAFT node {i}", p) for i, p in raft.items()]:
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
            for node_id, proc in raft.items():
                if proc.poll() is None:
                    continue
                if node_id not in raft_dead_since:
                    raft_dead_since[node_id] = time.monotonic()
                    print(f"  RAFT node {node_id} exited (code {proc.returncode}); the other two carry on"
                          + (f" - restarting it in {args.raft_restart_delay:g} s" if args.raft_restart_delay else ""))
                elif args.raft_restart_delay and time.monotonic() - raft_dead_since[node_id] >= args.raft_restart_delay:
                    raft[node_id] = start_node(node_id)
                    del raft_dead_since[node_id]
                    print(f"  RAFT node {node_id} restarted (pid {raft[node_id].pid}); it catches up from the leader")
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
