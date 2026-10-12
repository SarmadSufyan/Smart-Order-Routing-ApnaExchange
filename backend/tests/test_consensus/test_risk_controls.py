"""RiskControls: the gateway side of RAFT — write through the leader, adopt
the committed state, and stay fail-safe when the cluster has no majority."""

import asyncio
import unittest

from backend.services.consensus.client import ConsensusUnavailableError
from backend.services.consensus.state_machine import RiskStateMachine
from backend.services.consensus.wire import MessageSigner
from backend.services.order_state.order_manager import OrderManager
from backend.services.risk_engine.controls import RiskControls
from backend.services.risk_engine.kill_switch import KillSwitch
from backend.services.risk_engine.position_tracker import PositionTracker
from backend.services.risk_engine.pre_trade import PreTradeRiskEngine
from backend.services.venue_monitor.health_checker import VenueHealthMonitor
from backend.shared.events.event_bus import event_bus
from backend.shared.exceptions import RiskLimitAboveCeilingError
from backend.shared.models.risk import RiskCeilings, RiskLimits
from backend.shared.models.venue import VenueStatus


class FakeAdapter:
    def __init__(self, venue_id: str) -> None:
        self.venue_id, self.name = venue_id, f"Venue {venue_id}"
        self.display_label, self.api_host, self.mode, self.data_mode = "test", "example.test", "testnet", "mainnet"
        self.execution_available, self.can_trade, self.execution_note = True, True, None


class FakeRegistry:
    def __init__(self) -> None:
        self.adapters = [FakeAdapter("V1"), FakeAdapter("V4"), FakeAdapter("V5")]
        self.sweeps = 0

    def all(self):
        return self.adapters

    async def cancel_all_open_orders(self) -> int:
        self.sweeps += 1
        return 0


class FakeCluster:
    """Stands in for ConsensusClient: one committed state, switchable on/off.

    ``lose_replies`` = the command reaches the leader and commits, but the
    answer never comes back (the client sees a failure)."""

    def __init__(self, mode: str = "testnet") -> None:
        self.nodes = {1: ("127.0.0.1", 1), 2: ("127.0.0.1", 2), 3: ("127.0.0.1", 3)}
        self.sm = RiskStateMachine()
        self.index = 0
        self.up = True
        self.lose_replies = False
        self.mode = mode
        self.proposed: list[dict] = []
        self.signer = MessageSigner("unit-test-secret-0123456789")

    def commit(self, command: dict) -> dict:
        self.index += 1
        return self.sm.apply(self.index, 1, command)

    async def propose(self, command: dict) -> dict:
        self.proposed.append(command)
        if not self.up:
            raise ConsensusUnavailableError("no leader")
        result = self.commit(command)
        if self.lose_replies:
            raise ConsensusUnavailableError("reply lost")
        return {"ok": True, **result, "state": self.sm.snapshot()}

    async def states(self) -> dict:
        if not self.up:
            return {i: None for i in self.nodes}
        info = {"ok": True, "term": 1, "leader": 1, "mode": self.mode, "state": self.sm.snapshot(),
                "applied_index": self.index, "commit_index": self.index, "pid": 100}
        return {1: {**info, "role": "LEADER"}, 2: {**info, "role": "FOLLOWER"}, 3: {**info, "role": "FOLLOWER"}}


class ControlsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.audits: list[dict] = []

        async def capture(data: dict) -> None:
            self.audits.append(data)

        event_bus.subscribe("audit", capture)
        self.addCleanup(event_bus.unsubscribe, "audit", capture)
        self.registry = FakeRegistry()
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits())
        self.venues = VenueHealthMonitor(self.registry)
        self.cluster = FakeCluster()

    def make(self, replicated: bool = True, mode: str = "testnet", startup_wait_s: float = 0.3) -> RiskControls:
        return RiskControls(self.risk, OrderManager(), self.registry, self.venues,
                            client=self.cluster if replicated else None, default_limits=RiskLimits(), mode=mode,
                            startup_wait_s=startup_wait_s)

    def audit_types(self) -> list[str]:
        return [a["event_type"] for a in self.audits]

    @property
    def ks(self) -> KillSwitch:
        return self.risk.kill_switch

    async def test_local_mode_works_exactly_as_before(self) -> None:
        c = self.make(replicated=False)
        await c.engage_kill_switch("test", "risk_mgr")
        self.assertTrue(self.ks.is_active)
        await c.release_kill_switch("risk_mgr")
        await c.set_limits(RiskLimits(max_order_notional=42), "admin")
        await c.blacklist_venue("V5", "risk_mgr")
        self.assertEqual(self.risk.limits.max_order_notional, 42)
        self.assertEqual(c.local_blacklist(), {"V5"})
        self.assertEqual(self.cluster.proposed, [])
        self.assertFalse(c.status().enabled)
        persisted = [a["persist"] for a in self.audits if a.get("persist")]
        self.assertIn({"key": "venue_blacklist", "value": {"venues": ["V5"]}}, persisted)  # survives a restart now

    async def test_every_change_goes_through_the_cluster(self) -> None:
        c = self.make()
        await c.engage_kill_switch("demo", "risk_mgr")
        self.assertTrue(self.cluster.sm.kill_switch["active"])
        await c.release_kill_switch("risk_mgr")
        self.assertFalse(self.cluster.sm.kill_switch["active"])
        self.assertFalse(self.ks.is_active)
        await c.set_limits(RiskLimits(max_order_notional=500), "admin")
        self.assertEqual(self.cluster.sm.risk_limits["max_order_notional"], 500)
        await c.blacklist_venue("V5", "risk_mgr")
        self.assertIn("V5", self.cluster.sm.blacklist)
        await c.unblacklist_venue("V5", "risk_mgr")
        self.assertEqual(self.cluster.sm.blacklist, {})
        self.assertEqual(self.audit_types().count("consensus_committed"), 5)

    async def test_no_majority_kill_switch_still_engages_at_once_and_replicates_later(self) -> None:
        c = self.make()
        self.cluster.up = False
        await c.engage_kill_switch("market crash", "risk_mgr")
        self.assertTrue(self.ks.is_active)  # halted here immediately
        self.assertEqual(self.registry.sweeps, 1)
        self.assertEqual(c.status().pending_writes, ["kill_switch"])
        self.assertIn("consensus_pending", self.audit_types())
        self.cluster.up = True
        await c.sync()
        self.assertTrue(self.cluster.sm.kill_switch["active"])
        self.assertEqual(c.status().pending_writes, [])

    async def test_known_no_majority_answers_at_once_without_waiting_for_a_timeout(self) -> None:
        c = self.make()
        self.cluster.up = False
        await c.sync()  # the regular re-read: the gateway now knows there is no majority
        await c.engage_kill_switch("market crash", "risk_mgr")
        self.assertTrue(self.ks.is_active)
        self.assertEqual(self.cluster.proposed, [])  # queued at once — no 3 s attempt
        self.assertEqual(c.status().pending_writes, ["kill_switch"])
        with self.assertRaises(ConsensusUnavailableError) as ctx:
            await c.release_kill_switch("risk_mgr")
        self.assertIn("only 0 of 3 RAFT nodes are reachable", ctx.exception.message)
        self.assertIn("Nothing changed", ctx.exception.message)
        self.assertEqual(self.cluster.proposed, [])
        self.assertTrue(self.ks.is_active)
        self.assertIn("consensus_refused", self.audit_types())

    async def test_an_out_of_date_no_majority_picture_is_checked_again_before_refusing(self) -> None:
        c = self.make()
        await c.engage_kill_switch("demo", "risk_mgr")
        self.cluster.up = False
        await c.sync()  # picture: no majority...
        self.cluster.up = True  # ...but the cluster has just come back
        await c.release_kill_switch("risk_mgr")  # one fresh look, then it goes through
        self.assertFalse(self.ks.is_active)
        self.assertFalse(self.cluster.sm.kill_switch["active"])

    async def test_no_majority_release_and_limit_changes_are_refused_and_nothing_changes(self) -> None:
        c = self.make()
        await c.engage_kill_switch("demo", "risk_mgr")
        self.cluster.up = False
        with self.assertRaises(ConsensusUnavailableError):
            await c.release_kill_switch("risk_mgr")
        self.assertTrue(self.ks.is_active)
        with self.assertRaises(ConsensusUnavailableError):
            await c.set_limits(RiskLimits(max_order_notional=1_000_000), "admin")
        self.assertEqual(self.risk.limits.max_order_notional, RiskLimits().max_order_notional)

    async def test_a_release_that_commits_late_cannot_resume_trading_behind_your_back(self) -> None:
        c = self.make()
        await c.engage_kill_switch("demo", "risk_mgr")
        self.cluster.lose_replies = True  # the release commits, but we never hear so
        with self.assertRaises(ConsensusUnavailableError):
            await c.release_kill_switch("risk_mgr")
        self.assertFalse(self.cluster.sm.kill_switch["active"])  # the cluster DID release...
        self.assertTrue(self.ks.is_active)  # ...but the operator was told "refused"
        self.cluster.lose_replies = False
        await c.sync()  # the current state is re-asserted after the late release
        self.assertTrue(self.cluster.sm.kill_switch["active"])
        self.assertTrue(self.ks.is_active)  # never released here in between

    async def test_changes_made_by_another_client_are_adopted(self) -> None:
        c = self.make()
        await c.sync()
        self.cluster.commit({"op": "set_kill_switch", "state": {"active": True, "reason": "other gateway",
                                                                 "activated_by": "ops"}})
        self.cluster.commit({"op": "set_risk_limits", "limits": {"max_order_notional": 77.0}})
        self.cluster.commit({"op": "set_blacklist", "venue_id": "V4", "on": True, "actor": "ops"})
        await c.sync()
        self.assertTrue(self.ks.is_active)
        self.assertEqual(self.ks.get_state().reason, "other gateway")
        self.assertEqual(self.registry.sweeps, 1)  # the full halt ran here too
        self.assertEqual(self.risk.limits.max_order_notional, 77.0)
        self.assertEqual(self.risk.limits.max_order_size, RiskLimits().max_order_size)  # rest = defaults
        self.assertEqual(self.venues.get_venue_health("V4").status, VenueStatus.BLACKLISTED)
        self.cluster.commit({"op": "set_kill_switch", "state": {"active": False}})
        self.cluster.commit({"op": "set_blacklist", "venue_id": "V4", "on": False})
        await c.sync()
        self.assertFalse(self.ks.is_active)
        self.assertNotEqual(self.venues.get_venue_health("V4").status, VenueStatus.BLACKLISTED)
        self.assertGreaterEqual(self.audit_types().count("consensus_adopted"), 3)

    async def test_real_money_ceilings_hold_against_the_page_and_the_cluster(self) -> None:
        caps = RiskCeilings(max_order_notional=25, max_algo_notional_usdt=100, max_notional_exposure=150,
                            max_trading_loss_usdt=10)
        self.risk = PreTradeRiskEngine(KillSwitch(), PositionTracker(), RiskLimits(), ceilings=caps)
        c = self.make()
        await c.sync()
        with self.assertRaises(RiskLimitAboveCeilingError):
            await c.set_limits(RiskLimits(), "admin")  # the testnet-sized defaults: far above
        self.assertEqual([p for p in self.cluster.proposed if p.get("op") == "set_risk_limits"
                          and p.get("actor") == "admin"], [])  # refused before anything was replicated
        # Another client's committed limits are adopted only as far as this gateway's ceilings allow
        self.cluster.commit({"op": "set_risk_limits", "limits": {"max_order_notional": 5_000.0}})
        await c.sync()
        self.assertEqual(self.risk.limits.max_order_notional, 25)
        adopted = self.audit_types().count("consensus_adopted")
        await c.sync()
        self.assertEqual(self.audit_types().count("consensus_adopted"), adopted)  # no audit on every poll

    async def test_a_new_cluster_is_seeded_with_this_gateways_limits(self) -> None:
        self.risk.limits = RiskLimits(max_order_notional=321)
        c = self.make()
        await c.sync()
        self.assertEqual(self.cluster.sm.risk_limits["max_order_notional"], 321)

    async def test_startup_keeps_a_locally_engaged_kill_switch_and_blacklist(self) -> None:
        await self.ks.activate("before the restart", "risk_mgr")  # restored from the local store
        self.venues.restore_blacklist(["V5"])
        c = self.make()
        await c.startup()
        self.assertTrue(self.ks.is_active)
        self.assertTrue(self.cluster.sm.kill_switch["active"])
        self.assertIn("V5", self.cluster.sm.blacklist)
        self.assertIn("consensus_synced", self.audit_types())

    async def test_startup_without_a_cluster_keeps_the_local_copy(self) -> None:
        await self.ks.activate("before the restart", "risk_mgr")
        self.cluster.up = False
        c = self.make()
        await c.startup()
        self.assertTrue(self.ks.is_active)
        self.assertIn("consensus_unreachable", self.audit_types())
        self.assertIn("kill_switch", c.status().pending_writes)
        self.assertIn("no majority", c.status().message)

    def answer_after(self, calls: int, *, without_leader: bool = False) -> list[int]:
        """The cluster's first ``calls`` answers are lost (or, ``without_leader``, come from
        an election in progress); returns the list that counts the questions."""
        asked: list[int] = []
        real_states = self.cluster.states

        async def states() -> dict:
            asked.append(1)
            if len(asked) > calls:
                return await real_states()
            if not without_leader:
                return {i: None for i in self.cluster.nodes}
            return {i: {**s, "role": "FOLLOWER", "leader": None} for i, s in (await real_states()).items()}

        self.cluster.states = states  # type: ignore[method-assign]
        return asked

    async def test_startup_asks_again_when_the_first_answers_are_late(self) -> None:
        # Live: the gateway's event loop is frozen ~2 s at start-up, so the first
        # answers time out although the cluster is fine — that must not count as down.
        asked = self.answer_after(2)
        c = self.make(startup_wait_s=5)
        await c.startup()
        self.assertEqual(len(asked), 4)  # 2 lost, the 3rd answered, then the usual sync() reads once more
        self.assertIn("consensus_synced", self.audit_types())
        self.assertNotIn("consensus_unreachable", self.audit_types())
        self.assertTrue(all(n.reachable for n in c.status().nodes))

    async def test_startup_waits_for_an_election_to_finish(self) -> None:
        asked = self.answer_after(2, without_leader=True)
        c = self.make(startup_wait_s=5)
        await c.startup()
        self.assertEqual(len(asked), 4)  # 2 without a leader, the 3rd with one, then sync()
        synced = next(a for a in self.audits if a["event_type"] == "consensus_synced")
        self.assertIn("leader node 1", synced["summary"])

    async def test_startup_gives_up_after_the_wait(self) -> None:
        self.cluster.up = False
        asked = self.answer_after(0)
        loop = asyncio.get_running_loop()
        started = loop.time()
        c = self.make(startup_wait_s=0.6)
        await c.startup()
        self.assertGreaterEqual(loop.time() - started, 0.6)
        self.assertGreater(len(asked), 1)
        unreachable = next(a for a in self.audits if a["event_type"] == "consensus_unreachable")
        self.assertIn("no answer for 0.6 s", unreachable["summary"])

    async def test_startup_without_a_leader_in_time_still_syncs_what_the_nodes_have(self) -> None:
        self.cluster.commit({"op": "set_kill_switch", "state": {"active": True, "reason": "x"}})
        self.answer_after(1000, without_leader=True)
        c = self.make(startup_wait_s=0.3)
        await c.startup()
        self.assertTrue(self.ks.is_active)  # adopted from the committed state
        synced = next(a for a in self.audits if a["event_type"] == "consensus_synced")
        self.assertIn("being elected", synced["summary"])

    async def test_nodes_of_another_exchange_mode_are_ignored(self) -> None:
        self.cluster.mode = "mainnet"
        self.cluster.commit({"op": "set_kill_switch", "state": {"active": True}})
        c = self.make(mode="testnet")
        await c.sync()
        self.assertFalse(self.ks.is_active)
        self.assertFalse(any(n.reachable for n in c.status().nodes))

    async def test_status_names_the_leader(self) -> None:
        c = self.make()
        await c.sync()
        status = c.status()
        self.assertEqual((status.leader, status.majority_reachable, status.signed), (1, True, True))
        self.assertEqual([n.role for n in status.nodes], ["LEADER", "FOLLOWER", "FOLLOWER"])


if __name__ == "__main__":
    unittest.main()
