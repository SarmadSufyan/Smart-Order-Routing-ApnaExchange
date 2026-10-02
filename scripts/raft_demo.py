"""RAFT failover demo: start 3 nodes, kill the leader, show nothing is lost.

Two ways to run it (from the project root):

  Standalone — its own 3 nodes on ports 9201-9203 and a throwaway data folder
  (data/raft-demo/), no exchange or gateway needed:
      .venv/Scripts/python.exe scripts/raft_demo.py

  Against the running system — the gateway and its cluster started by
  `python start_all.py` with RAFT_ENABLED=true in .env. Logs in as the POC
  admin, optionally runs a small TWAP on testnet, kills the RAFT leader in
  the middle of it and shows trading and the risk controls carry on:
      .venv/Scripts/python.exe scripts/raft_demo.py --gateway --twap 0.0006

Every step prints what happened and how long it took.
"""

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.consensus.client import (  # noqa: E402
    ConsensusClient,
    ConsensusUnavailableError,
    parse_cluster,
)

DEMO_NODES = "127.0.0.1:9201,127.0.0.1:9202,127.0.0.1:9203"
DEMO_DIR = ROOT / "data" / "raft-demo"
T0 = time.perf_counter()


def say(text: str) -> None:
    print(f"[{time.perf_counter() - T0:6.2f}s] {text}", flush=True)


def summary(state: dict) -> str:
    ks = state.get("kill_switch") or {}
    limits = state.get("risk_limits") or {}
    return (f"kill switch {'ENGAGED' if ks.get('active') else 'off'}"
            f" | max order ${limits.get('max_order_notional', '-')}"
            f" | blacklist {sorted(state.get('blacklist') or {}) or 'none'}"
            f" | log index {state.get('applied_index')}")


async def wait_leader(client: ConsensusClient, exclude: int | None = None, timeout: float = 10.0):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        states = await client.states()
        leaders = [(i, s) for i, s in states.items() if s and s["role"] == "LEADER" and i != exclude]
        if leaders:
            i, s = max(leaders, key=lambda x: x[1]["term"])
            return i, s, (time.perf_counter() - start) * 1000
        await asyncio.sleep(0.02)
    raise SystemExit("no leader elected in time")


def show_nodes(states: dict) -> None:
    for i, s in sorted(states.items()):
        if s is None:
            say(f"   node {i}: DOWN")
        else:
            say(f"   node {i}: {s['role']:<9} term {s['term']}  pid {s['pid']}  -> {summary(s['state'])}")


def kill(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)  # on Windows: TerminateProcess = an instant crash, no clean shutdown


