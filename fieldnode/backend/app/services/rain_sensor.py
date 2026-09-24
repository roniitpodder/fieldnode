"""
Interprets the physical rain sensor (a water-detection plate used as a rain sensor).

The ESP32 reports `rain_detected` (bool, True = plate wet) with each reading. Cheap
plates have two failure modes this module guards against:

  * Offline / no data  -> we can't claim it's dry OR wet; status "offline"/"no_data".
  * Stuck wet          -> corrosion, a short, dew, or pump splash keeps it "wet" for
                          hours. If that vetoed watering forever, crops would silently
                          go dry. After RAIN_SENSOR_STUCK_HOURS of continuous "wet"
                          the veto is lifted and the sensor is flagged suspect.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models
from app.config import settings


def get_rain_sensor_status(db: Session, device: Optional[models.Device]) -> dict:
    """
    Returns:
      status:       dry | wet | suspect_stuck | offline | no_data
      raining_now:  True only for a FRESH "wet" reading from a sensor that isn't suspect
      intensity:    latest 0-100 wetness, if reported
      wet_hours:    how long it has been continuously wet (0 if dry)
    """
    result = {"status": "no_data", "raining_now": False, "intensity": None, "wet_hours": 0.0}
    if device is None:
        return result

    latest = (
        db.query(models.SensorReading)
        .filter(models.SensorReading.device_id == device.id,
                models.SensorReading.rain_detected.isnot(None))
        .order_by(models.SensorReading.timestamp.desc())
        .first()
    )
    if latest is None:
        return result  # firmware never reported the rain sensor

    now = datetime.utcnow()
    age = (now - latest.timestamp).total_seconds()
    result["intensity"] = latest.rain_intensity
    if age > settings.RAIN_SENSOR_FRESH_SECONDS:
        result["status"] = "offline"
        return result

    if not latest.rain_detected:
        result["status"] = "dry"
        return result

    # Wet now: how long has it been continuously wet? Walk back to the last dry reading.
    lookback = now - timedelta(hours=settings.RAIN_SENSOR_STUCK_HOURS + 1)
    last_dry = (
        db.query(models.SensorReading.timestamp)
        .filter(models.SensorReading.device_id == device.id,
                models.SensorReading.rain_detected.is_(False),
                models.SensorReading.timestamp >= lookback)
        .order_by(models.SensorReading.timestamp.desc())
        .first()
    )
    if last_dry is not None:
        wet_since = last_dry[0]
    else:
        # no dry reading in the whole lookback window -> wet at least since its start,
        # but only trust that if we actually have readings covering the window
        oldest = (
            db.query(models.SensorReading.timestamp)
            .filter(models.SensorReading.device_id == device.id,
                    models.SensorReading.rain_detected.isnot(None),
                    models.SensorReading.timestamp >= lookback)
            .order_by(models.SensorReading.timestamp.asc())
            .first()
        )
        wet_since = oldest[0] if oldest else latest.timestamp
    wet_hours = (now - wet_since).total_seconds() / 3600
    result["wet_hours"] = round(wet_hours, 2)

    if wet_hours >= settings.RAIN_SENSOR_STUCK_HOURS:
        result["status"] = "suspect_stuck"   # veto lifted; caller should warn
    else:
        result["status"] = "wet"
        result["raining_now"] = True
    return result
