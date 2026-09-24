"""
Automatic irrigation controller.

AUTO MODE rules:

START:
    soil moisture < zone.moisture_threshold_low
    AND sunlight >= zone.sunlight_threshold
    AND rain <= 50%
    AND sensors are healthy/fresh
    AND no command is already pending

STOP:
    pump is reported running AND ANY safety/target condition becomes true:
        soil moisture >= zone.moisture_threshold_high
        OR sunlight < zone.sunlight_threshold
        OR rain > 50%
        OR sensor data becomes invalid/stale

IMPORTANT:
    This service ONLY QUEUES commands for the ESP32.
    It does NOT claim that the physical pump changed state.

    device.pump_running must represent the ACTUAL physical pump state
    reported by the ESP32 telemetry/acknowledgement endpoint.
"""

import asyncio
import logging
from datetime import datetime, timedelta, date

from app import models
from app.config import settings
from app.database import SessionLocal
from app.services.rain_sensor import get_rain_sensor_status
from app.services.rain import get_rain_forecast
from app.services.sunlight import sunlight_percent


logger = logging.getLogger("fieldnode.auto_watering")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [auto-watering] %(message)s"
        )
    )
    logger.addHandler(_handler)
    logger.propagate = False


# ============================================================================
# CONSTANTS
# ============================================================================

RAIN_STOP_THRESHOLD = 50.0


# ============================================================================
# HELPERS
# ============================================================================

def _latest_reading(db, device):
    """Return the newest sensor reading for a device."""
    return (
        db.query(models.SensorReading)
        .filter(
            models.SensorReading.device_id == device.id
        )
        .order_by(
            models.SensorReading.timestamp.desc()
        )
        .first()
    )


def _get_sunlight_threshold(zone) -> float:
    """Safely read and clamp the zone sunlight threshold."""
    try:
        value = float(zone.sunlight_threshold)
    except (AttributeError, TypeError, ValueError):
        value = 30.0

    return max(0.0, min(100.0, value))


def _get_rain_intensity(rain_status) -> float:
    """
    Extract rain intensity safely.

    Supports both:
        rain_intensity
    and:
        intensity
    """

    if not rain_status:
        return 0.0

    value = rain_status.get("rain_intensity")

    if value is None:
        value = rain_status.get("intensity")

    try:
        return max(
            0.0,
            min(
                100.0,
                float(value or 0.0)
            )
        )
    except (TypeError, ValueError):
        return 0.0


def _rain_is_blocking(rain_status) -> bool:
    """
    Rain protection rule.

    IMPORTANT:
    The ESP32 uses the same rule:

        rain > 50% -> block/stop pump

    We intentionally do NOT use a separate raining_now boolean here,
    because that could make backend and ESP32 decisions disagree.
    """

    rain_intensity = _get_rain_intensity(
        rain_status
    )

    return (
        rain_intensity >
        RAIN_STOP_THRESHOLD
    )


# ============================================================================
# QUEUE START
# ============================================================================

def _queue_start(
    db,
    device,
    zone,
    soil,
    sun_pct,
):
    """
    Queue START command for ESP32.

    IMPORTANT:
    This does NOT set device.pump_running=True.

    The ESP32 must receive the command, activate the relay,
    and then report the actual physical pump state.
    """

    # Never overwrite another pending command.
    if device.pending_command:
        logger.info(
            "zone '%s' (%s): START not queued -- "
            "existing command=%s",
            zone.name,
            zone.id,
            device.pending_command,
        )
        return False

    duration = getattr(
        settings,
        "AUTO_WATER_RUN_SECONDS",
        300,
    )

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 300

    if duration <= 0:
        duration = 300

    # ESP32 maximum safety limit.
    duration = min(
        duration,
        300
    )

    device.pending_command = "start"
    device.pending_command_duration = duration
    device.pending_command_source = "auto"

    db.commit()

    logger.info(
        "zone '%s' (%s): QUEUED START -- "
        "soil %.1f%% < low %.1f%%, "
        "sunlight %.1f%% >= required %.1f%%, "
        "rain <= %.1f%%, "
        "duration=%ss",
        zone.name,
        zone.id,
        soil,
        float(zone.moisture_threshold_low),
        sun_pct,
        _get_sunlight_threshold(zone),
        RAIN_STOP_THRESHOLD,
        duration,
    )

    return True


