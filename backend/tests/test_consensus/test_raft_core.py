"""RAFT core (Phase 6): election, log replication, commit — on a simulated network.

The core has no clock and no sockets, so every scenario (crashes, network
splits, restarts) is driven tick by tick and is exactly reproducible.
"""

import random
import tempfile
import unittest
from pathlib import Path

from backend.services.consensus.core import Entry, NotLeaderError, RaftCore, Role
from backend.services.consensus.state_machine import RiskStateMachine
from backend.services.consensus.storage import MemoryStorage, SqliteStorage


class SimCluster:
    """N cores wired through a perfect-but-controllable network."""

    def __init__(self, n: int = 3, seed: int = 1) -> None:
        self.ids = list(range(1, n + 1))
        self.seed = seed
        self.storage = {i: MemoryStorage() for i in self.ids}
        self.nodes = {i: self._make(i) for i in self.ids}
        self.state = {i: RiskStateMachine() for i in self.ids}
        self.down: set[int] = set()
        self.cut: set[frozenset[int]] = set()

    def _make(self, i: int) -> RaftCore:
        return RaftCore(i, self.ids, self.storage[i], election_ticks=10, heartbeat_ticks=2,
                        rng=random.Random(self.seed * 100 + i))

    def link_ok(self, a: int, b: int) -> bool:
        return a not in self.down and b not in self.down and frozenset((a, b)) not in self.cut

    def deliver(self) -> None:
        for _ in range(50):  # until the network is quiet
            moved = False
            for i in self.ids:
                if i in self.down:
                    continue
                for msg in self.nodes[i].drain_outbox():
                    if self.link_ok(i, msg["to"]):
                        self.nodes[msg["to"]].step(msg)
                        moved = True
            if not moved:
                break
        for i in self.ids:
            if i not in self.down:
                for index, entry in self.nodes[i].take_committed():
                    self.state[i].apply(index, entry.term, entry.command)

    def tick(self, n: int = 1) -> None:
        for _ in range(n):
            for i in self.ids:
                if i not in self.down:
                    self.nodes[i].tick()
            self.deliver()

    def leader(self) -> int | None:
        leaders = [i for i in self.ids if i not in self.down and self.nodes[i].role == Role.LEADER]
        return max(leaders, key=lambda i: self.nodes[i].term) if leaders else None

    def run_until(self, predicate, max_ticks: int = 400) -> int:
        for t in range(max_ticks):
            if predicate():
                return t
            self.tick()
        raise AssertionError("condition not reached")

    def elect(self) -> int:
        self.run_until(lambda: self.leader() is not None)
        return self.leader()

    def crash(self, i: int) -> None:
        self.down.add(i)
        self.nodes[i].drain_outbox()

    def restart(self, i: int) -> None:
        """A fresh process: only what was in storage survives."""
        self.nodes[i] = self._make(i)
        self.state[i] = RiskStateMachine()
        self.down.discard(i)
        self.deliver()

    def propose(self, command: dict) -> int:
        index, _ = self.nodes[self.leader()].propose(command)
        self.deliver()
        return index

    def commands(self, i: int) -> list[dict]:
        return [e.command for e in self.nodes[i].log[: self.nodes[i].commit_index] if e.command["op"] != "noop"]


def ks(active: bool) -> dict:
    return {"op": "set_kill_switch", "state": {"active": active, "reason": "test", "activated_by": "tester"}}


