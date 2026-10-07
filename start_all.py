"""
Start ApnaSOR locally: API gateway (+ frontend dev server), and the 3-node
RAFT cluster first when RAFT_ENABLED=true in .env.

Run from the project root:
    python start_all.py               # gateway + frontend
    python start_all.py --no-frontend # gateway only

Start: the settings are checked, then the ports this start needs (8000; 5173 for
the frontend; 9001-9003 with RAFT). A port in use stops the start with what to
do — a second copy would share the first one's audit database (uvicorn runs the
gateway's start-up BEFORE it takes its port, so it writes there before failing).
With RAFT on, the nodes start first and the gateway waits until they have a leader.

Stop: Ctrl+C stops everything, in order — the gateway cleanly first (running
TWAPs paused, "Gateway stopped" audited, arbitrage statistics saved), then the
frontend's whole process tree and the RAFT nodes. Ctrl+C again stops everything
at once. Every child runs in its own process group, so a Ctrl+C reaches only
this script, which decides the order. The gateway or frontend exiting by itself
stops the rest the same way.

A RAFT node that dies (e.g. the leader, killed for the failover demo) is
restarted after --raft-restart-delay seconds, like a real supervisor would;
the rest keeps running.

The gateway connects to the exchanges configured in .env (EXCHANGE_MODE,
ENABLED_EXCHANGES, *_API_KEY). The old venue simulators are retired — see
the archive/venue-simulators branch.

Options, decisions and how each part works: project-info/START_STOP_GUIDE.md.
"""

import argparse
import asyncio
import csv
import errno
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"

GATEWAY_PORT = 8000
FRONTEND_PORT = 5173  # frontend/vite.config.ts; Vite listens on "localhost" (IPv6 ::1 on Windows)
GATEWAY_STOP_S = 60.0  # a clean stop lets an order already at an exchange finish (rule 6); usually ~2 s
NODE_STOP_S = 5.0
LEADER_POLL_S = 0.2


