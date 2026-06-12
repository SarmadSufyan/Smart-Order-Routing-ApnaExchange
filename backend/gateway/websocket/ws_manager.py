import asyncio
import json
from collections import defaultdict
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from backend.shared.utils.logger import get_logger

logger = get_logger("ws_manager")

VALID_CHANNELS = {
    "venue_health",
    "market_data",
    "nbbo_update",
    "order_update",
    "risk_alert",
    "risk_check",
    "kill_switch",
    "execution_report",
}


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, set[str]] = defaultdict(set)
        self._user_map: dict[str, str] = {}

    async def connect(self, websocket: WebSocket, user_id: str = "") -> str:
        await websocket.accept()
        conn_id = str(uuid4())
        self._connections[conn_id] = websocket
        self._user_map[conn_id] = user_id
        logger.info("ws_connected", connection_id=conn_id, user_id=user_id)
        return conn_id

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