class ElectionTest(unittest.TestCase):
    def test_exactly_one_leader_and_everyone_agrees(self) -> None:
        c = SimCluster()
        leader = c.elect()
        c.tick(5)
        self.assertEqual([i for i in c.ids if c.nodes[i].role == Role.LEADER], [leader])
        self.assertEqual({c.nodes[i].leader_id for i in c.ids}, {leader})
        self.assertEqual(len({c.nodes[i].term for i in c.ids}), 1)

    def test_elections_are_reproducible_and_take_one_timeout(self) -> None:
        a, b = SimCluster(seed=7), SimCluster(seed=7)
        ticks_a = a.run_until(lambda: a.leader() is not None)
        ticks_b = b.run_until(lambda: b.leader() is not None)
        self.assertEqual((a.leader(), ticks_a), (b.leader(), ticks_b))
        self.assertLess(ticks_a, 20)  # election timeout is 10-20 ticks

    def test_one_vote_per_term_even_across_a_restart(self) -> None:
        s = MemoryStorage()
        node = RaftCore(1, [1, 2, 3], s)
        node.step({"type": "vote_req", "term": 5, "from": 2, "to": 1, "last_log_index": 0, "last_log_term": 0})
        self.assertTrue(node.drain_outbox()[0]["granted"])
        restarted = RaftCore(1, [1, 2, 3], s)  # crash + restart: the vote is remembered
        restarted.step({"type": "vote_req", "term": 5, "from": 3, "to": 1, "last_log_index": 0, "last_log_term": 0})
        self.assertFalse(restarted.drain_outbox()[0]["granted"])
        self.assertEqual((restarted.term, restarted.voted_for), (5, 2))

    def test_a_node_with_a_stale_log_cannot_become_leader(self) -> None:
        c = SimCluster()
        c.elect()
        lagging = next(i for i in c.ids if i != c.leader())
        c.crash(lagging)
        c.propose(ks(True))
        c.propose({"op": "set_risk_limits", "limits": {"max_order_notional": 500}})
        old = c.leader()
        c.crash(old)
        c.restart(lagging)  # alive: `lagging` (behind) and the third node (up to date)
        new = c.elect()
        third = next(i for i in c.ids if i not in (old, lagging))
        self.assertEqual(new, third)  # the stale node never wins a vote from an up-to-date one
        c.tick(5)
        self.assertEqual(c.commands(lagging), c.commands(third))  # and then catches up
        self.assertTrue(c.state[lagging].kill_switch["active"])

    def test_a_cut_off_leader_steps_down_and_the_majority_elects_a_new_one(self) -> None:
        c = SimCluster()
        old = c.elect()
        for i in c.ids:
            if i != old:
                c.cut.add(frozenset((old, i)))
        c.run_until(lambda: c.nodes[old].role != Role.LEADER)  # check-quorum
        c.run_until(lambda: c.leader() not in (None, old))
        new = c.leader()
        self.assertNotEqual(new, old)
        self.assertGreater(c.nodes[new].term, c.nodes[old].term)
        with self.assertRaises(NotLeaderError):
            c.nodes[old].propose(ks(True))