# ============================================================================
# QUEUE STOP
# ============================================================================

def _queue_stop(
    db,
    device,
    zone,
    soil,
    reason,
):
    """
    Queue STOP command for ESP32.

    IMPORTANT:
    This does NOT set device.pump_running=False.

    The ESP32 must physically stop the relay and then report
    the actual state.
    """

    # Stop already waiting.
    if device.pending_command == "stop":
        logger.info(
            "zone '%s' (%s): STOP already queued",
            zone.name,
            zone.id,
        )
        return False

    # If START is waiting but conditions have become unsafe,
    # replace START with STOP.
    if device.pending_command == "start":
        logger.warning(
            "zone '%s' (%s): replacing pending START with STOP -- %s",
            zone.name,
            zone.id,
            reason,
        )

    device.pending_command = "stop"
    device.pending_command_duration = 0
    device.pending_command_source = "auto"

    db.commit()

    logger.warning(
        "zone '%s' (%s): QUEUED STOP -- "
        "soil=%.1f%%, reason=%s",
        zone.name,
        zone.id,
        soil,
        reason,
    )

    return True


# ============================================================================
# MAIN DECISION ENGINE
# ============================================================================

def _check_zone(db, zone) -> str:
    """
    Evaluate one AUTO MODE zone.

    START:

        soil < low
        AND sunlight >= threshold
        AND rain <= 50%

    STOP:

        pump is running
        AND (
            soil >= high
            OR sunlight < threshold
            OR rain > 50%
            OR sensor invalid/stale
        )

    Returns:

        "start" -> START command queued
        "stop"  -> STOP command queued
        "none"  -> no command required
    """

    tag = (
        f"zone '{zone.name}' ({zone.id})"
    )

    # ========================================================================
    # 1. AUTO MODE
    # ========================================================================

    if not zone.auto_mode:

        logger.info(
            "%s: skipped -- auto_mode is OFF",
            tag,
        )

        return "none"

    # ========================================================================
    # 2. DEVICE
    # ========================================================================

    if not zone.devices:

        logger.warning(
            "%s: skipped -- no registered device",
            tag,
        )

        return "none"

    device = zone.devices[0]

    # ========================================================================
    # 3. LATEST SENSOR READING
    # ========================================================================

    latest = _latest_reading(
        db,
        device
    )

    if latest is None:

        logger.warning(
            "%s: skipped -- no sensor readings",
            tag,
        )

        return "none"

    # ========================================================================
    # 4. SENSOR FAULT
    # ========================================================================

    if latest.sensor_fault:

        logger.warning(
            "%s: sensor_fault=true",
            tag,
        )

        # Fail safe if physical pump is reported running.
        if device.pump_running:

            if _queue_stop(
                db,
                device,
                zone,
                float(
                    latest.soil_moisture or 0.0
                ),
                "Sensor fault",
            ):
                return "stop"

        return "none"

    # ========================================================================
    # 5. SOIL DATA VALIDATION
    # ========================================================================

    if latest.soil_moisture is None:

        logger.warning(
            "%s: soil moisture unavailable",
            tag,
        )

        # If pump is running and soil data disappears,
        # fail safe.
        if device.pump_running:

            if _queue_stop(
                db,
                device,
                zone,
                0.0,
                "Soil moisture unavailable",
            ):
                return "stop"

        return "none"

    # ========================================================================
    # 6. SENSOR FRESHNESS
    # ========================================================================

    age = (
        datetime.utcnow()
        - latest.timestamp
    ).total_seconds()

    # Use the stricter of the two limits so the auto-watering
    # controller cannot continue using readings beyond the
    # device offline threshold.
    stale_limit = min(
        float(settings.READING_STALE_SECONDS),
        float(settings.DEVICE_OFFLINE_AFTER_SECONDS),
    )

    if age > stale_limit:

        logger.warning(
            "%s: sensor data stale -- "
            "%.0fs > %ss",
            tag,
            age,
            stale_limit,
        )

        if device.pump_running:

            if _queue_stop(
                db,
                device,
                zone,
                float(
                    latest.soil_moisture
                ),
                "Stale sensor data",
            ):
                return "stop"

        return "none"

    # ========================================================================
    # 7. CURRENT VALUES
    # ========================================================================

    soil = float(
        latest.soil_moisture
    )

    low_threshold = float(
        zone.moisture_threshold_low
    )

    high_threshold = float(
        zone.moisture_threshold_high
    )

    sunlight_threshold = (
        _get_sunlight_threshold(zone)
    )

    # IMPORTANT:
    # sunlight_percent() must match the ESP32's lightPct calculation.
    sun_pct = sunlight_percent(
        latest.light_level
    )

    # ========================================================================
    # 8. SUNLIGHT VALIDATION
    # ========================================================================

    if sun_pct is None:

        logger.warning(
            "%s: sunlight unavailable",
            tag,
        )

        # Never allow a running AUTO pump to continue
        # when sunlight information disappears.
        if device.pump_running:

            if _queue_stop(
                db,
                device,
                zone,
                soil,
                "Sunlight unavailable",
            ):
                return "stop"

        return "none"

    sun_pct = float(
        sun_pct
    )

    # ========================================================================
    # 9. RAIN STATUS
    # ========================================================================

    rain_status = get_rain_sensor_status(
        db,
        device,
    )

    rain_intensity = _get_rain_intensity(
        rain_status
    )

    rain_blocked = _rain_is_blocking(
        rain_status
    )

    # ========================================================================
    # 10. PUMP CURRENTLY RUNNING
    # ========================================================================

    if device.pump_running:

        # --------------------------------------------------------------------
        # SAFETY STOP: RAIN
        # --------------------------------------------------------------------

        if rain_blocked:

            reason = (
                f"Rain protection "
                f"(rain={rain_intensity:.1f}%)"
            )

            if device.pending_command == "stop":

                logger.info(
                    "%s: STOP already pending -- %s",
                    tag,
                    reason,
                )

                return "none"

            if _queue_stop(
                db,
                device,
                zone,
                soil,
                reason,
            ):
                return "stop"

            return "none"

        # --------------------------------------------------------------------
        # SAFETY STOP: LOW SUNLIGHT
        # --------------------------------------------------------------------

        if sun_pct < sunlight_threshold:

            reason = (
                f"Insufficient sunlight "
                f"({sun_pct:.1f}% < "
                f"{sunlight_threshold:.1f}%)"
            )

            if device.pending_command == "stop":

                logger.info(
                    "%s: STOP already pending -- %s",
                    tag,
                    reason,
                )

                return "none"

            if _queue_stop(
                db,
                device,
                zone,
                soil,
                reason,
            ):
                return "stop"

            return "none"

        # --------------------------------------------------------------------
        # TARGET MOISTURE REACHED
        # --------------------------------------------------------------------

        if soil >= high_threshold:

            reason = (
                f"Target moisture reached "
                f"({soil:.1f}% >= "
                f"{high_threshold:.1f}%)"
            )

            if device.pending_command == "stop":

                logger.info(
                    "%s: STOP already pending -- %s",
                    tag,
                    reason,
                )

                return "none"

            if _queue_stop(
                db,
                device,
                zone,
                soil,
                reason,
            ):
                return "stop"

            return "none"

        # --------------------------------------------------------------------
        # CONTINUE PUMP
        # --------------------------------------------------------------------

        logger.info(
            "%s: pump running -- "
            "soil=%.1f%%, "
            "sunlight=%.1f%%, "
            "rain=%.1f%%",
            tag,
            soil,
            sun_pct,
            rain_intensity,
        )

        return "none"

    # ========================================================================
    # 11. PUMP NOT RUNNING
    # ========================================================================

    # Never issue START while STOP is still pending.
    if device.pending_command == "stop":

        logger.info(
            "%s: skipped -- STOP command pending",
            tag,
        )

        return "none"

    # ========================================================================
    # 12. SOIL ALREADY SUFFICIENT
    # ========================================================================

    if soil >= low_threshold:

        logger.info(
            "%s: no watering required -- "
            "soil %.1f%% >= low threshold %.1f%%",
            tag,
            soil,
            low_threshold,
        )

        return "none"

    # ========================================================================
    # 13. RAIN BLOCKS START
    # ========================================================================

    if rain_blocked:

        logger.info(
            "%s: START blocked -- "
            "rain=%.1f%% > %.1f%%",
            tag,
            rain_intensity,
            RAIN_STOP_THRESHOLD,
        )

        return "none"

    # ========================================================================
    # 14. SUNLIGHT BLOCKS START
    # ========================================================================

    if sun_pct < sunlight_threshold:

        logger.info(
            "%s: START blocked -- "
            "sunlight %.1f%% < required %.1f%%",
            tag,
            sun_pct,
            sunlight_threshold,
        )

        return "none"

    # ========================================================================
    # 14.5 COOLDOWN & WEATHER FORECAST CHECK
    # ========================================================================

    min_gap_minutes = float(getattr(settings, "AUTO_WATER_MIN_GAP_MINUTES", 0))
    min_gap_seconds = min_gap_minutes * 60

    recent_watering = (
        db.query(models.WateringEvent)
        .filter(
            models.WateringEvent.zone_id == zone.id,
            models.WateringEvent.trigger_type == models.TriggerType.AUTO,  # <-- Only auto cycles
            models.WateringEvent.amount_liters > 0,
        )
        .order_by(models.WateringEvent.timestamp.desc())
        .first()
    ) if min_gap_seconds > 0 else None

    if recent_watering:

        since_last = (
            datetime.utcnow()
            - recent_watering.timestamp
        ).total_seconds()

        if since_last < min_gap_seconds:

            logger.info(
                "%s: START blocked -- "
                "cooldown in effect "
                "(%.0fs < %.0fs)",
                tag,
                since_last,
                min_gap_seconds,
            )

            return "none"

    # ------------------------------------------------------------------------
    # RAIN FORECAST CHECK
    # ------------------------------------------------------------------------

    try:

        forecast = get_rain_forecast(
            zone.id,
            date.today() + timedelta(days=1),
            latitude=(
                zone.farm.latitude
                if zone.farm
                else None
            ),
            longitude=(
                zone.farm.longitude
                if zone.farm
                else None
            ),
        )

        if (
            forecast.probability is not None
            and forecast.probability >= 65.0
        ):

            if soil >= (low_threshold - 10.0):

                logger.info(
                    "%s: START skipped -- "
                    "high rain forecast (%.1f%%) "
                    "and soil not critically dry (%.1f%%)",
                    tag,
                    forecast.probability,
                    soil,
                )

                return "none"

    except Exception:

        pass

    # ========================================================================
    # 15. START
    # ========================================================================

    if _queue_start(
        db,
        device,
        zone,
        soil,
        sun_pct,
    ):
        return "start"

    return "none"


