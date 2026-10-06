"""One RAFT node process: the algorithm (``core.py``) + a timer + TCP + the
risk state machine.

    python -m backend.services.consensus.node --id 1     (ids 1..3, by position
                                                          in RAFT_CLUSTER_NODES)

``start_all.py`` starts all three when ``RAFT_ENABLED=true``.

Wire format: one SIGNED JSON object per line (``wire.py``: HMAC-SHA256 with
RAFT_SHARED_SECRET, a time stamp, and a one-time nonce on client requests),
TCP on 127.0.0.1. A forged, too-old or replayed line is refused and counted.
  * node -> node: RAFT messages (``vote_req``/``vote_resp``/``append_req``/
    ``append_resp``), one-way: a reply is just another message back. A lost
    message is harmless — the next heartbeat repeats whatever is missing.
  * client -> node: ``{"op": "propose" | "state" | "status"}`` -> one reply line.
    ``propose`` answers only once the command is committed (a majority has
    stored it) and applied, or says why not (not the leader / no majority).
"""

import argparse
import asyncio
import os
import signal
from pathlib import Path
from typing import Any

from backend.shared.utils.logger import get_logger, setup_logging

from .client import LINE_LIMIT, parse_cluster
from .core import NotLeaderError, RaftCore, Role
from .state_machine import OPS, RiskStateMachine
from .storage import SqliteStorage
from .wire import MessageSigner, NonceCache

logger = get_logger("raft_node")

CONNECT_TIMEOUT_S = 0.3
RECONNECT_DELAY_S = 0.2
QUEUE_LIMIT = 500  # per peer; when a peer is down, old heartbeats are dropped


class LostLeadershipError(Exception):
    pass


class RaftNode:
    def __init__(
        self,
        node_id: int,
        nodes: dict[int, tuple[str, int]],
        storage,
        *,
        tick_s: float = 0.05,
        election_ticks: int = 10,
        heartbeat_ticks: int = 2,
        commit_wait_s: float = 2.0,
        mode: str = "testnet",
        rng=None,
        signer: MessageSigner | None = None,
    ) -> None:
        self.id = node_id
        self._signer = signer or MessageSigner(None)
        self._nonces = NonceCache(self._signer.max_age_s)
        self.rejected = 0  # forged / too old / replayed lines refused
        self.nodes = nodes
        self.mode = mode
        self.core = RaftCore(node_id, list(nodes), storage, election_ticks=election_ticks,
                             heartbeat_ticks=heartbeat_ticks, rng=rng)
        self.sm = RiskStateMachine()
        self._tick_s = tick_s
        self._commit_wait = commit_wait_s
        self._pending: dict[int, tuple[int, asyncio.Future]] = {}  # log index -> (term, waiter)
        self._queues: dict[int, asyncio.Queue] = {p: asyncio.Queue(QUEUE_LIMIT) for p in self.core.peers}
        self._server: asyncio.base_events.Server | None = None
        self._tasks: list[asyncio.Task] = []
        self._writers: set[asyncio.StreamWriter] = set()  # open connections, closed on stop
        self._running = False
        self._seen = (self.core.role, self.core.term, self.core.leader_id)
        self._apply_committed()  # rebuild the state from the committed log (survives restarts)

    # ── lifecycle ───────────────────────────────────────────────────
    async def start(self) -> None:
        host, port = self.nodes[self.id]
        self._server = await asyncio.start_server(self._on_connection, host, port, limit=LINE_LIMIT)
        self._running = True
        self._tasks = [asyncio.create_task(self._ticker())]
        self._tasks += [asyncio.create_task(self._sender(p)) for p in self.core.peers]
        logger.info("raft_node_started", node=self.id, address=f"{host}:{port}", term=self.core.term,
                    log=self.core.last_index, commit=self.core.commit_index, pid=os.getpid())

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            # Peers keep their connections open; since Python 3.12 wait_closed()
            # waits for every one of them, so close them first.
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()

    async def serve_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    # ── the loop around the algorithm ───────────────────────────────
    async def _ticker(self) -> None:
        while self._running:
            await asyncio.sleep(self._tick_s)
            self.core.tick()
            self._pump()

    def _pump(self) -> None:
        """After anything happened: send what the core wants sent, apply what committed."""
        for msg in self.core.drain_outbox():
            queue = self._queues.get(msg["to"])
            if queue is not None:
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    pass  # peer is down or slow; RAFT re-sends through the next heartbeat
        self._apply_committed()
        now = (self.core.role, self.core.term, self.core.leader_id)
        if now != self._seen:
            logger.info("raft_role", node=self.id, role=now[0].value, term=now[1], leader=now[2],
                        commit=self.core.commit_index)
            self._seen = now
        if self.core.role != Role.LEADER and self._pending:
            for _, fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(LostLeadershipError())
            self._pending.clear()

    def _apply_committed(self) -> None:
        for index, entry in self.core.take_committed():
            result = self.sm.apply(index, entry.term, entry.command)
            waiter = self._pending.pop(index, None)
            if waiter is not None and not waiter[1].done():
                if waiter[0] == entry.term:
                    waiter[1].set_result(result)
                else:  # a different leader's entry took this slot: ours was dropped
                    waiter[1].set_exception(LostLeadershipError())

    async def _sender(self, peer: int) -> None:
        host, port = self.nodes[peer]
        queue = self._queues[peer]
        writer: asyncio.StreamWriter | None = None
        while self._running:
            msg = await queue.get()
            if writer is None:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT_S)
                    self._writers.add(writer)
                except (OSError, TimeoutError):
                    while not queue.empty():  # stale by the time the peer is back
                        queue.get_nowait()
                    await asyncio.sleep(RECONNECT_DELAY_S)
                    continue
            try:
                writer.write(self._signer.encode(msg))
                await asyncio.wait_for(writer.drain(), 1.0)
            except (OSError, TimeoutError):
                writer.close()
                self._writers.discard(writer)
                writer = None

    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                msg = self._signer.decode(line)
                if msg is None:
                    self._refuse("bad signature, too old, or not a JSON object")
                    break
                if "type" in msg:  # a peer's RAFT message: one-way
                    self.core.step(msg)
                    self._pump()
                    continue
                nonce = msg.get("nonce")
                if self._signer.signing and (not isinstance(nonce, str) or not self._nonces.first_time(nonce)):
                    self._refuse("request replayed (nonce already used) or without a nonce")
                    break
                reply = await self._client_op(msg)
                writer.write(self._signer.encode({**reply, "nonce": nonce}))
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _refuse(self, reason: str) -> None:
        self.rejected += 1
        logger.warning("raft_message_refused", node=self.id, reason=reason, total=self.rejected)

    # ── client operations ───────────────────────────────────────────
    def info(self) -> dict[str, Any]:
        return {
            "ok": True, "node": self.id, "role": self.core.role.value, "term": self.core.term,
            "leader": self.core.leader_id, "commit_index": self.core.commit_index,
            "last_index": self.core.last_index, "applied_index": self.sm.applied_index,
            "pid": os.getpid(), "mode": self.mode, "signed": self._signer.signing, "rejected": self.rejected,
        }

    async def _client_op(self, msg: dict[str, Any]) -> dict[str, Any]:
        op = msg.get("op")
        if op == "status":
            return self.info()
        if op == "state":
            return {**self.info(), "state": self.sm.snapshot()}
        if op != "propose":
            return {"ok": False, "error": "unknown_op"}
        command = msg.get("command")
        if not isinstance(command, dict) or command.get("op") not in OPS - {"noop"}:
            return {"ok": False, "error": "bad_command", "detail": f"unknown command {command!r:.80}"}
        try:
            index, term = self.core.propose(command)
        except NotLeaderError as exc:
            return {"ok": False, "error": "not_leader", "leader": exc.leader_id}
        waiter = asyncio.get_running_loop().create_future()
        self._pending[index] = (term, waiter)
        self._pump()
        try:
            result = await asyncio.wait_for(waiter, self._commit_wait)
        except TimeoutError:
            self._pending.pop(index, None)
            return {"ok": False, "error": "no_majority", "index": index}
        except LostLeadershipError:
            return {"ok": False, "error": "not_leader", "leader": self.core.leader_id}
        return {"ok": True, **result, "state": self.sm.snapshot()}


