"""RAFT over real TCP on 127.0.0.1: three nodes, the client, a leader failover."""

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path

from backend.services.consensus.client import ConsensusClient, ConsensusUnavailableError, parse_cluster
from backend.services.consensus.node import RaftNode
from backend.services.consensus.storage import SqliteStorage


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
        self.client = ConsensusClient(self.addons, write_timeout_s=3.0)

    async def asyncTearDown(self) -> None:
        for i in list(self.nodes):
            await self.stop_node(i)

    async def start_node(self, i: int) -> None:
        self.storages[i] = SqliteStorage(Path(self.tmp.name) / f"node-{i}.db")
        # Fast timers for tests: 10 ms ticks, elections in 100-200 ms.
        self.nodes[i] = RaftNode(i, self.addons, self.storages[i], tick_s=0.01, election_ticks=10,
                                 heartbeat_ticks=2, commit_wait_s=1.0)
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
        await asyncio.sleep(0.5)
        states = await self.client.states()
        snaps = {i: s["state"] for i, s in states.items()}
        self.assertEqual(snaps[leader], snaps[new])
        self.assertTrue(snaps[leader]["kill_switch"]["active"])
        self.assertEqual(snaps[leader]["risk_limits"], {"max_order_notional": 123.0})

    async def test_no_majority_means_no_write(self) -> None:
        leader = await self.wait_leader()
        for i in [i for i in self.addons if i != leader]:
            await self.stop_node(i)
        client = ConsensusClient(self.addons, write_timeout_s=1.5)
        with self.assertRaises(ConsensusUnavailableError):
            await client.propose({"op": "set_kill_switch", "state": {"active": False}})
        states = await client.states()
        self.assertIsNone(states[leader]["state"]["kill_switch"])  # never applied anywhere

    async def test_bad_commands_are_refused(self) -> None:
        await self.wait_leader()
        with self.assertRaises(ValueError):
            await self.client.propose({"op": "transfer_funds", "amount": 1})


if __name__ == "__main__":
    unittest.main()
