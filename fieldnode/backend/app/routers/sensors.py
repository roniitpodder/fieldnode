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
    """Called by the ESP32 field node itself (authenticated via X-Device-Key header,
    not user JWT) to push a new sensor sample."""
    reading = models.SensorReading(device_id=device.id, **payload.model_dump())
    device.last_seen = datetime.utcnow()
    db.add(reading)
    db.commit()
    db.refresh(reading)

    maybe_notify_from_reading(db, device, reading)

    await manager.broadcast(device.zone_id, {
        "event": "reading",
        "device_id": device.id,
        "soil_moisture": reading.soil_moisture,
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "light_level": reading.light_level,
        "sunlight_pct": sunlight_percent(reading.light_level),
        "rain_detected": reading.rain_detected,
        "rain_intensity": reading.rain_intensity,
        "timestamp": reading.timestamp,
    })
    return reading


@router.get("/zones/{zone_id}/readings", response_model=List[schemas.SensorReadingOut])
def get_zone_readings(
    zone_id: str,
    hours: int = 24,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    zone = get_owned_zone_or_404(db, zone_id, user)
    device_ids = [d.id for d in zone.devices]
    since = datetime.utcnow() - timedelta(hours=hours)
    readings = (
        db.query(models.SensorReading)
        .filter(models.SensorReading.device_id.in_(device_ids), models.SensorReading.timestamp >= since)
        .order_by(models.SensorReading.timestamp.asc())
        .all()
    )
    return readings


@router.get("/zones/{zone_id}/readings/latest", response_model=schemas.SensorReadingOut)
def get_latest_reading(
    zone_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    zone = get_owned_zone_or_404(db, zone_id, user)
    device_ids = [d.id for d in zone.devices]
    reading = (
        db.query(models.SensorReading)
        .filter(models.SensorReading.device_id.in_(device_ids))
        .order_by(models.SensorReading.timestamp.desc())
        .first()
    )
    if not reading:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No readings yet for this zone")
    return reading