def main() -> None:
    from backend.gateway.config import settings

    parser = argparse.ArgumentParser(description="Run one ApnaSOR RAFT node")
    parser.add_argument("--id", type=int, required=True, help="1-based position in RAFT_CLUSTER_NODES")
    parser.add_argument("--nodes", default=settings.raft_cluster_nodes)
    parser.add_argument("--data-dir", default=settings.resolved_raft_data_dir)
    args = parser.parse_args()
    if problem := settings.raft_secret_problem():
        parser.error(problem)

    setup_logging(debug=False)
    nodes = parse_cluster(args.nodes)
    if args.id not in nodes:
        parser.error(f"--id must be one of {sorted(nodes)}")
    if hasattr(signal, "SIGBREAK"):
        # Windows: start_all.py stops a node with Ctrl+Break (Ctrl+C can't be sent to one
        # process group). Turn it into a Ctrl+C, which asyncio.run handles by cancelling
        # the main task — raising KeyboardInterrupt straight from the handler could hit the
        # event loop mid-callback, and a node hung on its way out (seen live, 1 of 3 nodes).
        signal.signal(signal.SIGBREAK, lambda *_: signal.raise_signal(signal.SIGINT))
    storage = SqliteStorage(Path(args.data_dir) / f"node-{args.id}.db")
    node = RaftNode(
        args.id, nodes, storage,
        tick_s=settings.raft_tick_ms / 1000, election_ticks=settings.raft_election_ticks,
        heartbeat_ticks=settings.raft_heartbeat_ticks, mode=settings.exchange_mode,
        signer=MessageSigner(settings.raft_shared_secret.get_secret_value()),
    )
    try:
        asyncio.run(node.serve_forever())
    except KeyboardInterrupt:
        logger.info("raft_node_stopped", node=args.id, term=node.core.term, commit=node.core.commit_index)
    finally:
        storage.close()


if __name__ == "__main__":
    main()