# ── ports: refuse a second start ────────────────────────────────────
def port_free(host: str, port: int) -> bool:
    """Would a server's bind on ``host:port`` succeed right now?

    Same socket options as asyncio's servers (uvicorn, our RAFT nodes):
    SO_REUSEADDR only outside Windows — on Windows it would let us bind over a
    live server.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError:  # no IPv6 on this machine: nothing can listen there either
        return True
    with sock:
        if not WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return exc.errno == errno.EADDRNOTAVAIL  # e.g. IPv6 switched off: not a conflict
    return True


def needed_ports(frontend: bool, raft_nodes: dict[int, tuple[str, int]]) -> list[tuple[str, str, int]]:
    """(what, host, port) for every address this start will listen on."""
    ports = [("gateway", "127.0.0.1", GATEWAY_PORT)]
    if frontend:
        ports += [("frontend", "127.0.0.1", FRONTEND_PORT), ("frontend", "::1", FRONTEND_PORT)]
    ports += [(f"RAFT node {i}", host, port) for i, (host, port) in raft_nodes.items()]
    return ports


def busy_ports(needed: list[tuple[str, str, int]]) -> dict[int, str]:
    """port -> what needs it, for each needed port something else already holds."""
    return {port: what for what, host, port in needed if not port_free(host, port)}


def listeners(netstat_output: str) -> dict[int, int]:
    """port -> pid of the process listening there, from ``netstat -ano`` (Windows).

    A listening socket is the one without a remote end (0.0.0.0:0 / [::]:0):
    that works in every Windows language, while the word LISTENING is translated.
    """
    found: dict[int, int] = {}
    for line in netstat_output.splitlines():
        parts = line.split()
        if len(parts) != 5 or parts[0] != "TCP" or parts[2] not in ("0.0.0.0:0", "[::]:0"):
            continue
        port = parts[1].rsplit(":", 1)[-1]
        if port.isdigit() and parts[4].isdigit():
            found.setdefault(int(port), int(parts[4]))
    return found


def port_owners(ports: list[int]) -> dict[int, tuple[int, str]]:
    """port -> (pid, program) for the message. Windows only, best effort ({} when unknown)."""
    if not WINDOWS:
        return {}
    try:
        output = "".join(subprocess.run(["netstat", "-ano", "-p", proto], capture_output=True, text=True,
                                        timeout=10).stdout for proto in ("TCP", "TCPv6"))
    except (OSError, subprocess.SubprocessError):
        return {}
    pids = listeners(output)
    return {port: (pids[port], process_name(pids[port])) for port in ports if port in pids}


def process_name(pid: int) -> str:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "a program"
    row = next(csv.reader([out.strip()]), [])
    return row[0] if len(row) > 1 and row[1] == str(pid) else "a program"


def ports_in_use_message(busy: dict[int, str], owners: dict[int, tuple[int, str]]) -> str:
    lines = []
    for port, what in sorted(busy.items()):
        owner = f" - by {owners[port][1]}, pid {owners[port][0]}" if port in owners else ""
        lines.append(f"  Port {port} ({what}) is already in use{owner}.")
    pids = sorted({owners[port][0] for port in busy if port in owners})
    if pids:
        stop = "taskkill " + " ".join(f"/PID {pid}" for pid in pids) + " /T /F"
    else:
        stop = "find it with  lsof -i :<port>  then  kill <pid>" if not WINDOWS else "close it in Task Manager"
    lines += [
        "",
        "  ApnaSOR is probably running already - or an earlier start did not stop cleanly.",
        "  A second copy would share the first one's audit database, so nothing was started.",
        "    - Running in another window? Use that one, or press Ctrl+C there first.",
        f"    - Left over from an earlier start? Stop it:  {stop}",
    ]
    return "\n".join(lines)


# ── RAFT: wait for a leader before the gateway asks ─────────────────
def leader_of(states: dict[int, dict[str, Any] | None], mode: str) -> int | None:
    """The leader, once a majority of the nodes answers and one of them leads."""
    valid = {i: s for i, s in states.items() if s and s.get("mode") == mode}
    if len(valid) * 2 <= len(states):
        return None
    leaders = [i for i, s in valid.items() if s.get("role") == "LEADER"]
    return max(leaders, key=lambda i: valid[i].get("term", 0)) if leaders else None


async def wait_for_leader(client: Any, mode: str, timeout_s: float,
                          poll_s: float = LEADER_POLL_S) -> tuple[int | None, dict[int, dict[str, Any] | None]]:
    """Ask the nodes every ``poll_s`` until there is a leader, or ``timeout_s`` passed (leader None)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        states = await client.states()
        leader = leader_of(states, mode)
        if leader is not None or loop.time() >= deadline:
            return leader, states
        await asyncio.sleep(poll_s)


def report_leader(settings: Any, nodes: dict[int, tuple[str, int]], wait_s: float) -> None:
    from backend.services.consensus.client import ConsensusClient
    from backend.services.consensus.wire import MessageSigner

    client = ConsensusClient(nodes, signer=MessageSigner(settings.raft_shared_secret.get_secret_value()))
    started = time.monotonic()
    leader, states = asyncio.run(wait_for_leader(client, settings.exchange_mode, wait_s))
    up = [i for i, s in states.items() if s and s.get("mode") == settings.exchange_mode]
    if leader is not None:
        print(f"  RAFT      leader: node {leader} (term {states[leader]['term']}), {len(up)} of {len(nodes)} nodes up"
              f" - after {time.monotonic() - started:.1f} s")
    else:
        print(f"  RAFT      no leader after {wait_s:g} s (answering: {', '.join(map(str, up)) or 'none'}) - starting "
              "the gateway anyway: engaging the kill switch / blacklisting still work at once, releasing them "
              "and limit changes wait for a majority")


