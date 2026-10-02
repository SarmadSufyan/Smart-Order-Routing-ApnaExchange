"""Risk controls: the ONE way to change the kill switch, the risk limits and
the venue blacklist — locally, or replicated through the RAFT cluster.

RAFT off (the default): a change applies in this process and is saved in the
local audit store, exactly as before Phase 6.

RAFT on: the cluster's committed state is the truth. The gateway keeps a
local copy — the order path reads it in microseconds and never waits on the
network — writes through the leader, and re-reads the committed state every
``poll_interval_s`` (changes made by another client are adopted, audited).

When the cluster can't commit (no leader / no majority), the rule is fail-safe:
  * TIGHTENING — engage the kill switch, blacklist a venue — happens locally
    AT ONCE and is replicated as soon as a majority is back. A broken cluster
    must never stop an operator from halting trading.
  * LOOSENING — release the kill switch, un-blacklist, change limits — needs a
    majority, or it is refused and nothing changes. If such a write was sent
    but not confirmed, the current state is re-asserted after it, so a late
    commit can't loosen anything behind the operator's back.
  * While a write of ours is unconfirmed, the cluster can't overwrite that
    item in the local copy.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from backend.services.consensus.client import ConsensusClient, ConsensusUnavailableError
from backend.shared.events.event_bus import audit
from backend.shared.models.consensus import ConsensusNode, ConsensusStatus
from backend.shared.models.risk import KillSwitchState, RiskLimits
from backend.shared.models.venue import VenueStatus
from backend.shared.utils.logger import get_logger

from .kill_switch import engage_kill_switch

logger = get_logger("risk_controls")

ACTOR = "consensus"


class RiskControls:
    def __init__(
        self,
        risk_engine,
        order_manager,
        registry,
        venue_monitor,
        *,
        client: ConsensusClient | None = None,
        default_limits: RiskLimits | None = None,
        poll_interval_s: float = 0.5,
        mode: str = "testnet",
    ) -> None:
        self._risk = risk_engine
        self._ks = risk_engine.kill_switch
        self._orders = order_manager
        self._registry = registry
        self._venues = venue_monitor
        self._client = client
        self._defaults = default_limits or RiskLimits()
        self._poll_s = poll_interval_s
        self._mode = mode
        self._lock = asyncio.Lock()  # one write or one adoption at a time
        self._pending: dict[str, dict[str, Any]] = {}  # item -> command not yet confirmed by the cluster
        self._seen_index = 0  # highest committed log index adopted
        self._nodes: dict[int, dict[str, Any] | None] = {}
        self._last_sync: datetime | None = None
        self._warned_mode = False
        self._running = False

    @property
    def replicated(self) -> bool:
        return self._client is not None

    # ── kill switch ─────────────────────────────────────────────────
    async def engage_kill_switch(self, reason: str, operator: str) -> KillSwitchState:
        async with self._lock:
            state = await engage_kill_switch(self._ks, self._orders, self._registry, reason=reason, operator=operator)
            await self._tighten("kill_switch", _ks_command(state), f"kill switch ENGAGED by {operator}")
            return state

    async def release_kill_switch(self, operator: str) -> KillSwitchState:
        async with self._lock:
            current = self._ks.get_state()
            released = current.model_copy(update={"active": False, "last_deactivated": datetime.now(UTC)})
            reply = await self._loosen("kill_switch", _ks_command(released), _ks_command(current),
                                       f"kill switch release by {operator}")
            state = await self._ks.deactivate(deactivated_by=operator)
            await self._catch_up(reply)
            return state

    # ── risk limits (any change needs a majority) ───────────────────
    async def set_limits(self, limits: RiskLimits, actor: str) -> RiskLimits:
        async with self._lock:
            reply = await self._loosen(
                "risk_limits",
                {"op": "set_risk_limits", "limits": limits.model_dump(), "actor": actor},
                {"op": "set_risk_limits", "limits": self._risk.limits.model_dump(), "actor": "system"},
                f"risk limit change by {actor}",
            )
            result = await self._risk.update_limits(limits, actor=actor)
            await self._catch_up(reply)
            return result

    # ── venue blacklist ─────────────────────────────────────────────
    async def blacklist_venue(self, venue_id: str, actor: str) -> None:
        async with self._lock:
            await self._venues.blacklist_venue(venue_id, actor=actor)
            await self._tighten(f"blacklist:{venue_id}", _bl_command(venue_id, True, actor),
                                f"blacklist of {venue_id} by {actor}")

    async def unblacklist_venue(self, venue_id: str, actor: str) -> None:
        async with self._lock:
            was = venue_id in self.local_blacklist()
            reply = await self._loosen(f"blacklist:{venue_id}", _bl_command(venue_id, False, actor),
                                       _bl_command(venue_id, was, "system"),
                                       f"removal of {venue_id} from the blacklist by {actor}")
            await self._venues.unblacklist_venue(venue_id, actor=actor)
            await self._catch_up(reply)

    def local_blacklist(self) -> set[str]:
        return {h.venue_id for h in self._venues.get_all_health() if h.status == VenueStatus.BLACKLISTED}

    # ── writing through the cluster ─────────────────────────────────
    async def _tighten(self, key: str, command: dict[str, Any], what: str) -> None:
        if not self.replicated:
            return
        try:
            reply = await self._client.propose(command)
        except ConsensusUnavailableError as exc:
            self._pending[key] = command
            await audit("consensus_pending",
                        f"{_cap(what)} applied here at once; the RAFT cluster can't confirm it right now "
                        f"({exc.message}) — it will be replicated as soon as a majority is back",
                        actor=ACTOR, payload={"item": key, "command": command})
            logger.warning("consensus_write_pending", item=key)
            return
        self._pending.pop(key, None)
        await self._record_commit(what, reply)
        await self._catch_up(reply)

    async def _loosen(self, key: str, command: dict[str, Any], compensate: dict[str, Any],
                      what: str) -> dict[str, Any] | None:
        if not self.replicated:
            return None
        await self._flush_pending()
        if key in self._pending:  # our own earlier change to this item must land first
            raise ConsensusUnavailableError(
                f"Refused: the RAFT cluster has no majority right now, so the {what} can't be agreed. "
                "Nothing changed.")
        try:
            reply = await self._client.propose(command)
        except ConsensusUnavailableError as exc:
            # It may still commit later; re-asserting the current state after it keeps that harmless.
            self._pending[key] = compensate
            await audit("consensus_refused", f"Refused: {what} — {exc.message}. Nothing changed here.",
                        actor=ACTOR, payload={"item": key, "command": command})
            raise ConsensusUnavailableError(
                f"Refused: the {what} needs a majority of the RAFT cluster ({exc.message}). Nothing changed."
            ) from exc
        await self._record_commit(what, reply)
        return reply

    async def _flush_pending(self) -> None:
        for key, command in list(self._pending.items()):
            try:
                reply = await self._client.propose(command)
            except ConsensusUnavailableError:
                return
            self._pending.pop(key, None)
            await self._record_commit(f"{key.replace(':', ' ')} (queued while the cluster was unavailable)", reply)
            # Adopt the state as of THIS commit, so a snapshot read before it
            # (e.g. a late-committed release it overrides) is now too old to apply.
            await self._catch_up(reply)

    async def _record_commit(self, what: str, reply: dict[str, Any]) -> None:
        await audit(
            "consensus_committed",
            f"Agreed by the RAFT cluster: {what} (log index {reply.get('index')}, term {reply.get('term')})",
            actor=ACTOR, payload={"index": reply.get("index"), "term": reply.get("term")},
        )

    async def _catch_up(self, reply: dict[str, Any] | None) -> None:
        """Adopt the committed state a write reply carries (it may include others' changes)."""
        state = (reply or {}).get("state")
        if state and state.get("applied_index", 0) > self._seen_index:
            self._seen_index = state["applied_index"]
            await self._adopt(state)

    # ── reading the committed state ─────────────────────────────────
    async def _adopt(self, state: dict[str, Any]) -> None:
        """Make the local copy match the cluster, item by item (not items we're still writing)."""
        ks = state.get("kill_switch")
        if ks is not None and "kill_switch" not in self._pending:
            if ks.get("active") and not self._ks.is_active:
                await engage_kill_switch(self._ks, self._orders, self._registry,
                                         reason=ks.get("reason") or "engaged through the RAFT cluster",
                                         operator=ks.get("activated_by") or "cluster")
                await audit("consensus_adopted", "Kill switch ENGAGED here: the RAFT cluster's committed state "
                            "says so (changed by another client)", actor=ACTOR)
            elif not ks.get("active") and self._ks.is_active:
                await self._ks.deactivate(deactivated_by="RAFT cluster")
                await audit("consensus_adopted", "Kill switch RELEASED here: the RAFT cluster's committed state "
                            "says so (changed by another client)", actor=ACTOR)

        limits = state.get("risk_limits")
        if limits is not None and "risk_limits" not in self._pending:
            merged = RiskLimits.model_validate({**self._defaults.model_dump(), **limits})
            if merged != self._risk.limits:
                self._risk.limits = merged
                await audit("consensus_adopted", "Risk limits updated here from the RAFT cluster's committed state",
                            actor=ACTOR, payload={"limits": merged.model_dump()},
                            persist={"key": "risk_limits", "value": merged.model_dump()})

        cluster = state.get("blacklist") or {}
        local = self.local_blacklist()
        for vid in sorted(set(cluster) - local):
            if f"blacklist:{vid}" not in self._pending and self._venues.get_venue_health(vid):
                await self._venues.blacklist_venue(vid, actor=f"{cluster[vid].get('by') or 'cluster'} (via RAFT)")
        for vid in sorted(local - set(cluster)):
            if f"blacklist:{vid}" not in self._pending:
                await self._venues.unblacklist_venue(vid, actor="RAFT cluster")

    def _valid(self, nodes: dict[int, dict[str, Any] | None]) -> dict[int, dict[str, Any]]:
        valid = {i: s for i, s in nodes.items() if s and s.get("mode") == self._mode}
        if not self._warned_mode and any(s and s.get("mode") != self._mode for s in nodes.values()):
            self._warned_mode = True
            logger.error("raft_mode_mismatch", gateway_mode=self._mode,
                         nodes={i: s.get("mode") for i, s in nodes.items() if s})
        return valid

    async def sync(self) -> None:
        """Re-read the committed state; retry our unconfirmed writes when a leader exists."""
        nodes = await self._client.states()
        self._nodes, self._last_sync = nodes, datetime.now(UTC)
        valid = self._valid(nodes)
        async with self._lock:
            best = max(valid.values(), key=lambda s: s["state"]["applied_index"], default=None)
            if best is not None and best["state"].get("risk_limits") is None and "risk_limits" not in self._pending:
                # A brand-new cluster: the first gateway seeds it with its limits.
                self._pending["risk_limits"] = {"op": "set_risk_limits", "limits": self._risk.limits.model_dump(),
                                                "actor": "system"}
            if self._pending and any(s["role"] == "LEADER" for s in valid.values()):
                await self._flush_pending()
            if best is not None and best["state"]["applied_index"] > self._seen_index:
                self._seen_index = best["state"]["applied_index"]
                await self._adopt(best["state"])

    async def startup(self) -> None:
        """First contact after a restart. Anything this gateway knows as TIGHTER is kept and replicated."""
        if not self.replicated:
            return
        nodes = await self._client.states()
        self._nodes, self._last_sync = nodes, datetime.now(UTC)
        valid = self._valid(nodes)
        best = max(valid.values(), key=lambda s: s["state"]["applied_index"], default=None)
        cluster = best["state"] if best else {}
        async with self._lock:
            kept: list[str] = []
            ks = cluster.get("kill_switch")
            if self._ks.is_active and not (ks and ks.get("active")):
                self._pending["kill_switch"] = _ks_command(self._ks.get_state())
                kept.append("kill switch ENGAGED")
            for vid in sorted(self.local_blacklist() - set(cluster.get("blacklist") or {})):
                self._pending[f"blacklist:{vid}"] = _bl_command(vid, True, "system")
                kept.append(f"{vid} blacklisted")
            keep_note = f" Kept from this gateway's own record and replicated: {', '.join(kept)}." if kept else ""
            if best is None:
                await audit("consensus_unreachable",
                            "RAFT cluster unreachable at start-up — using this gateway's last saved risk controls "
                            f"(kill switch {'ENGAGED' if self._ks.is_active else 'off'}). Tightening is replicated "
                            "when it is back; loosening needs it." + keep_note, actor="system")
                return
        await self.sync()
        leader = next((i for i, s in valid.items() if s["role"] == "LEADER"), None)
        await audit("consensus_synced",
                    f"Risk controls synced with the RAFT cluster at start-up (log index {self._seen_index}, "
                    f"leader node {leader or 'being elected'})." + keep_note, actor="system")

    async def start(self) -> None:
        if not self.replicated:
            return
        self._running = True
        logger.info("risk_controls_sync_started", interval_s=self._poll_s)
        while self._running:
            try:
                await self.sync()
            except Exception as exc:  # noqa: BLE001 — the sync must keep running
                logger.error("risk_controls_sync_failed", error=str(exc)[:200])
            await asyncio.sleep(self._poll_s)

    async def stop(self) -> None:
        self._running = False

    # ── status for the API ──────────────────────────────────────────
    def status(self) -> ConsensusStatus:
        if not self.replicated:
            return ConsensusStatus(enabled=False, message="RAFT is off (RAFT_ENABLED=false): the kill switch, "
                                   "limits and blacklist are kept by this gateway alone.")
        nodes = []
        for i, address in self._client.nodes.items():
            s = self._nodes.get(i)
            ok = bool(s) and s.get("mode") == self._mode
            nodes.append(ConsensusNode(
                id=i, address=f"{address[0]}:{address[1]}", reachable=ok,
                role=s.get("role") if ok else None, term=s.get("term") if ok else None,
                leader=s.get("leader") if ok else None, commit_index=s.get("commit_index") if ok else None,
                applied_index=s.get("applied_index") if ok else None, pid=s.get("pid") if ok else None,
                mode=s.get("mode") if s else None,
            ))
        reachable = sum(n.reachable for n in nodes)
        leaders = [n for n in nodes if n.role == "LEADER"]
        leader = max(leaders, key=lambda n: n.term or 0).id if leaders else None
        majority = reachable >= len(nodes) // 2 + 1
        if leader is not None and majority:
            message = f"Leader: node {leader}. {reachable} of {len(nodes)} nodes up — changes can be agreed."
        elif majority:
            message = f"{reachable} of {len(nodes)} nodes up, electing a leader."
        else:
            message = (f"Only {reachable} of {len(nodes)} nodes reachable — no majority: the kill switch still "
                       "engages here at once; releasing it or changing limits is refused until a majority is back.")
        return ConsensusStatus(
            enabled=True, leader=leader, majority_reachable=majority, nodes=nodes,
            pending_writes=sorted(self._pending), synced_index=self._seen_index, last_sync=self._last_sync,
            message=message,
        )


def _ks_command(state: KillSwitchState) -> dict[str, Any]:
    return {"op": "set_kill_switch", "state": state.model_dump(mode="json")}


def _bl_command(venue_id: str, on: bool, actor: str) -> dict[str, Any]:
    return {"op": "set_blacklist", "venue_id": venue_id, "on": on, "actor": actor,
            "at": datetime.now(UTC).isoformat(timespec="seconds")}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]
