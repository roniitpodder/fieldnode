from sqlalchemy.orm import Session
from app import models
from app.config import settings


def _owner_user_id(db: Session, device: models.Device):
    zone = db.query(models.Zone).filter(models.Zone.id == device.zone_id).first()
    if not zone:
        return None
    farm = db.query(models.Farm).filter(models.Farm.id == zone.farm_id).first()
    return farm.owner_id if farm else None


def _notify(db: Session, user_id: str, type_: models.NotificationType, message: str):
    if not user_id:
        return
    n = models.Notification(user_id=user_id, type=type_, message=message)
    db.add(n)
    db.commit()


def maybe_notify_from_reading(db: Session, device: models.Device, reading: models.SensorReading):
    user_id = _owner_user_id(db, device)

    if reading.sensor_fault:
        _notify(db, user_id, models.NotificationType.SENSOR_FAULT,
                f"Sensor fault reported by {device.device_code}.")

    # Rain: notify only on the dry -> wet TRANSITION, not on every reading while it rains.
    if reading.rain_detected:
        prev = (
            db.query(models.SensorReading)
            .filter(models.SensorReading.device_id == device.id,
                    models.SensorReading.id != reading.id,
                    models.SensorReading.rain_detected.isnot(None))
            .order_by(models.SensorReading.timestamp.desc())
            .first()
        )
        was_dry = (
            prev is None
            or not prev.rain_detected
            or (reading.timestamp - prev.timestamp).total_seconds() > settings.RAIN_SENSOR_FRESH_SECONDS
        )
        if was_dry:
            _notify(db, user_id, models.NotificationType.RAIN_SKIP,
                    f"Rain detected at {device.device_code}. Automatic watering is on hold "
                    f"until the rain sensor dries.")
