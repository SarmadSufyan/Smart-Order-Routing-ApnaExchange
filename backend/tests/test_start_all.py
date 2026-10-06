"""start_all.py: refuse a second start, wait for a RAFT leader, stop everything
in order. The process tests start real child processes (a child that starts a
grandchild, like npm -> cmd -> node) — what a unit test can't fake."""

import asyncio
import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import start_all

# A child that starts a grandchild (prints the grandchild's real pid), then waits.
TREE_CHILD = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import os, time; print(os.getpid(), flush=True); time.sleep(60)'],"
    " stdout=subprocess.PIPE, text=True)\n"
    "print(g.stdout.readline().strip(), flush=True)\n"
    "time.sleep(60)\n"
)
# A child that records a clean stop: Ctrl+Break on Windows, SIGINT elsewhere (what uvicorn
# handles). Short sleeps: on Windows only Ctrl+C wakes a long time.sleep() early (an event
# loop, like the gateway's and the nodes', wakes up for any signal).
CLEAN_CHILD = (
    "import signal, sys, time\n"
    "def stop(*_):\n"
    "    open(sys.argv[1], 'w').write('clean')\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGBREAK if hasattr(signal, 'SIGBREAK') else signal.SIGINT, stop)\n"
    "print('ready', flush=True)\n"
    "for _ in range(600):\n"
    "    time.sleep(0.1)\n"
)


def alive(pid: int) -> bool:
    if start_all.WINDOWS:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(OSError):  # an exited process nobody has reaped yet is a zombie: dead
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    return True


def gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def listening_socket(host: str = "127.0.0.1") -> socket.socket:
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    sock.listen()
    return sock


class PortCheckTest(unittest.TestCase):
    def test_a_port_with_a_server_is_not_free(self) -> None:
        sock = listening_socket()
        port = sock.getsockname()[1]
        self.assertFalse(start_all.port_free("127.0.0.1", port))
        sock.close()
        self.assertTrue(start_all.port_free("127.0.0.1", port))

    def test_ipv6_localhost_is_checked_too(self) -> None:
        # Vite listens on "localhost", which is ::1 on Windows: an IPv4 check alone misses it.
        try:
            sock = listening_socket("::1")
        except OSError:
            self.skipTest("no IPv6 on this machine")
        with sock:
            self.assertFalse(start_all.port_free("::1", sock.getsockname()[1]))

    def test_busy_ports_says_what_needed_the_port(self) -> None:
        with listening_socket() as sock:
            port = sock.getsockname()[1]
            needed = [("gateway", "127.0.0.1", port), ("RAFT node 1", "127.0.0.1", 0)]
            self.assertEqual(start_all.busy_ports(needed), {port: "gateway"})

    def test_only_the_ports_this_start_uses_are_checked(self) -> None:
        self.assertEqual(start_all.needed_ports(False, {}), [("gateway", "127.0.0.1", 8000)])
        with_all = start_all.needed_ports(True, {1: ("127.0.0.1", 9001), 2: ("127.0.0.1", 9002)})
        self.assertIn(("frontend", "::1", 5173), with_all)
        self.assertIn(("frontend", "127.0.0.1", 5173), with_all)
        self.assertIn(("RAFT node 2", "127.0.0.1", 9002), with_all)

    def test_netstat_listeners_in_any_language(self) -> None:
        output = (
            "Active Connections\n\n  Proto  Local Address          Foreign Address        State           PID\n"
            "  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       19856\n"
            "  TCP    127.0.0.1:8000         127.0.0.1:53211        ESTABLISHED     19856\n"
            "  TCP    127.0.0.1:53211        127.0.0.1:8000         ESTABLISHED     4242\n"
            "  TCP    127.0.0.1:9001         0.0.0.0:0              ABHÖREN         6904\n"  # German Windows
            "  TCP    [::1]:5173             [::]:0                 LISTENING       4608\n"
            "  UDP    0.0.0.0:5353           *:*                                    1234\n"
        )
        self.assertEqual(start_all.listeners(output), {8000: 19856, 9001: 6904, 5173: 4608})

    def test_the_message_names_the_program_and_how_to_stop_it(self) -> None:
        with mock.patch.object(start_all, "WINDOWS", True):
            text = start_all.ports_in_use_message({8000: "gateway", 5173: "frontend"},
                                                  {8000: (19856, "python.exe"), 5173: (4608, "node.exe")})
        self.assertIn("Port 8000 (gateway) is already in use - by python.exe, pid 19856.", text)
        self.assertIn("Port 5173 (frontend) is already in use - by node.exe, pid 4608.", text)
        self.assertIn("taskkill /PID 4608 /PID 19856 /T /F", text)
        self.assertIn("nothing was started", text)


class FakeCluster:
    def __init__(self, answers: list[dict]) -> None:
        self.answers = answers
        self.asked = 0

    async def states(self) -> dict:
        self.asked += 1
        return self.answers[min(self.asked, len(self.answers)) - 1]


