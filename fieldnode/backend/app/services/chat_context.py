"""
Builds the "FIELD SNAPSHOT" that is injected into the chat assistant's system prompt on
EVERY message. The LLM never queries the database itself: it only sees what is written
here, which keeps it grounded (and makes its behaviour testable).

Principles:
  * Only real, current facts. Anything assumed / simulated / missing is labelled as such,
    so the assistant can't present a made-up number as a measurement.
  * Relative times ("3h ago") next to UTC times, because the app has no timezone setting
    and the assistant must not guess the farmer's local clock time.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services.advisor import run_advisor
from app.services.field_health import compute_field_health
from app.services.rain_sensor import get_rain_sensor_status
from app.services.water_savings import water_used_today


def _ago(now: datetime, ts: datetime) -> str:
    mins = max(0, int((now - ts).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min ago"
    if mins < 60 * 48:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)} days ago"


def _fmt(v: Optional[float], unit: str = "", nd: int = 1) -> str:
    return "not measured" if v is None else f"{v:.{nd}f}{unit}"


def build_snapshot(db: Session, zone: models.Zone) -> Tuple[str, dict, bool]:
    """Returns (snapshot_text, advisor_result, device_online)."""
    now = datetime.utcnow()
    farm = zone.farm
    device = zone.devices[0] if zone.devices else None
    lines = [f"Current time: {now:%Y-%m-%d %H:%M} UTC (month: {now:%B})."]

    # ---- farm & zone ----
    where = (f"{farm.latitude:.3f}, {farm.longitude:.3f}"
             if farm and farm.latitude is not None and farm.longitude is not None
             else "UNKNOWN (no coordinates set) \u2014 ask the farmer their region before giving region-specific advice")
    preset = zone.crop_preset
    lines += [
        "",
        f"FARM: {farm.name if farm else '?'} | location (lat, long): {where}",
        f"ZONE: {zone.name} | soil type: {zone.soil_type} | auto mode: {'on' if zone.auto_mode else 'off'}",
        (f"CROP: {preset.crop_name} (healthy moisture {preset.moisture_min:.0f}\u2013{preset.moisture_max:.0f}%)"
         if preset else "CROP: none selected for this zone"),
        f"Watering thresholds for this zone: start below {zone.moisture_threshold_low:.0f}%, "
        f"stop above {zone.moisture_threshold_high:.0f}%",
    ]

    # ---- device ----
    online = False
    if device is None:
        lines.append("DEVICE: no field node is registered for this zone.")
    else:
        online = bool(device.last_seen and
                      (now - device.last_seen).total_seconds() < settings.DEVICE_OFFLINE_AFTER_SECONDS)
        lines.append(
            f"DEVICE {device.device_code}: {'ONLINE' if online else 'OFFLINE'}"
            + (f" (last seen {_ago(now, device.last_seen)})" if device.last_seen else " (never seen)")
            + f" | pump running: {'yes' if device.pump_running else 'no'}"
            + f" | pump flow rate {device.pump_flow_rate_lpm} L/min (water amounts are ESTIMATES, "
              f"there is no flow sensor)"
        )

    # ---- latest reading ----
    latest = None
    if device:
        latest = (db.query(models.SensorReading)
                  .filter(models.SensorReading.device_id == device.id)
                  .order_by(models.SensorReading.timestamp.desc()).first())
    if latest is None:
        lines += ["", "LATEST SENSOR READING: none yet."]
    else:
        lines += [
            "",
            f"LATEST SENSOR READING ({_ago(now, latest.timestamp)}):",
            f"  soil moisture: {_fmt(latest.soil_moisture, '%')}",
            f"  temperature: {_fmt(latest.temperature, ' C')} | humidity: {_fmt(latest.humidity, '%', 0)} "
            f"| light (raw LDR): {_fmt(latest.light_level, '', 0)}",
        ]
        if latest.sensor_fault:
            lines.append("  WARNING: this reading was flagged as a sensor fault.")

    # ---- rain ----
    sensor = get_rain_sensor_status(db, device)
    rain_txt = {
        "dry": "rain sensor is DRY (not raining at the field right now)",
        "wet": "rain sensor is WET (it is raining at the field right now)",
        "suspect_stuck": f"rain sensor has read wet for {sensor['wet_hours']:.0f}h straight \u2014 probably "
                         f"faulty/corroded, being ignored",
        "offline": "rain sensor is not reporting",
        "no_data": "rain sensor has never reported",
    }[sensor["status"]]
    lines += ["", f"RAIN: {rain_txt}."]

    # ---- moisture history (real data only) ----
    if device:
        since24 = now - timedelta(hours=24)
        vals = [r.soil_moisture for r in db.query(models.SensorReading)
                .filter(models.SensorReading.device_id == device.id,
                        models.SensorReading.timestamp >= since24,
                        models.SensorReading.soil_moisture.isnot(None))
                .order_by(models.SensorReading.timestamp.asc()).all()]
        if vals:
            drift = vals[-1] - vals[0]
            trend = "rising" if drift > 3 else "falling" if drift < -3 else "roughly stable"
            lines += ["", f"SOIL MOISTURE LAST 24H: min {min(vals):.0f}%, avg {sum(vals) / len(vals):.0f}%, "
                          f"max {max(vals):.0f}% ({len(vals)} readings) \u2014 {trend}."]

        since7 = now - timedelta(days=7)
        rows = (db.query(models.SensorReading)
                .filter(models.SensorReading.device_id == device.id,
                        models.SensorReading.timestamp >= since7,
                        models.SensorReading.soil_moisture.isnot(None))
                .order_by(models.SensorReading.timestamp.asc()).all())
        days = {}
        for r in rows:
            d = days.setdefault(r.timestamp.date(), {"m": [], "rain": False})
            d["m"].append(r.soil_moisture)
            d["rain"] = d["rain"] or bool(r.rain_detected)
        if len(days) > 1:
            lines.append("DAILY SOIL MOISTURE (last 7 days, UTC dates):")
            for day in sorted(days):
                m = days[day]["m"]
                lines.append(f"  {day}: min {min(m):.0f}% / avg {sum(m) / len(m):.0f}% / max {max(m):.0f}%"
                             + ("  [rain detected]" if days[day]["rain"] else ""))

    # ---- watering history ----
    events = (db.query(models.WateringEvent)
              .filter(models.WateringEvent.zone_id == zone.id)
              .order_by(models.WateringEvent.timestamp.desc()).limit(8).all())
    lines += ["", f"WATER USED TODAY: ~{water_used_today(db, zone.id):.1f} L (estimated)"]
    if events:
        lines.append("RECENT WATERING ACTIVITY (newest first; SKIPPED = system deliberately did not water):")
        for e in events:
            kind = e.trigger_type.value.upper() if e.trigger_type else "?"
            lines.append(f"  {_ago(now, e.timestamp)} ({e.timestamp:%Y-%m-%d %H:%M} UTC): {kind}, "
                         f"~{e.amount_liters:.1f} L, {e.duration_seconds}s \u2014 {e.reason or 'no reason recorded'}")
    else:
        lines.append("RECENT WATERING ACTIVITY: none recorded.")

    # ---- the irrigation advisor's current verdict (ML + safety rules) ----
    adv = run_advisor(db, zone)
    fc = {"open-meteo": f"{adv['rain_probability']:.0f}% chance of rain today (weather forecast)",
          "override": f"{adv['rain_probability']:.0f}% chance of rain today",
          "mock": "NOT AVAILABLE (farm has no coordinates, so there is no real forecast \u2014 do not quote any "
                  "rain percentage)",
          "unavailable": "NOT AVAILABLE"}.get(adv["rain_forecast_source"], "unknown")
    lines += ["", "IRRIGATION ADVISOR VERDICT RIGHT NOW (machine-learning model + safety rules):",
              f"  should water now: {'YES' if adv['should_water'] else 'NO'}"
              + (f" \u2014 about {adv['predicted_liters']:.1f} L, pump ~{adv['recommended_duration_seconds']}s"
                 if adv["should_water"] and adv["recommended_duration_seconds"] else ""),
              f"  reason: {adv['reasoning']}",
              f"  rain forecast: {fc}"]
    if adv["warnings"]:
        lines.append("  data-quality notes (tell the farmer about any that matter):")
        lines += [f"   - {w}" for w in adv["warnings"]]

    # ---- health + crop library ----
    health = compute_field_health(db, zone)
    lines += ["", f"FIELD HEALTH SCORE: {health['score']}/100"]
    presets = db.query(models.CropPreset).order_by(models.CropPreset.crop_name).all()
    if presets:
        lines.append("CROPS ALREADY CONFIGURED IN THIS APP (healthy moisture range):")
        lines += [f"  {p.crop_name}: {p.moisture_min:.0f}\u2013{p.moisture_max:.0f}%"
                  + (f" \u2014 {p.notes}" if p.notes else "") for p in presets]

    return "\n".join(lines), adv, online
