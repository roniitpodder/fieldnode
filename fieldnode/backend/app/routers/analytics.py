from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_zone_or_404

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/zones/{zone_id}/trends", response_model=List[schemas.AnalyticsPoint])
def zone_trends(zone_id: str, days: int = 7, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    zone = get_owned_zone_or_404(db, zone_id, user)
    device_ids = [d.id for d in zone.devices]
    since = datetime.utcnow() - timedelta(days=days)

    readings = (
        db.query(models.SensorReading)
        .filter(models.SensorReading.device_id.in_(device_ids), models.SensorReading.timestamp >= since)
        .order_by(models.SensorReading.timestamp.asc())
        .all()
    )
    events = (
        db.query(models.WateringEvent)
        .filter(models.WateringEvent.zone_id == zone_id, models.WateringEvent.timestamp >= since)
        .all()
    )

    # bucket watering liters by day for a lightweight overlay series
    liters_by_day = {}
    for e in events:
        day_key = e.timestamp.date()
        liters_by_day[day_key] = liters_by_day.get(day_key, 0.0) + e.amount_liters

    points = []
    for r in readings:
        points.append(schemas.AnalyticsPoint(
            timestamp=r.timestamp,
            soil_moisture=r.soil_moisture,
            temperature=r.temperature,
            water_used_liters=liters_by_day.get(r.timestamp.date()),
        ))
    return points
