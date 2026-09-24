"""Shared AI-advisor core: gathers a zone's live inputs, runs the ML advisor + safety
rules, and returns a result dict. Used by /api/ai/advise, /api/ai/generate-schedule
and the AI chat assistant, so all three always agree."""
import math
from datetime import datetime, timedelta, date

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services.rain import get_rain_forecast
from app.services.rain_sensor import get_rain_sensor_status
from app.ml import advisor_model

# Used only when the device doesn't report a value. Every use is surfaced in `warnings`
# so the dashboard never presents an assumed number as a measurement.
DEFAULTS = {"temperature": 28.0, "humidity": 55.0, "light_level": 500.0}


def run_advisor(db: Session, zone: models.Zone, rain_override: float = None) -> dict:
    """Gathers live inputs for the zone, runs the ML advisor, returns a result dict
    (advisor output + recommended_time + warnings). Shared by /advise and /generate-schedule."""
    warnings = []
    now = datetime.utcnow()
    device = zone.devices[0] if zone.devices else None

    latest = None
    if device:
        latest = (
            db.query(models.SensorReading)
            .filter(models.SensorReading.device_id == device.id)
            .order_by(models.SensorReading.timestamp.desc())
            .first()
        )

    # ---- rain: forecast (weather API) + physical sensor (rain plate) ----
    if rain_override is not None:
        rain_probability, rain_source = rain_override, "override"
    else:
        fc = get_rain_forecast(
            zone.id, date.today(),
            latitude=zone.farm.latitude if zone.farm else None,
            longitude=zone.farm.longitude if zone.farm else None,
        )
        rain_source = fc.source
        if fc.probability is None:
            rain_probability = 0.0
            warnings.append("Rain forecast unavailable (no farm coordinates or weather API "
                            "unreachable) \u2014 forecast-based rain skipping is off; only the "
                            "rain sensor is protecting against rain.")
        else:
            rain_probability = fc.probability
            if rain_source == "mock":
                warnings.append("Rain forecast is SIMULATED (farm has no coordinates or the "
                                "weather API is unreachable) \u2014 set the farm's latitude/longitude "
                                "for a real forecast.")

    sensor = get_rain_sensor_status(db, device)
    if sensor["status"] == "suspect_stuck":
        warnings.append(f"Rain sensor has read 'wet' for {sensor['wet_hours']:.0f}h straight \u2014 it "
                        f"may be corroded/shorted. Ignoring it for now; please inspect the plate.")
    elif sensor["status"] in ("offline", "no_data"):
        warnings.append("Rain sensor is not reporting \u2014 relying on the forecast only.")
    raining_now = sensor["raining_now"]

    # ---- soil moisture is essential: without it we don't water blind ----
    if latest is None or latest.soil_moisture is None:
        return {
            "should_water": False, "predicted_liters": 0.0, "recommended_duration_seconds": None,
            "rain_probability": rain_probability, "rain_skip": False, "raining_now": raining_now,
            "rain_forecast_source": rain_source, "confidence": 0.0,
            "reasoning": "No soil-moisture reading is available from the field node yet, so the "
                         "advisor can't make a safe recommendation. Watering is not being scheduled blind.",
            "warnings": warnings + ["No soil moisture data."],
            "recommended_time": now + timedelta(hours=1),
        }

    age = (now - latest.timestamp).total_seconds()
    if age > settings.READING_STALE_SECONDS:
        warnings.append(f"Latest sensor reading is {age / 60:.0f} minutes old \u2014 "
                        f"is the field node offline?")
    if latest.sensor_fault:
        warnings.append("The latest reading was flagged as a sensor fault; treat this advice with caution.")

    def pick(name):
        val = getattr(latest, name)
        if val is None:
            warnings.append(f"{name.replace('_', ' ').capitalize()} not reported by the device "
                            f"\u2014 assumed {DEFAULTS[name]:g}.")
            return DEFAULTS[name]
        return val

    # last *real* watering: skipped cycles and never-completed manual commands don't count
    last_watering = (
        db.query(models.WateringEvent)
        .filter(models.WateringEvent.zone_id == zone.id,
                models.WateringEvent.trigger_type != models.TriggerType.SKIPPED,
                models.WateringEvent.amount_liters > 0)
        .order_by(models.WateringEvent.timestamp.desc())
        .first()
    )
    hours_since = (now - last_watering.timestamp).total_seconds() / 3600 if last_watering else 48.0

    result = advisor_model.predict(
        soil_moisture=latest.soil_moisture,
        temperature=pick("temperature"),
        humidity=pick("humidity"),
        rain_probability=rain_probability,
        hours_since_last_watering=hours_since,
        light_level=pick("light_level"),
        moisture_min=zone.moisture_threshold_low,
        moisture_max=zone.moisture_threshold_high,
        raining_now=raining_now,
    )

    # Liters -> pump run time (there's no flow sensor; the ESP32 can only run by time)
    duration = None
    if result["should_water"] and result["predicted_liters"] > 0:
        rate = (device.pump_flow_rate_lpm if device and device.pump_flow_rate_lpm else
                settings.PUMP_DEFAULT_FLOW_LPM)
        duration = max(1, math.ceil(result["predicted_liters"] / rate * 60))
        if duration > settings.MAX_PUMP_RUN_SECONDS:
            duration = settings.MAX_PUMP_RUN_SECONDS
            warnings.append(f"Recommended amount needs more than the {settings.MAX_PUMP_RUN_SECONDS}s "
                            f"per-run cap; run duration was capped \u2014 another cycle will be needed.")

    result.update({
        "recommended_duration_seconds": duration,
        "rain_forecast_source": rain_source,
        "warnings": warnings,
        "recommended_time": now if result["should_water"] else now + timedelta(hours=12),
    })
    return result