# ── child processes ─────────────────────────────────────────────────
def spawn(cmd: list[str], **kwargs: Any) -> subprocess.Popen:
    """Start a child in its own process group.

    A Ctrl+C in this window then reaches only start_all.py (Windows: a new group
    ignores Ctrl+C; elsewhere: a new session is not the terminal's foreground
    group), which stops the children in order instead of all at the same instant.
    """
    if WINDOWS:
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **kwargs)
    return subprocess.Popen(cmd, start_new_session=True, **kwargs)


def ask_to_stop(proc: subprocess.Popen) -> bool:
    """Ask a child to shut down cleanly. False when the request can't be sent.

    Windows: Ctrl+Break to the child's process group — uvicorn and our RAFT
    nodes treat it like Ctrl+C, and the venv's python.exe launcher in front of
    each ignores it and waits for the real Python. Elsewhere: SIGINT (Ctrl+C).
    """
    if proc.poll() is not None:
        return True
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
    except OSError:  # Windows without a console (e.g. started by an IDE): Ctrl+Break can't be sent
        return False
    return True


def wait_for_exit(proc: subprocess.Popen, timeout_s: float) -> bool:
    """Wait until ``proc`` exits, at most ``timeout_s``. Polls: on Windows a Ctrl+C
    can't interrupt Popen.wait(), and the second Ctrl+C must work."""
    deadline = time.monotonic() + timeout_s
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop a child and everything it started, at once.

    Windows: taskkill /T follows the parent links (npm -> cmd -> node: killing
    only the first left Vite holding port 5173). Elsewhere: the child's whole
    process group (spawn() made one).
    """
    if WINDOWS:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    wait_for_exit(proc, 5)


def stop_all(gateway: subprocess.Popen | None, frontend: subprocess.Popen | None,
             raft: dict[int, subprocess.Popen]) -> None:
    """The gateway cleanly first, then the frontend's tree and the RAFT nodes. Ctrl+C again: all at once."""
    try:
        if gateway is not None and gateway.poll() is None:
            print("  Gateway   stopping cleanly (running TWAPs paused, state saved) ...", flush=True)
            started = time.monotonic()
            if ask_to_stop(gateway) and wait_for_exit(gateway, GATEWAY_STOP_S):
                print(f"  Gateway   stopped ({time.monotonic() - started:.1f} s)")
            else:
                kill_tree(gateway)
                print("  Gateway   did not stop in time - killed. The next start marks an order it was "
                      "executing as interrupted; reconciliation shows what the exchange did.")
        if frontend is not None:
            kill_tree(frontend)
            print("  Frontend  stopped (with every process it started)")
        if raft:
            for proc in raft.values():
                ask_to_stop(proc)
            for proc in raft.values():
                if not wait_for_exit(proc, NODE_STOP_S):
                    kill_tree(proc)
            print(f"  RAFT      nodes {', '.join(map(str, raft))} stopped")
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a third Ctrl+C must not interrupt this
        print("\n  Second Ctrl+C - stopping everything at once.")
        for proc in (gateway, frontend, *raft.values()):
            if proc is not None:
                kill_tree(proc)
    print("  Done.")


