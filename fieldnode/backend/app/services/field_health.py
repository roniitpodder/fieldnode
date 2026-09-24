from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app import models
from app.config import settings
from app.services.rain_sensor import get_rain_sensor_status


def compute_field_health(db: Session, zone: models.Zone) -> dict:
    """
    Score out of 100, docked for: devices offline, recent sensor faults,
    low battery health, rain sensor stuck "wet". Mirrors the "Field Health 98/100 —
    All sensors reporting" card in the dashboard.
    """
    devices = zone.devices
    if not devices:
        return {"score": 0, "sensors_reporting_pct": 0.0, "all_reporting": False}

    score = 100
    online_count = 0
    fault_count = 0
    total_recent_readings = 0

    since = datetime.utcnow() - timedelta(hours=6)

    for device in devices:
        is_online = bool(
            device.last_seen
            and (datetime.utcnow() - device.last_seen).total_seconds() < settings.DEVICE_OFFLINE_AFTER_SECONDS
        )
        if is_online:
            online_count += 1
        else:
            score -= 15

        if device.battery_health < 30:
            score -= 10

        recent = [r for r in device.readings if r.timestamp >= since]
        total_recent_readings += len(recent)
        fault_count += sum(1 for r in recent if r.sensor_fault)

        if get_rain_sensor_status(db, device)["status"] == "suspect_stuck":
            score -= 10  # rain plate reads wet for hours: likely corroded/shorted

    if fault_count:
        score -= min(20, fault_count * 5)

    score = max(0, min(100, score))
    sensors_reporting_pct = round((online_count / len(devices)) * 100, 1) if devices else 0.0

    return {
        "score": score,
        "sensors_reporting_pct": sensors_reporting_pct,
        "all_reporting": online_count == len(devices),
    }
