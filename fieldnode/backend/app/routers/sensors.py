from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, get_device_from_api_key
from app.services.ownership import get_owned_zone_or_404
from app.services.notify import maybe_notify_from_reading
from app.services.ws_manager import manager
from app.services.sunlight import sunlight_percent

router = APIRouter(prefix="/api", tags=["sensors"])


@router.post("/ingest/reading", response_model=schemas.SensorReadingOut)
async def ingest_reading(
    payload: schemas.SensorReadingIn,
    db: Session = Depends(get_db),
    device: models.Device = Depends(get_device_from_api_key),
):
    """
    Called by the ESP32 field node to push a new sensor sample.

    The ESP32 may report pump_is_on and pump_source, but these are
    device-state fields and are NOT columns in SensorReading.
    """

    # Convert Pydantic payload to a normal dictionary
    data = payload.model_dump()

    # ---------------------------------------------------------
    # Device-state fields reported by ESP32
    # ---------------------------------------------------------
    pump_is_on = data.pop("pump_is_on", None)
    pump_source = data.pop("pump_source", None)

    # ---------------------------------------------------------
    # Create database SensorReading using ONLY fields that
    # actually belong to the SensorReading SQLAlchemy model.
    # ---------------------------------------------------------
    reading = models.SensorReading(
        device_id=device.id,
        **data,
    )

    # Device heartbeat
    device.last_seen = datetime.utcnow()

    # ---------------------------------------------------------
    # PHYSICAL PUMP STATE
    # ---------------------------------------------------------

    # Track whether the physical pump state actually changed.
    pump_state_changed = False

    if pump_is_on is not None:

        new_state = bool(pump_is_on)

        if device.pump_running != new_state:
            device.pump_running = new_state
            pump_state_changed = True

    # ---------------------------------------------------------
    # PUMP SOURCE
    # ---------------------------------------------------------

    if pump_source is not None:

        if hasattr(device, "pending_command_source"):
            device.pending_command_source = pump_source

    # ---------------------------------------------------------
    # SAVE READING
    # ---------------------------------------------------------

    db.add(reading)
    db.commit()
    db.refresh(reading)

    # ---------------------------------------------------------
    # PHYSICAL PUMP STATE BROADCAST
    # ---------------------------------------------------------

    # If physical pump state changed, broadcast immediately.
    if pump_state_changed:

        await manager.broadcast(
            device.zone_id,
            {
                "event": "pump_state",
                "pump_running": device.pump_running,
                "source": pump_source or "telemetry",
            },
        )

    # ---------------------------------------------------------
    # NOTIFICATIONS
    # ---------------------------------------------------------

    maybe_notify_from_reading(
        db,
        device,
        reading,
    )

    # ---------------------------------------------------------
    # WEBSOCKET READING UPDATE
    # ---------------------------------------------------------

    await manager.broadcast(
        device.zone_id,
        {
            "event": "reading",
            "device_id": device.id,
            "soil_moisture": reading.soil_moisture,
            "temperature": reading.temperature,
            "humidity": reading.humidity,
            "light_level": reading.light_level,
            "sunlight_pct": sunlight_percent(
                reading.light_level
            ),
            "rain_detected": reading.rain_detected,
            "rain_intensity": reading.rain_intensity,
            "pump_is_on": pump_is_on,
            "pump_source": pump_source,
            "timestamp": reading.timestamp,
        },
    )

    return reading


@router.get(
    "/zones/{zone_id}/readings",
    response_model=List[schemas.SensorReadingOut],
)
def get_zone_readings(
    zone_id: str,
    hours: int = 24,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    zone = get_owned_zone_or_404(
        db,
        zone_id,
        user,
    )

    device_ids = [
        d.id
        for d in zone.devices
    ]

    since = (
        datetime.utcnow()
        - timedelta(hours=hours)
    )

    readings = (
        db.query(models.SensorReading)
        .filter(
            models.SensorReading.device_id.in_(device_ids),
            models.SensorReading.timestamp >= since,
        )
        .order_by(
            models.SensorReading.timestamp.asc()
        )
        .all()
    )

    return readings


@router.get(
    "/zones/{zone_id}/readings/latest",
    response_model=schemas.SensorReadingOut,
)
def get_latest_reading(
    zone_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    zone = get_owned_zone_or_404(
        db,
        zone_id,
        user,
    )

    device_ids = [
        d.id
        for d in zone.devices
    ]

    reading = (
        db.query(models.SensorReading)
        .filter(
            models.SensorReading.device_id.in_(device_ids)
        )
        .order_by(
            models.SensorReading.timestamp.desc()
        )
        .first()
    )

    if not reading:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="No readings yet for this zone",
        )

    return reading