class ReplicationTest(unittest.TestCase):
    def test_commands_commit_and_apply_identically_everywhere(self) -> None:
        c = SimCluster()
        c.elect()
        c.propose(ks(True))
        c.propose({"op": "set_blacklist", "venue_id": "V5", "on": True, "actor": "risk_mgr", "at": "t1"})
        c.propose({"op": "set_risk_limits", "limits": {"max_order_notional": 500.0}})
        c.propose(ks(False))
        c.tick(3)  # followers learn the final commit index from the next heartbeat
        snapshots = [c.state[i].snapshot() for i in c.ids]
        self.assertTrue(all(s == snapshots[0] for s in snapshots))
        self.assertEqual(snapshots[0]["kill_switch"]["active"], False)
        self.assertEqual(snapshots[0]["blacklist"], {"V5": {"by": "risk_mgr", "at": "t1"}})
        self.assertEqual(snapshots[0]["risk_limits"], {"max_order_notional": 500.0})

    def test_nothing_commits_without_a_majority(self) -> None:
        c = SimCluster()
        leader = c.elect()
        followers = [i for i in c.ids if i != leader]
        for f in followers:
            c.crash(f)
        index = c.propose(ks(True))
        c.tick(3)  # (before check-quorum makes the leader step down)
        self.assertLess(c.nodes[leader].commit_index, index)
        self.assertIsNone(c.state[leader].kill_switch)  # stored, not committed, not applied
        c.restart(followers[0])
        c.tick(2)
        self.assertGreaterEqual(c.nodes[leader].commit_index, index)  # 2 of 3 = majority
        self.assertTrue(c.state[leader].kill_switch["active"])

    def test_leader_crash_loses_nothing_committed_and_the_rest_carry_on(self) -> None:
        c = SimCluster()
        c.elect()
        c.propose(ks(True))
        c.propose({"op": "set_risk_limits", "limits": {"max_order_notional": 250.0}})
        c.tick(3)
        old, old_term = c.leader(), c.nodes[c.leader()].term
        c.crash(old)
        c.run_until(lambda: c.leader() not in (None, old))
        new = c.leader()
        self.assertGreater(c.nodes[new].term, old_term)
        self.assertTrue(c.state[new].kill_switch["active"])  # unchanged after failover
        self.assertEqual(c.state[new].risk_limits, {"max_order_notional": 250.0})
        c.propose(ks(False))  # 2 of 3 nodes still commit
        c.tick(3)
        survivors = [i for i in c.ids if i != old]
        self.assertTrue(all(c.state[i].kill_switch["active"] is False for i in survivors))
        c.restart(old)  # the crashed node returns as a follower and catches up
        c.tick(10)
        self.assertEqual(c.nodes[old].role, Role.FOLLOWER)
        self.assertEqual(c.state[old].snapshot(), c.state[new].snapshot())

    def test_uncommitted_entries_of_a_deposed_leader_are_replaced(self) -> None:
        c = SimCluster()
        old = c.elect()
        c.propose(ks(True))
        c.tick(3)
        for i in c.ids:  # cut the leader off, then let it append things nobody else sees
            if i != old:
                c.cut.add(frozenset((old, i)))
        c.nodes[old].propose({"op": "set_risk_limits", "limits": {"max_order_notional": 1.0}})
        c.nodes[old].propose({"op": "set_risk_limits", "limits": {"max_order_notional": 2.0}})
        c.deliver()
        c.run_until(lambda: c.leader() not in (None, old))
        new = c.leader()
        c.propose({"op": "set_risk_limits", "limits": {"max_order_notional": 777.0}})
        c.cut.clear()  # the old leader rejoins
        c.tick(10)
        self.assertEqual(c.nodes[old].role, Role.FOLLOWER)
        self.assertEqual([e.term for e in c.nodes[old].log], [e.term for e in c.nodes[new].log])
        self.assertEqual(c.state[old].risk_limits, {"max_order_notional": 777.0})
        self.assertNotIn({"max_order_notional": 1.0}, [cmd.get("limits") for cmd in c.commands(old)])

    def test_a_long_way_behind_follower_catches_up_quickly(self) -> None:
        c = SimCluster()
        c.elect()
        behind = next(i for i in c.ids if i != c.leader())
        c.crash(behind)
        for n in range(150):  # more than one append message can carry
            c.propose({"op": "set_risk_limits", "limits": {"n": n}})
        c.restart(behind)
        c.tick(4)
        self.assertEqual(c.nodes[behind].commit_index, c.nodes[c.leader()].commit_index)
        self.assertEqual(c.state[behind].risk_limits, {"n": 149})

    def test_a_new_leader_commits_old_entries_only_through_one_of_its_own(self) -> None:
        # Paper §5.4.2 / Figure 8: counting replicas is only safe for the
        # leader's own term; older entries commit indirectly.
        s = MemoryStorage()
        s.term, s.log = 3, [Entry(1, {"op": "noop"}), Entry(2, ks(True))]
        node = RaftCore(1, [1, 2, 3], s)
        node._become_leader()  # appends a term-3 no-op at index 3
        node.drain_outbox()
        node.step({"type": "append_resp", "term": 3, "from": 2, "to": 1, "success": True, "match_index": 2})
        self.assertEqual(node.commit_index, 0)  # index 2 is on a majority, but it's from term 2
        node.step({"type": "append_resp", "term": 3, "from": 2, "to": 1, "success": True, "match_index": 3})
        self.assertEqual(node.commit_index, 3)  # the term-3 entry commits, and with it 1-2

    def test_messages_from_strangers_are_ignored(self) -> None:
        node = RaftCore(1, [1, 2, 3], MemoryStorage())
        node.step({"type": "vote_req", "term": 9, "from": 7, "to": 1, "last_log_index": 0, "last_log_term": 0})
        node.step({"type": "vote_req", "term": 9, "from": 2, "to": 3, "last_log_index": 0, "last_log_term": 0})
        self.assertEqual((node.term, node.drain_outbox()), (0, []))


