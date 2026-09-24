import json
import logging
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger("fieldnode.ws")


class ConnectionManager:
    """
    Keeps track of WebSocket clients per zone_id and pushes JSON messages to
    them as new sensor readings / pump state changes arrive, so the dashboard
    doesn't have to poll for "live telemetry".

    In-process only (single-worker deployments). For multi-worker/production
    deployments, swap this for a Redis pub/sub-backed broadcaster instead —
    the router code below doesn't need to change, only this class's internals.
    """

    def __init__(self):
        self._zone_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, zone_id: str, websocket: WebSocket):
        await websocket.accept()
        self._zone_connections.setdefault(zone_id, []).append(websocket)

    def disconnect(self, zone_id: str, websocket: WebSocket):
        conns = self._zone_connections.get(zone_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and zone_id in self._zone_connections:
            del self._zone_connections[zone_id]

    async def broadcast(self, zone_id: str, message: dict):
        conns = self._zone_connections.get(zone_id, [])
        dead = []
        for ws in conns:
            try:
                await ws.send_text(json.dumps(message, default=str))
            except Exception:  # noqa: BLE001 — connection dropped mid-send
                dead.append(ws)
        for ws in dead:
            self.disconnect(zone_id, ws)


manager = ConnectionManager()
