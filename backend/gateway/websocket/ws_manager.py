"""The one live connection between the gateway and a browser.

Who may connect: only a logged-in user. The browser can't put a login header
on a WebSocket, and a token in the URL ends up in every access log, so the
login token travels in the FIRST message instead:

    client -> {"type": "auth", "token": "<login token>"}     (within 5 s)
    server -> {"type": "authenticated", "username": ..., "expires_at": ...}
    client -> {"type": "subscribe", "channels": [...]}

No auth message in time, a wrong / expired / refresh token, or anything else
first: the connection is closed with code 4401 and nothing is ever sent. When
the login expires, the server closes an open connection the same way, so a
stale session can't keep receiving orders, balances and kill-switch events.

Which websites: CORS doesn't cover WebSockets — a browser opens one from any
page, saying only which site the page is from (the Origin header). A page
from a site not allowed by CORS_ORIGINS is refused before the connection
opens (HTTP 403), the same rule as for REST. Scripts send no Origin; they
still need a login token.
"""

import asyncio
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from backend.gateway.config import settings
from backend.gateway.middleware.auth import TokenError, User, decode_access_token
from backend.shared.utils.logger import get_logger

logger = get_logger("ws_manager")

AUTH_TIMEOUT_S = 5.0
CLOSE_UNAUTHORIZED = 4401  # application close code, mirrors HTTP 401

VALID_CHANNELS = {
    "venue_health",
    "market_data",
    "nbbo_update",
    "order_update",
    "risk_alert",
    "risk_check",
    "kill_switch",
    "execution_report",
    "wallet_update",
    "algo_update",
    "arb_update",
}


class WebSocketManager:
    def __init__(self, origin_allowed: Callable[[str], bool] | None = None) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, set[str]] = defaultdict(set)
        self._user_map: dict[str, str] = {}
        self._origin_allowed = origin_allowed or settings.origin_allowed

    async def serve(self, websocket: WebSocket) -> None:
        """Run one browser connection: login handshake, then messages until it closes."""
        origin = websocket.headers.get("origin")
        if origin is not None and not self._origin_allowed(origin):
            logger.warning("ws_refused", reason="website not allowed", origin=origin, client=_client(websocket))
            await websocket.close(code=1008)  # before accept: the browser gets HTTP 403
            return
        await websocket.accept()
        try:
            user, expires_at = await self._authenticate(websocket)
        except TokenError as exc:
            logger.warning("ws_refused", reason=str(exc), client=_client(websocket))
            await _close(websocket, CLOSE_UNAUTHORIZED, str(exc))
            return
        except WebSocketDisconnect:
            return

        conn_id = str(uuid4())
        self._connections[conn_id] = websocket
        self._user_map[conn_id] = user.username
        logger.info("ws_connected", connection_id=conn_id, username=user.username)
        await self.send_personal(conn_id, {
            "type": "authenticated", "username": user.username, "expires_at": expires_at.isoformat(),
        })
        expiry = asyncio.create_task(self._close_at(conn_id, websocket, expires_at, user))
        try:
            while True:
                raw = await websocket.receive_text()
                await self.handle_client_message(conn_id, raw)
        except (WebSocketDisconnect, RuntimeError):
            pass  # RuntimeError: we closed it ourselves (login expired) mid-receive
        finally:
            expiry.cancel()
            await self.disconnect(conn_id)

    async def _authenticate(self, websocket: WebSocket) -> tuple[User, datetime]:
        """The first message must be {"type": "auth", "token": ...}, within AUTH_TIMEOUT_S."""
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_S)
        except TimeoutError as exc:
            raise TokenError(f"no login within {AUTH_TIMEOUT_S:g} s") from exc
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TokenError("the first message must be a login") from exc
        if not isinstance(msg, dict) or msg.get("type") != "auth":
            raise TokenError("the first message must be a login")
        token = msg.get("token")
        return decode_access_token(token if isinstance(token, str) else None)

    async def _close_at(self, conn_id: str, websocket: WebSocket, expires_at: datetime, user: User) -> None:
        await asyncio.sleep(max(0.0, (expires_at - datetime.now(UTC)).total_seconds()))
        logger.info("ws_login_expired", username=user.username)
        # Stop broadcasting to it at once; don't wait for the browser to acknowledge the close.
        await self.disconnect(conn_id)
        await _close(websocket, CLOSE_UNAUTHORIZED, "login expired")

    async def disconnect(self, conn_id: str) -> None:
        self._connections.pop(conn_id, None)
        self._user_map.pop(conn_id, None)
        for channel_subs in self._subscriptions.values():
            channel_subs.discard(conn_id)
        logger.info("ws_disconnected", connection_id=conn_id)

    def subscribe(self, conn_id: str, channels: list[str]) -> list[str]:
        subscribed = []
        for ch in channels:
            if ch in VALID_CHANNELS:
                self._subscriptions[ch].add(conn_id)
                subscribed.append(ch)
        return subscribed

    def unsubscribe(self, conn_id: str, channels: list[str]) -> None:
        for ch in channels:
            self._subscriptions[ch].discard(conn_id)

    async def broadcast(self, channel: str, data: dict) -> None:
        message = json.dumps({"type": channel, "data": data})
        subscribers = self._subscriptions.get(channel, set())
        dead: list[str] = []

        for conn_id in subscribers:
            ws = self._connections.get(conn_id)
            if not ws:
                dead.append(conn_id)
                continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(conn_id)

        for conn_id in dead:
            await self.disconnect(conn_id)

    async def send_personal(self, conn_id: str, data: dict) -> None:
        ws = self._connections.get(conn_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                await self.disconnect(conn_id)

    async def handle_client_message(self, conn_id: str, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "subscribe":
            channels = msg.get("channels", [])
            subscribed = self.subscribe(conn_id, channels)
            await self.send_personal(
                conn_id, {"type": "subscribed", "channels": subscribed}
            )

        elif msg_type == "unsubscribe":
            channels = msg.get("channels", [])
            self.unsubscribe(conn_id, channels)
            await self.send_personal(
                conn_id, {"type": "unsubscribed", "channels": channels}
            )

        elif msg_type == "ping":
            from datetime import UTC, datetime

            await self.send_personal(
                conn_id,
                {"type": "pong", "timestamp": datetime.now(UTC).isoformat()},
            )

    @property
    def connection_count(self) -> int:
        return len(self._connections)


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])  # a close reason is capped at 123 bytes
    except (RuntimeError, WebSocketDisconnect):
        pass  # already closed by the other side


def _client(websocket: WebSocket) -> str:
    client = websocket.client
    return f"{client.host}:{client.port}" if client else "?"