# ============================================================================
# ONE AUTO-WATERING PASS
# ============================================================================

async def _run_pass() -> None:

    db = SessionLocal()

    try:

        zones = (
            db.query(models.Zone)
            .filter(
                models.Zone.auto_mode.is_(True)
            )
            .all()
        )

        if not zones:

            logger.debug(
                "Auto-watering pass: "
                "no auto-mode zones"
            )

            return

        for zone in zones:

            try:

                action = _check_zone(
                    db,
                    zone,
                )

                if action == "none":
                    continue

                device = zone.devices[0]

                # IMPORTANT:
                #
                # We ONLY queued the command.
                #
                # We do NOT modify:
                #
                #     device.pump_running
                #
                # The ESP32 must physically execute the command
                # and then report its actual relay/pump state.

                logger.info(
                    "zone '%s' (%s): "
                    "auto action=%s queued "
                    "for device=%s",
                    zone.name,
                    zone.id,
                    action,
                    device.id,
                )

            except Exception:

                logger.exception(
                    "Failed processing zone %s",
                    zone.id,
                )

    finally:

        db.close()


# ============================================================================
# CONTINUOUS BACKGROUND LOOP
# ============================================================================

async def auto_watering_loop() -> None:
    """
    Continuously evaluate all auto-mode zones.

    Every pass checks:

        - soil moisture
        - sunlight
        - rain
        - sensor health
        - sensor freshness
        - actual reported pump state
        - pending commands
    """

    logger.info(
        "Auto-watering loop started "
        "(interval=%ss)",
        settings.AUTO_WATER_CHECK_INTERVAL_SECONDS,
    )

    while True:

        try:

            await _run_pass()

        except Exception:

            logger.exception(
                "Auto-watering pass failed"
            )

        await asyncio.sleep(
            settings.AUTO_WATER_CHECK_INTERVAL_SECONDS
        )