class RandomizedSafetyTest(unittest.TestCase):
    """Random crashes, restarts, network splits and proposals; after every
    tick check RAFT's two promises: at most one leader per term, and no two
    nodes ever apply different commands at the same log index."""

    def test_safety_holds_under_random_failures(self) -> None:
        for seed in range(40):
            with self.subTest(seed=seed):
                self._run(seed)

    def _run(self, seed: int) -> None:
        rng = random.Random(seed)
        c = SimCluster(seed=seed)
        leaders_by_term: dict[int, int] = {}
        applied: dict[int, dict] = {}  # index -> the one command every node must apply there
        proposed = 0
        for _ in range(600):
            roll = rng.random()
            if roll < 0.02 and len(c.down) < 2:
                c.crash(rng.choice([i for i in c.ids if i not in c.down]))
            elif roll < 0.05 and c.down:
                c.restart(rng.choice(sorted(c.down)))
            elif roll < 0.07:
                a, b = rng.sample(c.ids, 2)
                c.cut ^= {frozenset((a, b))}  # toggle a network split between two nodes
            elif roll < 0.25 and (leader := c.leader()) is not None:
                proposed += 1
                c.nodes[leader].propose({"op": "set_risk_limits", "limits": {"n": proposed}})
            c.tick()
            for i in c.ids:
                node = c.nodes[i]
                if node.role == Role.LEADER and i not in c.down:
                    self.assertEqual(leaders_by_term.setdefault(node.term, i), i, "two leaders in one term")
                for index in range(1, node.last_applied + 1):
                    command = node.log[index - 1].command
                    self.assertEqual(applied.setdefault(index, command), command, "applied logs diverged")
        c.down.clear()
        c.cut.clear()
        for i in list(c.ids):
            c.restart(i)
        c.run_until(lambda: c.leader() is not None)
        c.propose({"op": "set_risk_limits", "limits": {"n": "final"}})
        c.tick(10)  # healed: everyone converges on the same state
        snaps = [c.state[i].snapshot() for i in c.ids]
        self.assertTrue(all(s == snaps[0] for s in snaps))
        self.assertEqual(snaps[0]["risk_limits"], {"n": "final"})


class StateMachineTest(unittest.TestCase):
    def test_set_commands_are_idempotent_and_unknown_ones_are_ignored_everywhere(self) -> None:
        sm = RiskStateMachine()
        sm.apply(1, 1, ks(True))
        once = sm.snapshot()
        sm.apply(2, 1, ks(True))  # a client retry after a lost reply
        self.assertEqual({**sm.snapshot(), "applied_index": 1}, once)
        result = sm.apply(3, 1, {"op": "withdraw_all_funds"})
        self.assertIn("unknown", result["error"])
        self.assertEqual(sm.applied_index, 3)
        sm.apply(4, 1, {"op": "set_blacklist", "venue_id": "V5", "on": True})
        sm.apply(5, 1, {"op": "set_blacklist", "venue_id": "V5", "on": False})
        self.assertEqual(sm.blacklist, {})


class SqliteStorageTest(unittest.TestCase):
    def test_survives_reopen_and_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node-1.db"
            s = SqliteStorage(path)
            s.save_term_vote(4, 2)
            s.append(1, [Entry(1, {"op": "noop"}), Entry(4, ks(True)), Entry(4, ks(False))])
            s.save_commit(2)
            s.truncate_from(3)
            s.close()
            reopened = SqliteStorage(path)
            term, voted, log, commit = reopened.load()
            reopened.close()
            self.assertEqual((term, voted, commit), (4, 2, 2))
            self.assertEqual(log, [Entry(1, {"op": "noop"}), Entry(4, ks(True))])

    def test_a_restarted_node_rebuilds_its_state_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = SqliteStorage(Path(tmp) / "n.db")
            node = RaftCore(1, [1], s)  # one-node cluster: commits alone
            node.tick(), [node.tick() for _ in range(20)]
            node.propose(ks(True))
            s.close()
            s2 = SqliteStorage(Path(tmp) / "n.db")
            again = RaftCore(1, [1], s2)
            sm = RiskStateMachine()
            for index, entry in again.take_committed():
                sm.apply(index, entry.term, entry.command)
            s2.close()
            self.assertTrue(sm.kill_switch["active"])


if __name__ == "__main__":
    unittest.main()