# ── standalone ──────────────────────────────────────────────────────────
async def standalone() -> None:
    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    nodes = parse_cluster(DEMO_NODES)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    procs: dict[int, subprocess.Popen] = {}

    def start(i: int) -> None:
        procs[i] = subprocess.Popen(
            [sys.executable, "-m", "backend.services.consensus.node", "--id", str(i),
             "--nodes", DEMO_NODES, "--data-dir", str(DEMO_DIR)],
            env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    client = ConsensusClient(nodes, write_timeout_s=3.0)
    try:
        say("Starting 3 RAFT node processes (separate OS processes, each with its own SQLite log)")
        for i in nodes:
            start(i)
        leader, s, ms = await wait_leader(client)
        say(f"Leader elected: node {leader} (term {s['term']}), {ms / 1000:.1f} s after launch (mostly Python start-up)")

        say("Writing risk controls through the leader (each waits for a majority):")
        for what, command in [
            ("engage kill switch", {"op": "set_kill_switch", "state": {"active": True, "reason": "demo",
                                                                        "activated_by": "risk_mgr"}}),
            ("max order $500", {"op": "set_risk_limits", "limits": {"max_order_notional": 500.0}}),
            ("blacklist V5 (OKX)", {"op": "set_blacklist", "venue_id": "V5", "on": True, "actor": "risk_mgr"}),
        ]:
            t = time.perf_counter()
            reply = await client.propose(command)
            say(f"   {what:<22} committed at log index {reply['index']} in {(time.perf_counter() - t) * 1000:.0f} ms")
        await asyncio.sleep(0.3)
        show_nodes(await client.states())

        say(f"KILLING the leader, node {leader} (pid {procs[leader].pid}) - a hard crash")
        kill(procs[leader].pid)
        new, s, ms = await wait_leader(client, exclude=leader)
        say(f"New leader: node {new} (term {s['term']}), elected {ms:.0f} ms after the crash")
        say(f"   its state: {summary(s['state'])}  <- nothing lost")

        t = time.perf_counter()
        reply = await client.propose({"op": "set_kill_switch", "state": {"active": False}})
        say(f"Release the kill switch with 2 of 3 nodes: committed at index {reply['index']} "
            f"in {(time.perf_counter() - t) * 1000:.0f} ms")

        say(f"Restarting node {leader}: it reloads its log from disk and catches up from the new leader")
        start(leader)
        await asyncio.sleep(1.5)
        states = await client.states()
        show_nodes(states)
        same = len({str(st["state"]) for st in states.values() if st}) == 1
        say(f"All 3 nodes hold the same state: {'YES' if same else 'NO'}")

        others = [i for i in nodes if i != new]
        say(f"Now killing TWO nodes ({others}): one node alone is not a majority")
        for i in others:
            kill(procs[i].pid)
        await asyncio.sleep(0.5)
        try:
            await ConsensusClient(nodes, write_timeout_s=2.0).propose(
                {"op": "set_risk_limits", "limits": {"max_order_notional": 1e9}})
            say("   !! a write committed without a majority (this must never happen)")
        except ConsensusUnavailableError as exc:
            say(f"   write refused, as it must be: {exc.message}")
        survivor = (await client.states())[new]
        say(f"   the survivor still serves the last agreed state: {summary(survivor['state'])}")
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        say("Demo nodes stopped")


# ── against the running gateway ─────────────────────────────────────────
async def against_gateway(base_url: str, twap_qty: float | None) -> None:
    import httpx

    from backend.gateway.config import settings

    client = ConsensusClient(parse_cluster(settings.raft_cluster_nodes))
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as http:
        r = await http.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        r.raise_for_status()
        http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        status = (await http.get("/api/consensus/status")).json()
        if not status["enabled"]:
            raise SystemExit("The gateway runs with RAFT off: set RAFT_ENABLED=true in .env and restart start_all.py")
        say(f"Gateway sees the cluster: {status['message']}")
        leader, s, _ = await wait_leader(client)
        say(f"Leader: node {leader} (pid {s['pid']}, term {s['term']}) -> {summary(s['state'])}")

        algo = None
        if twap_qty:
            r = await http.post("/api/algos/twap", json={"symbol": "BTC/USDT", "side": "BUY", "quantity": twap_qty,
                                                         "duration_s": 60, "slices": 6})
            if r.status_code != 201:
                raise SystemExit(f"TWAP refused: {r.text}")
            algo = r.json()
            say(f"TWAP {algo['id']} started: BUY {twap_qty} BTC in 6 slices over 60 s (testnet)")
            while True:
                algo = (await http.get(f"/api/algos/{algo['id']}")).json()
                if sum(1 for sl in algo["slices"] if sl["status"] != "PENDING") >= 2:
                    break
                await asyncio.sleep(0.5)
            say(f"   {algo['filled_quantity']:g} BTC filled so far - now the leader dies mid-TWAP")

        say(f"KILLING the RAFT leader, node {leader} (pid {s['pid']})")
        kill(s["pid"])
        new, s2, ms = await wait_leader(client, exclude=leader)
        say(f"New leader: node {new} (term {s2['term']}) after {ms:.0f} ms -> {summary(s2['state'])}")

        t = time.perf_counter()
        r = await http.post("/api/risk/kill-switch/activate", json={"reason": "RAFT failover demo"})
        say(f"Kill switch ENGAGED through the gateway: HTTP {r.status_code} in {(time.perf_counter() - t) * 1000:.0f} ms")
        await asyncio.sleep(0.3)
        states = await client.states()
        show_nodes(states)
        r = await http.post("/api/risk/kill-switch/deactivate")
        say(f"Kill switch released (needs a majority: 2 of 3 are up): HTTP {r.status_code}")

        if algo:
            r = await http.post(f"/api/algos/{algo['id']}/resume")  # the kill switch paused it
            say(f"TWAP resumed after the kill switch paused it: HTTP {r.status_code}")
            while algo["status"] in ("RUNNING", "PAUSED"):
                await asyncio.sleep(1)
                algo = (await http.get(f"/api/algos/{algo['id']}")).json()
                if algo["status"] == "PAUSED":
                    break
            filled = [sl for sl in algo["slices"] if sl["filled"] > 0]
            say(f"TWAP {algo['status']}: {algo['filled_quantity']:g} of {twap_qty:g} BTC in {len(filled)} slices "
                f"(venues {sorted({v for sl in filled for v in sl['venues']})}) - trading never waited on RAFT")

        say("Waiting for start_all.py to restart the killed node (~15 s)...")
        for _ in range(40):
            states = await client.states()
            if states.get(leader):
                break
            await asyncio.sleep(1)
        await asyncio.sleep(1.0)
        states = await client.states()
        show_nodes(states)
        status = (await http.get("/api/consensus/status")).json()
        say(f"Gateway: {status['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAFT failover demo")
    parser.add_argument("--gateway", action="store_true", help="use the running gateway + its cluster")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--twap", type=float, default=None, help="with --gateway: BTC to buy by TWAP during failover")
    args = parser.parse_args()
    if args.gateway:
        asyncio.run(against_gateway(args.url, args.twap))
    else:
        asyncio.run(standalone())


if __name__ == "__main__":
    main()
