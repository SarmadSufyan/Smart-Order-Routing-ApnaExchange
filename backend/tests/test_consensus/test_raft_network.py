"""RAFT over real TCP on 127.0.0.1: three nodes, the client, a leader failover."""

import asyncio
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from backend.services.consensus.client import ConsensusClient, ConsensusUnavailableError, parse_cluster, request
from backend.services.consensus.node import RaftNode
from backend.services.consensus.storage import SqliteStorage
from backend.services.consensus.wire import MessageSigner

SECRET = "test-shared-secret-0123456789abcdef"


def free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


class ParseTest(unittest.TestCase):
    def test_ids_by_position_and_localhost_becomes_ipv4(self) -> None:
        self.assertEqual(parse_cluster("localhost:9001, 10.0.0.2:9002"),
                         {1: ("127.0.0.1", 9001), 2: ("10.0.0.2", 9002)})


class NetworkTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # IsolatedAsyncioTestCase runs asyncio in debug mode, which records a
        # stack trace for every callback — far too slow for three nodes
        # ticking every 10 ms (elections never settle). Real speed, please.
        asyncio.get_running_loop().set_debug(False)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addons = {i: ("127.0.0.1", p) for i, p in enumerate(free_ports(3), start=1)}
        self.storages: dict[int, SqliteStorage] = {}
        self.nodes: dict[int, RaftNode] = {}
        for i in self.addons:
            await self.start_node(i)
        self.client = ConsensusClient(self.addons, write_timeout_s=3.0, signer=MessageSigner(SECRET))

    async def asyncTearDown(self) -> None:
        for i in list(self.nodes):
            await self.stop_node(i)

    async def start_node(self, i: int) -> None:
        self.storages[i] = SqliteStorage(Path(self.tmp.name) / f"node-{i}.db")
        # Fast timers for tests: 10 ms ticks, elections in 100-200 ms.
        self.nodes[i] = RaftNode(i, self.addons, self.storages[i], tick_s=0.01, election_ticks=10,
                                 heartbeat_ticks=2, commit_wait_s=1.0, signer=MessageSigner(SECRET))
        await self.nodes[i].start()

    async def stop_node(self, i: int) -> None:
        await self.nodes.pop(i).stop()
        self.storages.pop(i).close()

    async def wait_leader(self, exclude: int | None = None, timeout: float = 5.0) -> int:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            states = await self.client.states()
            leaders = [i for i, s in states.items() if s and s["role"] == "LEADER" and i != exclude]
            if leaders:
                return leaders[0]
            await asyncio.sleep(0.05)
        raise AssertionError("no leader elected")

    async def test_write_through_the_leader_failover_and_catch_up(self) -> None:
        leader = await self.wait_leader()
        follower = next(i for i in self.addons if i != leader)
        self.client.leader_hint = follower  # start at the wrong node: the redirect must work
        reply = await self.client.propose({"op": "set_kill_switch", "state": {"active": True, "reason": "demo"}})
        self.assertTrue(reply["ok"])
        self.assertTrue(reply["state"]["kill_switch"]["active"])

        await self.stop_node(leader)  # the leader dies
        new = await self.wait_leader(exclude=leader)
        self.assertNotEqual(new, leader)
        reply = await self.client.propose({"op": "set_risk_limits", "limits": {"max_order_notional": 123.0}})
        self.assertTrue(reply["ok"])  # 2 of 3 is still a majority

        await self.start_node(leader)  # back from the dead: catches up from its disk + the leader
        # Wait until it has caught up rather than a fixed time: on a busy machine the
        # returning node sometimes times out and holds an election before it hears the
        # leader (no pre-vote), which adds a few hundred ms. Seen once in ~40 runs.
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            states = await self.client.states()
            snaps = {i: s["state"] for i, s in states.items() if s}
            if len(snaps) == 3 and snaps[leader] == snaps[new] or asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(snaps[leader], snaps[new])
        self.assertTrue(snaps[leader]["kill_switch"]["active"])
        self.assertEqual(snaps[leader]["risk_limits"], {"max_order_notional": 123.0})

    async def test_no_majority_means_no_write(self) -> None:
        leader = await self.wait_leader()
        for i in [i for i in self.addons if i != leader]:
            await self.stop_node(i)
        client = ConsensusClient(self.addons, write_timeout_s=1.5, signer=MessageSigner(SECRET))
        with self.assertRaises(ConsensusUnavailableError):
            await client.propose({"op": "set_kill_switch", "state": {"active": False}})
        states = await client.states()
        self.assertIsNone(states[leader]["state"]["kill_switch"])  # never applied anywhere

    async def test_a_program_without_the_secret_cannot_change_anything(self) -> None:
        leader = await self.wait_leader()
        await self.client.propose({"op": "set_kill_switch", "state": {"active": True}})
        intruder = ConsensusClient(self.addons, write_timeout_s=1.0, signer=MessageSigner("wrong-secret-0000000000"))
        with self.assertRaises(ConsensusUnavailableError):
            await intruder.propose({"op": "set_kill_switch", "state": {"active": False}})
        unsigned = ConsensusClient(self.addons, write_timeout_s=1.0)  # no signature at all
        with self.assertRaises(ConsensusUnavailableError):
            await unsigned.propose({"op": "set_kill_switch", "state": {"active": False}})
        self.assertEqual(await intruder.states(), {1: None, 2: None, 3: None})  # can't even read
        states = await self.client.states()
        self.assertTrue(states[leader]["state"]["kill_switch"]["active"])  # nothing changed
        self.assertGreater(sum(s["rejected"] for s in states.values()), 0)  # and it was noticed
        self.assertTrue(all(s["signed"] for s in states.values()))

    async def raw(self, node: int, line: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(*self.addons[node])
        writer.write(line)
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.readline(), 1.0)
        finally:
            writer.close()

    async def test_tampered_and_replayed_requests_are_refused(self) -> None:
        leader = await self.wait_leader()
        signer = MessageSigner(SECRET)
        genuine = signer.encode({"op": "propose", "nonce": "n-1",
                                 "command": {"op": "set_risk_limits", "limits": {"max_order_notional": 5.0}}})
        self.assertNotEqual(await self.raw(leader, genuine), b"")  # accepted once
        self.assertEqual(await self.raw(leader, genuine), b"")  # the same bytes again: refused
        tampered = genuine.replace(b"5.0", b"9.0")  # change one number, keep the old signature
        self.assertEqual(await self.raw(leader, tampered), b"")
        old = MessageSigner(SECRET, clock=lambda: time.time() - 120).encode({"op": "state", "nonce": "n-2"})
        self.assertEqual(await self.raw(leader, old), b"")  # 2 minutes old: refused
        states = await self.client.states()
        self.assertEqual(states[leader]["state"]["risk_limits"], {"max_order_notional": 5.0})
        self.assertGreaterEqual(states[leader]["rejected"], 3)

    async def test_a_fake_node_cannot_feed_the_gateway_a_fake_state(self) -> None:
        # Something listening on a node's port answers without the secret.
        fake_reply = json.dumps({"ok": True, "role": "LEADER", "state": {"kill_switch": {"active": False}}})
        server = await asyncio.start_server(
            lambda r, w: (w.write((fake_reply + "\n").encode()), w.close()), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertIsNone(await request(("127.0.0.1", port), {"op": "state"}, 1.0, MessageSigner(SECRET)))
        finally:
            server.close()

    async def test_bad_commands_are_refused(self) -> None:
        await self.wait_leader()
        with self.assertRaises(ValueError):
            await self.client.propose({"op": "transfer_funds", "amount": 1})


if __name__ == "__main__":
    unittest.main()
