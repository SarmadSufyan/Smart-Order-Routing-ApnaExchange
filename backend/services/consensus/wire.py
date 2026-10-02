"""Signed messages: every line between RAFT nodes, and between a client and a
node, carries an HMAC-SHA256 signature made with a shared secret.

Why: the nodes listen on TCP. Without signatures, any program on the machine
could send "release the kill switch" straight to the leader, or pretend to be
a node and feed the gateway a fake state. With them, a message counts only if
whoever wrote it knows ``RAFT_SHARED_SECRET`` (from .env; never sent anywhere).

Line format:  ``<64 hex chars = HMAC-SHA256(secret, json)> <json>\\n``
The signature covers the exact JSON bytes sent, so nothing needs canonicalising.

Replays (sending a genuine old message again): every message carries the
sender's clock (``ts``) and is refused when more than ``max_age_s`` old (the
nodes share the machine's clock, or NTP across machines). Client requests also
carry a one-time ``nonce`` that the node remembers for that window and echoes
in its signed reply, so neither an old request nor an old answer can be played
back as a new one.

Production systems use mutual TLS (a certificate per node) instead of one
shared secret; the principle — no message counts unless its sender proves who
it is — is the same.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

MAX_AGE_S = 30.0


class MessageSigner:
    """Encodes and checks one JSON message per line. ``secret=None`` = unsigned (unit tests only)."""

    def __init__(self, secret: str | None, *, max_age_s: float = MAX_AGE_S,
                 clock: Callable[[], float] = time.time) -> None:
        self._key = secret.encode() if secret else None
        self.max_age_s = max_age_s
        self._clock = clock

    @property
    def signing(self) -> bool:
        return self._key is not None

    def encode(self, payload: dict[str, Any]) -> bytes:
        body = json.dumps({**payload, "ts": round(self._clock(), 3)}, separators=(",", ":"))
        if self._key is None:
            return (body + "\n").encode()
        return f"{self._sign(body)} {body}\n".encode()

    def decode(self, line: bytes) -> dict[str, Any] | None:
        """The message, or None if it is forged, too old, or not a JSON object."""
        try:
            text = line.decode().strip()
        except UnicodeDecodeError:
            return None
        body = text
        if self._key is not None:
            signature, _, body = text.partition(" ")
            if not hmac.compare_digest(signature.encode(), self._sign(body).encode()):
                return None
        try:
            msg = json.loads(body)
        except ValueError:
            return None
        if not isinstance(msg, dict):
            return None
        if self._key is not None:
            ts = msg.get("ts")
            if not isinstance(ts, int | float) or abs(self._clock() - ts) > self.max_age_s:
                return None
        return msg

    def _sign(self, body: str) -> str:
        return hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()


class NonceCache:
    """Request ids already seen inside the replay window."""

    def __init__(self, max_age_s: float = MAX_AGE_S, clock: Callable[[], float] = time.time) -> None:
        self._seen: dict[str, float] = {}
        self._max_age = max_age_s
        self._clock = clock

    def first_time(self, nonce: str) -> bool:
        now = self._clock()
        if len(self._seen) > 1_000:
            self._seen = {n: t for n, t in self._seen.items() if now - t <= self._max_age}
        if nonce in self._seen and now - self._seen[nonce] <= self._max_age:
            return False
        self._seen[nonce] = now
        return True
