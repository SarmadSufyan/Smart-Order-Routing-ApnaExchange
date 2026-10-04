"""Signed RAFT messages: HMAC-SHA256, time window, one-time nonces."""

import unittest

from backend.services.consensus.wire import MessageSigner, NonceCache


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class MessageSignerTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        s = MessageSigner("secret-0123456789abcdef")
        line = s.encode({"op": "state", "nonce": "abc"})
        self.assertRegex(line.decode(), r"^[0-9a-f]{64} \{")  # signature, space, JSON
        msg = s.decode(line)
        self.assertEqual((msg["op"], msg["nonce"]), ("state", "abc"))

    def test_wrong_secret_tampering_and_garbage_are_refused(self) -> None:
        s = MessageSigner("secret-0123456789abcdef")
        line = s.encode({"op": "propose", "command": {"op": "set_kill_switch", "state": {"active": True}}})
        self.assertIsNone(MessageSigner("another-secret-000000000").decode(line))
        self.assertIsNone(s.decode(line.replace(b"true", b"false")))  # one word changed
        self.assertIsNone(s.decode(b'{"op": "state"}\n'))  # no signature
        self.assertIsNone(s.decode(b"\xff\xfe\n"))
        self.assertIsNone(s.decode(s.encode({"x": 1}).replace(b"{", b"[", 1)))

    def test_messages_outside_the_time_window_are_refused(self) -> None:
        clock = Clock()
        s = MessageSigner("secret-0123456789abcdef", max_age_s=30, clock=clock)
        line = s.encode({"op": "state"})
        clock.now += 29
        self.assertIsNotNone(s.decode(line))
        clock.now += 2  # 31 s old
        self.assertIsNone(s.decode(line))
        clock.now -= 100  # from "the future" (a badly wrong clock) is refused too
        self.assertIsNone(s.decode(line))

    def test_unsigned_mode_is_plain_json(self) -> None:
        s = MessageSigner(None)
        self.assertFalse(s.signing)
        self.assertEqual(s.decode(s.encode({"a": 1}))["a"], 1)


class NonceCacheTest(unittest.TestCase):
    def test_a_nonce_works_once_inside_the_window(self) -> None:
        clock = Clock()
        cache = NonceCache(max_age_s=30, clock=clock)
        self.assertTrue(cache.first_time("n1"))
        self.assertFalse(cache.first_time("n1"))
        self.assertTrue(cache.first_time("n2"))


if __name__ == "__main__":
    unittest.main()
