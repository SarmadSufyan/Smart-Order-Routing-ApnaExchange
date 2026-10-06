"""The live-events WebSocket: logged-in users only, token never in the URL, closed at expiry."""

import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from jose import jwt
from starlette.websockets import WebSocketDisconnect

from backend.gateway.config import settings
from backend.gateway.middleware import auth as auth_module
from backend.gateway.middleware.auth import (
    HARDCODED_USERS,
    TokenError,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from backend.gateway.websocket import ws_manager as ws_module
from backend.gateway.websocket.ws_manager import CLOSE_UNAUTHORIZED, WebSocketManager

TEST_KEY = "test-signing-key-" + "k" * 48  # the gateway loads its key at start-up; tests set one
_key_patch = mock.patch.object(auth_module, "_signing_key", TEST_KEY)


def setUpModule() -> None:
    _key_patch.start()


def tearDownModule() -> None:
    _key_patch.stop()


def login_token(seconds: float | None = None) -> str:
    admin = authenticate_user("admin", HARDCODED_USERS["admin"]["password"])
    if seconds is None:
        return create_access_token(admin)
    payload = {"sub": admin.id, "username": admin.username, "role": admin.role.value, "name": admin.name,
               "exp": datetime.now(UTC) + timedelta(seconds=seconds)}
    return jwt.encode(payload, TEST_KEY, algorithm=settings.jwt_algorithm)


class WebSocketAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = WebSocketManager()
        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await self.manager.serve(websocket)

        self.client = TestClient(app)

    def assert_refused(self, ws, reason: str) -> None:
        with self.assertRaises(WebSocketDisconnect) as ctx:
            ws.receive_json()
        self.assertEqual(ctx.exception.code, CLOSE_UNAUTHORIZED)
        self.assertIn(reason, ctx.exception.reason)
        self.assertEqual(self.manager.connection_count, 0)

    def test_a_valid_login_can_subscribe(self) -> None:
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": login_token()})
            hello = ws.receive_json()
            self.assertEqual((hello["type"], hello["username"]), ("authenticated", "admin"))
            ws.send_json({"type": "subscribe", "channels": ["order_update", "kill_switch"]})
            self.assertEqual(ws.receive_json(), {"type": "subscribed", "channels": ["order_update", "kill_switch"]})
            self.assertEqual(self.manager.connection_count, 1)

    def test_no_login_gets_nothing(self) -> None:
        # The old hole: subscribing without any token used to work.
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "subscribe", "channels": ["order_update"]})
            self.assert_refused(ws, "first message must be a login")

    def test_a_token_in_the_url_is_not_a_login(self) -> None:
        # Tokens in URLs leak into access logs; only the first message counts.
        with self.client.websocket_connect(f"/ws?token={login_token()}") as ws:
            ws.send_json({"type": "subscribe", "channels": ["order_update"]})
            self.assert_refused(ws, "first message must be a login")

    def test_wrong_expired_and_refresh_tokens_are_refused(self) -> None:
        admin = authenticate_user("admin", HARDCODED_USERS["admin"]["password"])
        cases = {
            "invalid login token": login_token()[:-4] + "AAAA",  # bad signature
            "login expired": login_token(seconds=-5),
            "refresh token is not a login": create_refresh_token(admin),
            "no login token": "",
        }
        for reason, token in cases.items():
            with self.subTest(reason), self.client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "auth", "token": token})
                self.assert_refused(ws, reason)

    def test_silence_is_refused(self) -> None:
        with mock.patch.object(ws_module, "AUTH_TIMEOUT_S", 0.2), self.client.websocket_connect("/ws") as ws:
            self.assert_refused(ws, "no login within")

    def test_open_connection_is_closed_when_the_login_expires(self) -> None:
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": login_token(seconds=1.5)})
            self.assertEqual(ws.receive_json()["type"], "authenticated")
            started = time.monotonic()
            self.assert_refused(ws, "login expired")
            self.assertLess(time.monotonic() - started, 5)


class WebSocketOriginTest(unittest.TestCase):
    """CORS doesn't cover WebSockets: /ws checks the page's website itself."""

    def setUp(self) -> None:
        self.manager = WebSocketManager()  # the real rule: this machine's frontend
        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await self.manager.serve(websocket)

        self.client = TestClient(app)

    def test_our_frontend_and_scripts_get_in(self) -> None:
        for headers in ({"origin": "http://localhost:5173"}, {"origin": "http://127.0.0.1:5174"}, {}):
            with self.subTest(headers), self.client.websocket_connect("/ws", headers=headers) as ws:
                ws.send_json({"type": "auth", "token": login_token()})
                self.assertEqual(ws.receive_json()["type"], "authenticated")

    def test_another_website_is_refused_before_the_connection_opens(self) -> None:
        for origin in ("https://evil.example", "http://localhost.evil.example", "null"):
            with self.subTest(origin), self.assertRaises(WebSocketDisconnect) as ctx:
                with self.client.websocket_connect("/ws", headers={"origin": origin}):
                    pass
            self.assertEqual(ctx.exception.code, 1008)
        self.assertEqual(self.manager.connection_count, 0)


class DecodeAccessTokenTest(unittest.TestCase):
    def test_a_token_forged_with_the_old_public_secret_is_refused(self) -> None:
        # The hole: the old default secret was written in config.py (public on GitHub),
        # so anyone could sign "I am admin" without a password.
        payload = {"sub": "user_001", "username": "admin", "role": "ADMIN", "name": "x",
                   "exp": datetime.now(UTC) + timedelta(minutes=5)}
        forged = jwt.encode(payload, "apna-exchange-poc-secret-change-in-prod", algorithm="HS256")
        with self.assertRaises(TokenError):
            decode_access_token(forged)

    def test_refresh_tokens_decode_to_their_user_and_logins_are_not_refresh_tokens(self) -> None:
        admin = authenticate_user("admin", HARDCODED_USERS["admin"]["password"])
        self.assertEqual(decode_refresh_token(create_refresh_token(admin)), admin.id)
        with self.assertRaises(TokenError):
            decode_refresh_token(login_token())

    def test_returns_the_user_and_expiry(self) -> None:
        user, expires_at = decode_access_token(login_token())
        self.assertEqual(user.username, "admin")
        self.assertGreater(expires_at, datetime.now(UTC))

    def test_missing_fields_are_invalid_not_a_crash(self) -> None:
        # Used to raise KeyError (HTTP 500) for a well-signed token without a username.
        token = jwt.encode({"sub": "x", "exp": datetime.now(UTC) + timedelta(minutes=5)},
                           TEST_KEY, algorithm=settings.jwt_algorithm)
        with self.assertRaises(TokenError):
            decode_access_token(token)


if __name__ == "__main__":
    unittest.main()