def supervise(gateway: subprocess.Popen, frontend: subprocess.Popen | None, raft: dict[int, subprocess.Popen],
              start_node: Callable[[int], subprocess.Popen], restart_delay: float) -> int:
    """Watch the children until the gateway or the frontend exits; restart dead RAFT nodes."""
    dead_since: dict[int, float] = {}
    main_children = [("Gateway", gateway)] + ([("Frontend", frontend)] if frontend is not None else [])
    while True:
        for name, proc in main_children:
            if proc.poll() is not None:
                print(f"  {name} exited with code {proc.returncode} - stopping the rest")
                return 1
        for node_id, proc in raft.items():
            if proc.poll() is None:
                continue
            if node_id not in dead_since:
                dead_since[node_id] = time.monotonic()
                print(f"  RAFT node {node_id} exited (code {proc.returncode}); the other two carry on"
                      + (f" - restarting it in {restart_delay:g} s" if restart_delay else ""))
            elif restart_delay and time.monotonic() - dead_since[node_id] >= restart_delay:
                raft[node_id] = start_node(node_id)
                del dead_since[node_id]
                print(f"  RAFT node {node_id} restarted (pid {raft[node_id].pid}); it catches up from the leader")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start ApnaSOR locally")
    parser.add_argument("--no-frontend", action="store_true", help="start only the API gateway")
    parser.add_argument("--raft-restart-delay", type=float, default=15.0,
                        help="seconds before a dead RAFT node is restarted (0 = never)")
    parser.add_argument("--raft-wait", type=float, default=20.0,
                        help="seconds to wait for a RAFT leader before starting the gateway anyway")
    args = parser.parse_args()

    from backend.gateway.config import settings  # reads .env (RAFT_ENABLED, RAFT_CLUSTER_NODES)
    from backend.gateway.middleware.auth import demo_logins, login_setup_problems

    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT

    print("=" * 60)
    print("  ApnaSOR - starting services")
    print("=" * 60)

    for problem in (settings.jwt_secret_problem(), settings.cors_problem(), *login_setup_problems()):
        if problem:
            print(f"\n  {problem}\n  Nothing was started.")
            sys.exit(1)
    if settings.raft_enabled and (problem := settings.raft_secret_problem()):
        print(f"\n  RAFT_ENABLED=true, but: {problem}\n  Nothing was started.")
        sys.exit(1)

    raft_nodes: dict[int, tuple[str, int]] = {}
    if settings.raft_enabled:
        from backend.services.consensus.client import parse_cluster

        raft_nodes = parse_cluster(settings.raft_cluster_nodes)
    npm = None if args.no_frontend else shutil.which("npm")
    if busy := busy_ports(needed_ports(npm is not None, raft_nodes)):
        print("\n" + ports_in_use_message(busy, port_owners(list(busy))) + "\n")
        sys.exit(1)

    def start_node(node_id: int) -> subprocess.Popen:
        return spawn([sys.executable, "-m", "backend.services.consensus.node", "--id", str(node_id)],
                     env=env, cwd=ROOT)

    raft: dict[int, subprocess.Popen] = {}
    gateway: subprocess.Popen | None = None
    frontend: subprocess.Popen | None = None
    code = 0
    try:
        # RAFT nodes first: the gateway syncs the kill switch / limits with them at start-up.
        for node_id, (host, port) in raft_nodes.items():
            raft[node_id] = start_node(node_id)
            print(f"  RAFT node {node_id} {host}:{port}  (pid {raft[node_id].pid})")
        if raft_nodes:
            report_leader(settings, raft_nodes, args.raft_wait)

        gateway = spawn(
            [sys.executable, "-m", "uvicorn", "backend.gateway.main:app", "--host", "127.0.0.1",
             "--port", str(GATEWAY_PORT)],
            env=env, cwd=ROOT,
        )
        print(f"  Gateway   http://127.0.0.1:{GATEWAY_PORT}   (Swagger: /docs)")
        if demo := demo_logins(settings):
            print(f"  Logins    DEMO for {', '.join(demo)} (password = user name) - "
                  "real passwords: python scripts/hash_password.py <user>")
        else:
            print("  Logins    real passwords only (no demo logins)")

        if not args.no_frontend:
            if npm is None:
                print("  Frontend  skipped - npm not found on PATH")
            else:
                frontend = spawn([npm, "run", "dev"], cwd=os.path.join(ROOT, "frontend"))
                print(f"  Frontend  http://localhost:{FRONTEND_PORT}")

        print()
        print("  Order books take a few seconds to sync after start-up.")
        print("  Press Ctrl+C to stop everything (cleanly, in order; Ctrl+C again = at once).")
        print()
        code = supervise(gateway, frontend, raft, start_node, args.raft_restart_delay)
    except KeyboardInterrupt:
        print("\n  Ctrl+C - stopping ApnaSOR (press Ctrl+C again to stop everything at once)")
    finally:
        stop_all(gateway, frontend, raft)
    sys.exit(code)


if __name__ == "__main__":
    main()
