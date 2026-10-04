"""Every channel the gateway forwards to the browser must be one a client may subscribe to."""

import unittest

from backend.gateway.main import WS_CHANNELS
from backend.gateway.websocket.ws_manager import VALID_CHANNELS


class WebSocketChannelsTest(unittest.TestCase):
    def test_every_forwarded_channel_can_be_subscribed(self) -> None:
        # Found 2026-10-04: algo_update was forwarded but not subscribable, so the
        # server silently refused the frontend's subscription to it.
        self.assertEqual(set(WS_CHANNELS) - VALID_CHANNELS, set())


if __name__ == "__main__":
    unittest.main()
