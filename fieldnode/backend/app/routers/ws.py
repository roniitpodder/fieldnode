from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.security import decode_access_token
from app import models
from app.services.ownership import get_owned_zone_or_404
from app.services.ws_manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/zones/{zone_id}")
async def zone_telemetry_ws(websocket: WebSocket, zone_id: str, token: str = Query(...)):
    """
    Browser clients can't set custom Authorization headers on a WebSocket
    handshake, so the JWT is passed as a query param instead:
        wss://<host>/ws/zones/<zone_id>?token=<jwt>
    Pushes JSON messages for new sensor readings and pump state changes as
    they happen — the frontend's "Live telemetry" panel should open this
    instead of polling /api/overview on an interval.
    """
    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=4401)
        return

    db: Session = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.id == payload.get("sub")).first()
        if not user:
            await websocket.close(code=4401)
            return
        try:
            get_owned_zone_or_404(db, zone_id, user)
        except Exception:
            await websocket.close(code=4403)
            return
    finally:
        db.close()

    await manager.connect(zone_id, websocket)
    try:
        while True:
            # We don't expect inbound messages, but need to await something
            # to detect disconnects; a client ping/pong keepalive is fine too.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(zone_id, websocket)