def node(role: str, term: int = 5, mode: str = "testnet") -> dict:
    return {"ok": True, "role": role, "term": term, "mode": mode}


class LeaderTest(unittest.TestCase):
    def test_a_leader_needs_a_majority_answering(self) -> None:
        self.assertEqual(start_all.leader_of({1: node("FOLLOWER"), 2: node("LEADER"), 3: None}, "testnet"), 2)
        self.assertIsNone(start_all.leader_of({1: None, 2: node("LEADER"), 3: None}, "testnet"))
        self.assertIsNone(start_all.leader_of({1: node("CANDIDATE"), 2: node("FOLLOWER"), 3: None}, "testnet"))

    def test_nodes_of_another_mode_dont_count(self) -> None:
        states = {1: node("LEADER", mode="mainnet"), 2: node("FOLLOWER", mode="mainnet"), 3: node("FOLLOWER")}
        self.assertIsNone(start_all.leader_of(states, "testnet"))

    def test_the_newest_term_wins(self) -> None:
        states = {1: node("LEADER", term=4), 2: node("LEADER", term=7), 3: node("FOLLOWER", term=7)}
        self.assertEqual(start_all.leader_of(states, "testnet"), 2)

    def test_waits_until_the_election_is_over(self) -> None:
        down = {1: None, 2: None, 3: None}
        electing = {1: node("CANDIDATE"), 2: node("FOLLOWER"), 3: None}
        elected = {1: node("LEADER"), 2: node("FOLLOWER"), 3: None}
        cluster = FakeCluster([down, electing, elected])
        leader, states = asyncio.run(start_all.wait_for_leader(cluster, "testnet", timeout_s=5, poll_s=0.01))
        self.assertEqual((leader, cluster.asked), (1, 3))
        self.assertIs(states, elected)

    def test_gives_up_after_the_wait(self) -> None:
        cluster = FakeCluster([{1: None, 2: None, 3: None}])
        started = time.monotonic()
        leader, _ = asyncio.run(start_all.wait_for_leader(cluster, "testnet", timeout_s=0.3, poll_s=0.05))
        self.assertIsNone(leader)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertGreaterEqual(cluster.asked, 2)  # asked again (not more: a busy machine can stall a sleep)


class ProcessTest(unittest.TestCase):
    """Real processes: a stop must leave nothing behind and let the gateway finish cleanly."""

    def spawn(self, code: str, *args: str) -> subprocess.Popen:
        proc = start_all.spawn([sys.executable, "-c", code, *args], stdout=subprocess.PIPE, text=True)
        self.addCleanup(start_all.kill_tree, proc)
        self.addCleanup(proc.stdout.close)
        return proc

    def test_kill_tree_stops_the_grandchildren_too(self) -> None:
        # npm -> cmd -> node: killing only the first one left Vite holding port 5173.
        child = self.spawn(TREE_CHILD)
        grandchild = int(child.stdout.readline())
        self.assertTrue(alive(grandchild))
        start_all.kill_tree(child)
        self.assertIsNotNone(child.poll())
        self.assertTrue(gone_within(grandchild, 5), "the grandchild survived")

    def test_ask_to_stop_lets_the_child_stop_cleanly(self) -> None:
        marker = Path(tempfile.mkdtemp()) / "stopped"
        child = self.spawn(CLEAN_CHILD, str(marker))
        self.assertEqual(child.stdout.readline().strip(), "ready")
        if not start_all.ask_to_stop(child):
            self.skipTest("no console here: Ctrl+Break can't be sent (start_all.py then kills instead)")
        self.assertTrue(start_all.wait_for_exit(child, 10))
        self.assertEqual(marker.read_text(), "clean")

    def test_stop_all_stops_everything_gateway_cleanly(self) -> None:
        marker = Path(tempfile.mkdtemp()) / "gateway-stopped"
        gateway = self.spawn(CLEAN_CHILD, str(marker))
        frontend = self.spawn(TREE_CHILD)
        node = self.spawn(CLEAN_CHILD, os.devnull)
        self.assertEqual(gateway.stdout.readline().strip(), "ready")
        self.assertEqual(node.stdout.readline().strip(), "ready")
        vite = int(frontend.stdout.readline())
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(start_all, "GATEWAY_STOP_S", 15):
            start_all.stop_all(gateway, frontend, {1: node})
        for proc in (gateway, frontend, node):
            self.assertIsNotNone(proc.poll())
        self.assertTrue(gone_within(vite, 5), "the frontend's grandchild survived")
        if "killed" not in out.getvalue():  # no console: start_all.py had to kill the gateway
            self.assertEqual(marker.read_text(), "clean")
        self.assertIn("Done.", out.getvalue())

    def test_a_finished_child_is_left_alone(self) -> None:
        proc = start_all.spawn([sys.executable, "-c", "pass"])
        self.assertTrue(start_all.wait_for_exit(proc, 10))
        self.assertTrue(start_all.ask_to_stop(proc))
        start_all.kill_tree(proc)  # no error


if __name__ == "__main__":
    unittest.main()
