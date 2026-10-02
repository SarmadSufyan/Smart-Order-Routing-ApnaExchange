"""Talking to the RAFT cluster: address parsing and the client the gateway
(and the demo script) use.

Writes go to the leader. A node that isn't the leader answers "not_leader"
with its best guess of who is; the client follows that hint, falls back to
trying every node, and gives up after ``write_timeout_s`` with
``ConsensusUnavailableError`` (no leader with a majority right now).

Reads ask every node for its committed state at once and keep the one with
the highest applied log index. Committed entries never change, so a higher
index is always the fresher truth — no need to find the leader for a read.

Every network call has a short timeout: on Windows, connecting to a dead
localhost port takes ~2 s to fail, which would otherwise stall everything.
"""

import asyncio
import json
from typing import Any

from backend.shared.exceptions import DomainError

CONNECT_TIMEOUT_S = 0.3
LINE_LIMIT = 1 << 20  # bytes per JSON line (an append of 100 entries is far smaller)


class ConsensusUnavailableError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "CONSENSUS_UNAVAILABLE")


def parse_cluster(spec: str) -> dict[int, tuple[str, int]]:
    """``"127.0.0.1:9001,127.0.0.1:9002,..."`` -> {1: (host, port), 2: ...} (ids by position).

    ``localhost`` becomes 127.0.0.1: on Windows it may resolve to IPv6 first,
    and the refused IPv6 attempt costs ~2 s per connection.
    """
    nodes: dict[int, tuple[str, int]] = {}
    for i, part in enumerate((p.strip() for p in spec.split(",") if p.strip()), start=1):
        host, _, port = part.rpartition(":")
        host = "127.0.0.1" if host in ("", "localhost") else host
        nodes[i] = (host, int(port))
    if len(nodes) < 1:
        raise ValueError("RAFT_CLUSTER_NODES is empty")
    return nodes


async def request(address: tuple[str, int], payload: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
    """One request/reply over a fresh connection. None = no (valid) answer."""
    host, port = address
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=LINE_LIMIT), timeout=min(timeout_s, CONNECT_TIMEOUT_S),
        )
    except (OSError, TimeoutError):
        return None
    try:
        writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout_s)
        line = await asyncio.wait_for(reader.readline(), timeout_s)
        reply = json.loads(line) if line else None
        return reply if isinstance(reply, dict) else None
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
        return None
    finally:
        writer.close()


class ConsensusClient:
    def __init__(
        self,
        nodes: dict[int, tuple[str, int]],
        *,
        read_timeout_s: float = 0.5,
        write_timeout_s: float = 3.0,
        commit_wait_s: float = 2.0,
    ) -> None:
        self.nodes = nodes
        self._read_timeout = read_timeout_s
        self._write_timeout = write_timeout_s
        self._commit_wait = commit_wait_s  # how long a leader waits for a majority (node side)
        self.leader_hint: int | None = None

    async def states(self) -> dict[int, dict[str, Any] | None]:
        """Every node's status + committed state (None = unreachable)."""
        ids = list(self.nodes)
        replies = await asyncio.gather(*(request(self.nodes[i], {"op": "state"}, self._read_timeout) for i in ids))
        out = {i: (r if r and r.get("ok") else None) for i, r in zip(ids, replies, strict=True)}
        leaders = [i for i, r in out.items() if r and r.get("role") == "LEADER"]
        if leaders:
            self.leader_hint = max(leaders, key=lambda i: out[i]["term"])
        return out

    async def propose(self, command: dict[str, Any]) -> dict[str, Any]:
        """Commit a command through the leader. Returns the node's reply on success."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout
        last_error = "no node answered"
        while True:
            order = list(self.nodes)
            if self.leader_hint in self.nodes:
                order.remove(self.leader_hint)
                order.insert(0, self.leader_hint)
            for node_id in order:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ConsensusUnavailableError(
                        f"The RAFT cluster could not commit this change in {self._write_timeout:g} s "
                        f"({last_error}) — no leader with a majority right now"
                    )
                reply = await request(self.nodes[node_id], {"op": "propose", "command": command},
                                      min(remaining, self._commit_wait + 0.5))
                if reply is None:
                    last_error = f"node {node_id} unreachable"
                    continue
                if reply.get("ok"):
                    self.leader_hint = node_id
                    return reply
                error = reply.get("error")
                if error == "not_leader":
                    last_error = "election in progress" if reply.get("leader") is None else last_error
                    if reply.get("leader") in self.nodes and reply["leader"] != node_id:
                        self.leader_hint = reply["leader"]
                        break  # restart the round at the hinted leader
                elif error == "bad_command":
                    raise ValueError(f"RAFT node rejected the command: {reply.get('detail')}")
                else:
                    last_error = f"node {node_id}: {error}"
            else:
                await asyncio.sleep(0.1)  # a full round without success: let an election finish
