"""RAFT consensus — the algorithm itself, with no network and no clock.

Ongaro & Ousterhout, "In Search of an Understandable Consensus Algorithm"
(2014). Three processes keep the same numbered LOG of commands; a command
counts (is *committed*) once a majority has stored it, and then every node
applies it, in log order, to the same state machine.

    FOLLOWER --(no heartbeat for a random timeout)--> CANDIDATE
    CANDIDATE --(votes from a majority)--> LEADER --(sees a higher term)--> FOLLOWER

Why it is safe (the three rules that matter):
  * one vote per term, and only for a candidate whose log is at least as
    up to date as yours -> a leader always holds every committed command;
  * a follower accepts new entries only if its log matches the leader's at
    the entry just before them -> logs never fork once they agree;
  * a leader commits by counting only entries of its OWN term (§5.4.2) —
    older ones are committed indirectly. That is why a new leader appends a
    no-op first.

Design: this class is deterministic. The runtime (``node.py``) calls
``tick()`` on a timer, feeds received messages to ``step()``, sends whatever
lands in ``outbox`` and applies ``take_committed()``. Tests drive it the
same way, message by message — elections and crashes are reproducible.

State that must survive a crash (term, vote, log, and — safely, since a
committed entry is never undone — the commit index) is written to storage
BEFORE any message that depends on it is put in the outbox.

Added beyond the paper's core, both small: check-quorum (a leader that can't
reach a majority for an election timeout steps down, so a cut-off leader
stops claiming leadership) and a follower hint on a failed append (the
leader jumps back to the follower's log end instead of one entry per round).
"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

MAX_ENTRIES_PER_APPEND = 100


class Role(str, Enum):
    FOLLOWER = "FOLLOWER"
    CANDIDATE = "CANDIDATE"
    LEADER = "LEADER"


@dataclass(frozen=True, slots=True)
class Entry:
    term: int
    command: dict[str, Any]


class NotLeaderError(Exception):
    def __init__(self, leader_id: int | None) -> None:
        super().__init__(f"not the leader (leader: {leader_id})")
        self.leader_id = leader_id


class Storage(Protocol):
    """Durable state of one node. Every method must be durable on return."""

    def load(self) -> tuple[int, int | None, list[Entry], int]: ...  # term, voted_for, log, commit
    def save_term_vote(self, term: int, voted_for: int | None) -> None: ...
    def append(self, first_index: int, entries: list[Entry]) -> None: ...
    def truncate_from(self, index: int) -> None: ...  # delete entries >= index
    def save_commit(self, commit_index: int) -> None: ...


class RaftCore:
    def __init__(
        self,
        node_id: int,
        peers: list[int],
        storage: Storage,
        *,
        election_ticks: int = 10,
        heartbeat_ticks: int = 2,
        rng: random.Random | None = None,
    ) -> None:
        self.id = node_id
        self.peers = [p for p in peers if p != node_id]
        self.quorum = (len(self.peers) + 1) // 2 + 1
        self._storage = storage
        self.election_ticks = election_ticks
        self.heartbeat_ticks = heartbeat_ticks
        self._rng = rng or random.Random()

        self.term, self.voted_for, self.log, commit = storage.load()
        self.commit_index = min(commit, len(self.log))
        self.last_applied = 0  # the state machine is rebuilt from the log at start-up
        self.role = Role.FOLLOWER
        self.leader_id: int | None = None
        self.outbox: list[dict[str, Any]] = []

        self._elapsed = 0
        self._timeout = self._random_timeout()
        self._heartbeat_elapsed = 0
        self._votes: set[int] = set()
        self.next_index: dict[int, int] = {}
        self.match_index: dict[int, int] = {}
        self._active: set[int] = set()  # peers heard from this check-quorum period

    # ── log helpers (indexes are 1-based; 0 = "before the first entry") ─
    @property
    def last_index(self) -> int:
        return len(self.log)

    def term_at(self, index: int) -> int:
        return self.log[index - 1].term if 1 <= index <= len(self.log) else 0

    def _random_timeout(self) -> int:
        return self._rng.randrange(self.election_ticks, 2 * self.election_ticks)

    def _send(self, to: int, msg_type: str, **fields: Any) -> None:
        self.outbox.append({"type": msg_type, "term": self.term, "from": self.id, "to": to, **fields})

    def drain_outbox(self) -> list[dict[str, Any]]:
        out, self.outbox = self.outbox, []
        return out

    # ── time ────────────────────────────────────────────────────────
    def tick(self) -> None:
        if self.role == Role.LEADER:
            self._heartbeat_elapsed += 1
            if self._heartbeat_elapsed >= self.heartbeat_ticks:
                self._heartbeat_elapsed = 0
                self._broadcast_append()
            self._elapsed += 1
            if self._elapsed >= self.election_ticks:  # check-quorum
                self._elapsed = 0
                if len(self._active) + 1 < self.quorum:
                    self._become_follower(self.term, None)
                self._active = set()
            return
        self._elapsed += 1
        if self._elapsed >= self._timeout:
            self._start_election()

    # ── elections ───────────────────────────────────────────────────
    def _start_election(self) -> None:
        self.role = Role.CANDIDATE
        self.term += 1
        self.voted_for = self.id
        self._storage.save_term_vote(self.term, self.voted_for)
        self.leader_id = None
        self._votes = {self.id}
        self._elapsed, self._timeout = 0, self._random_timeout()
        if len(self._votes) >= self.quorum:  # a one-node cluster
            self._become_leader()
            return
        for peer in self.peers:
            self._send(peer, "vote_req", last_log_index=self.last_index, last_log_term=self.term_at(self.last_index))

    def _become_follower(self, term: int, leader_id: int | None) -> None:
        if term != self.term:
            self.term, self.voted_for = term, None
            self._storage.save_term_vote(self.term, self.voted_for)
        self.role = Role.FOLLOWER
        self.leader_id = leader_id
        self._elapsed, self._timeout = 0, self._random_timeout()

    def _become_leader(self) -> None:
        self.role = Role.LEADER
        self.leader_id = self.id
        self.next_index = {p: self.last_index + 1 for p in self.peers}
        self.match_index = {p: 0 for p in self.peers}
        self._active = set()
        self._elapsed = self._heartbeat_elapsed = 0
        # A no-op in the new term lets earlier entries commit (§5.4.2) and
        # tells everyone at once who leads.
        self._append_local({"op": "noop"})
        self._broadcast_append()
        self._maybe_commit()

    # ── client commands ─────────────────────────────────────────────
    def propose(self, command: dict[str, Any]) -> tuple[int, int]:
        """Append a command (leader only). Returns (index, term) to wait on."""
        if self.role != Role.LEADER:
            raise NotLeaderError(self.leader_id)
        index = self._append_local(command)
        self._broadcast_append()
        self._maybe_commit()
        return index, self.term

    def _append_local(self, command: dict[str, Any]) -> int:
        entry = Entry(self.term, command)
        self._storage.append(self.last_index + 1, [entry])
        self.log.append(entry)
        return self.last_index

    # ── replication ─────────────────────────────────────────────────
    def _broadcast_append(self) -> None:
        for peer in self.peers:
            self._send_append(peer)

    def _send_append(self, peer: int) -> None:
        prev = self.next_index[peer] - 1
        entries = self.log[prev:prev + MAX_ENTRIES_PER_APPEND]
        self._send(peer, "append_req", prev_index=prev, prev_term=self.term_at(prev),
                   entries=[[e.term, e.command] for e in entries], commit=self.commit_index)

    def _maybe_commit(self) -> None:
        for n in range(self.last_index, self.commit_index, -1):
            if self.term_at(n) != self.term:
                break  # only entries of the current term are committed by counting
            if 1 + sum(1 for p in self.peers if self.match_index.get(p, 0) >= n) >= self.quorum:
                self._set_commit(n)
                break

    def _set_commit(self, index: int) -> None:
        if index > self.commit_index:
            self.commit_index = index
            self._storage.save_commit(index)

    def take_committed(self) -> list[tuple[int, Entry]]:
        """Committed entries not yet applied, in log order (apply each exactly once)."""
        out = [(i, self.log[i - 1]) for i in range(self.last_applied + 1, self.commit_index + 1)]
        self.last_applied = self.commit_index
        return out

    # ── messages ────────────────────────────────────────────────────
    def step(self, msg: dict[str, Any]) -> None:
        if msg.get("to") != self.id or msg.get("from") not in self.peers:
            return
        term = int(msg.get("term", 0))
        if term > self.term:
            leader = msg["from"] if msg["type"] == "append_req" else None
            self._become_follower(term, leader)
        handler = {
            "vote_req": self._on_vote_req,
            "vote_resp": self._on_vote_resp,
            "append_req": self._on_append_req,
            "append_resp": self._on_append_resp,
        }.get(msg["type"])
        if handler is not None:
            handler(msg)

    def _on_vote_req(self, msg: dict[str, Any]) -> None:
        candidate = msg["from"]
        up_to_date = (msg["last_log_term"], msg["last_log_index"]) >= (self.term_at(self.last_index), self.last_index)
        granted = (msg["term"] == self.term and self.voted_for in (None, candidate) and up_to_date)
        if granted:
            self.voted_for = candidate
            self._storage.save_term_vote(self.term, self.voted_for)
            self._elapsed = 0  # granting a vote counts as hearing from a would-be leader
        self._send(candidate, "vote_resp", granted=granted)

    def _on_vote_resp(self, msg: dict[str, Any]) -> None:
        if self.role != Role.CANDIDATE or msg["term"] != self.term or not msg["granted"]:
            return
        self._votes.add(msg["from"])
        if len(self._votes) >= self.quorum:
            self._become_leader()

    def _on_append_req(self, msg: dict[str, Any]) -> None:
        leader = msg["from"]
        if msg["term"] < self.term:
            self._send(leader, "append_resp", success=False, hint=self.last_index)
            return
        # Same term: there is exactly one leader per term, and it is this one.
        if self.role != Role.FOLLOWER or self.leader_id != leader:
            self._become_follower(self.term, leader)
        self._elapsed = 0

        prev, prev_term = msg["prev_index"], msg["prev_term"]
        if prev > self.last_index or self.term_at(prev) != prev_term:
            # Log doesn't match here: tell the leader where to try next.
            self._send(leader, "append_resp", success=False, hint=min(self.last_index, prev - 1))
            return

        index = prev
        new: list[Entry] = []
        for term, command in msg["entries"]:
            index += 1
            if new:
                new.append(Entry(term, command))
            elif index > self.last_index:
                new.append(Entry(term, command))
            elif self.term_at(index) != term:
                # Conflict: everything from here on was never committed; drop it.
                self._storage.truncate_from(index)
                del self.log[index - 1:]
                new.append(Entry(term, command))
        if new:
            first = self.last_index + 1
            self._storage.append(first, new)
            self.log.extend(new)
        last_new = prev + len(msg["entries"])
        self._set_commit(min(msg["commit"], last_new))
        self._send(leader, "append_resp", success=True, match_index=last_new)

    def _on_append_resp(self, msg: dict[str, Any]) -> None:
        if self.role != Role.LEADER or msg["term"] != self.term:
            return
        peer = msg["from"]
        self._active.add(peer)
        if msg["success"]:
            if msg["match_index"] > self.match_index[peer]:
                self.match_index[peer] = msg["match_index"]
            self.next_index[peer] = self.match_index[peer] + 1
            self._maybe_commit()
            if self.next_index[peer] <= self.last_index:
                self._send_append(peer)  # still behind: keep the catch-up going
        else:
            self.next_index[peer] = max(1, min(self.next_index[peer] - 1, msg["hint"] + 1))
            self._send_append(peer)
