"""
Automatic soil-moisture-based watering.

Behavior:
    AUTO MODE ON
        ↓
    Soil moisture < zone threshold
        ↓
    Start pump
        ↓
    Keep pump running while moisture < threshold
        ↓
    Soil moisture >= threshold
        ↓
    Stop pump

Rain and sunlight are used as START conditions only.

Once automatic watering has started, the pump continues until
the soil moisture reaches the configured threshold.

There is NO cooldown between auto watering restarts because the
pump may need to continue after an ESP32 safety timeout.
"""

import asyncio
import logging
from datetime import datetime

from app import models
from app.config import settings
from app.database import SessionLocal
from app.services.rain_sensor import get_rain_sensor_status
from app.services.sunlight import sunlight_percent
from app.services.ws_manager import manager


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


def _latest_reading(db, device):
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


def _queue_start(db, device, zone, soil, sun_pct):
    """
    Queue a pump START.

    We use the ESP32 maximum allowed duration as a safety window.
    The backend will keep checking moisture and re-issue START
    if the ESP32 reaches its safety timeout before the soil
    reaches the threshold.
    """

    device.pending_command = "start"

    # Use the configured auto duration if available.
    # The ESP32 will enforce its own maximum safety limit.
    duration = getattr(
        settings,
        "AUTO_WATER_RUN_SECONDS",
        300,
    )

    if duration <= 0:
        duration = 300

    if duration > 300:
        duration = 300

    device.pending_command_duration = duration

    db.commit()

    logger.info(
        "zone '%s' (%s): QUEUED START -- soil %.1f%% < threshold %.1f%%, "
        "sunlight %.1f%%, duration %ss",
        zone.name,
        zone.id,
        soil,
        zone.moisture_threshold_low,
        sun_pct,
        duration,
    )

    return True


def _queue_stop(db, device, zone, soil):
    """
    Queue STOP when the soil has reached the target threshold.
    """

    device.pending_command = "stop"

    # Stop command does not need a duration.
    device.pending_command_duration = 0

    db.commit()

    logger.info(
        "zone '%s' (%s): QUEUED STOP -- soil %.1f%% >= threshold %.1f%%",
        zone.name,
        zone.id,
        soil,
        zone.moisture_threshold_low,
    )

    return True


def _check_zone(db, zone) -> str:
    """
    Returns:
        "start" -> start command queued
        "stop"  -> stop command queued
        "none"  -> nothing changed
    """

    tag = f"zone '{zone.name}' ({zone.id})"

    # ------------------------------------------------------------
    # 1. AUTO MODE
    # ------------------------------------------------------------

    if not zone.auto_mode:
        logger.info(
            "%s: skipped -- auto_mode is off",
            tag,
        )
        return "none"

    # ------------------------------------------------------------
    # 2. DEVICE
    # ------------------------------------------------------------

    if not zone.devices:
        logger.info(
            "%s: skipped -- no registered device",
            tag,
        )
        return "none"

    device = zone.devices[0]

    # ------------------------------------------------------------
    # 3. LATEST SENSOR READING
    # ------------------------------------------------------------

    latest = _latest_reading(db, device)

    if latest is None:
        logger.info(
            "%s: skipped -- no sensor readings",
            tag,
        )
        return "none"

    if latest.soil_moisture is None:
        logger.info(
            "%s: skipped -- soil_moisture is null",
            tag,
        )
        return "none"

    if latest.sensor_fault:
        logger.info(
            "%s: skipped -- sensor_fault=true",
            tag,
        )
        return "none"

    # ------------------------------------------------------------
    # 4. SENSOR FRESHNESS
    # ------------------------------------------------------------

    age = (
        datetime.utcnow() - latest.timestamp
    ).total_seconds()

    if age > settings.READING_STALE_SECONDS:
        logger.info(
            "%s: skipped -- reading %.0fs old > %ss",
            tag,
            age,
            settings.READING_STALE_SECONDS,
        )
        return "none"

    soil = float(latest.soil_moisture)

    threshold = float(
        zone.moisture_threshold_low
    )

    # ------------------------------------------------------------
    # 5. PUMP ALREADY RUNNING
    # ------------------------------------------------------------

    if device.pump_running:

        # If target moisture reached:
        # STOP the pump.
        if soil >= threshold:

            if device.pending_command:
                logger.info(
                    "%s: pump running but command already queued (%s)",
                    tag,
                    device.pending_command,
                )
                return "none"

            return (
                "stop"
                if _queue_stop(
                    db,
                    device,
                    zone,
                    soil,
                )
                else "none"
            )

        # Soil is still below threshold.
        # DO NOT stop/restart.
        logger.info(
            "%s: pump already running -- soil %.1f%% < threshold %.1f%%, continuing",
            tag,
            soil,
            threshold,
        )

        return "none"

    # ------------------------------------------------------------
    # 6. PUMP NOT RUNNING
    # ------------------------------------------------------------

    # If soil has already reached the threshold,
    # nothing needs to happen.
    if soil >= threshold:
        logger.info(
            "%s: soil %.1f%% >= threshold %.1f%% -- watering not needed",
            tag,
            soil,
            threshold,
        )
        return "none"

    # ------------------------------------------------------------
    # 7. COMMAND ALREADY QUEUED
    # ------------------------------------------------------------

    if device.pending_command:

        logger.info(
            "%s: skipped -- command already queued (%s)",
            tag,
            device.pending_command,
        )

        return "none"

    # ------------------------------------------------------------
    # 8. RAIN CHECK
    #
    # Rain is a START blocker.
    # Once the pump is already running, rain does not stop it.
    # ------------------------------------------------------------

    rain_status = get_rain_sensor_status(
        db,
        device,
    )

    if rain_status["raining_now"]:

        logger.info(
            "%s: skipped -- rain detected (%s)",
            tag,
            rain_status["status"],
        )

        return "none"

    # ------------------------------------------------------------
    # 9. SUNLIGHT CHECK
    #
    # Only used before starting a new watering cycle.
    # ------------------------------------------------------------

    sun_pct = sunlight_percent(
        latest.light_level
    )

    if sun_pct is None:
        logger.info(
            "%s: skipped -- sunlight unavailable",
            tag,
        )
        return "none"

    if sun_pct < settings.AUTO_WATER_MIN_SUNLIGHT_PCT:

        logger.info(
            "%s: skipped -- sunlight %.1f%% < required %.1f%%",
            tag,
            sun_pct,
            settings.AUTO_WATER_MIN_SUNLIGHT_PCT,
        )

        return "none"

    # ------------------------------------------------------------
    # 10. START PUMP
    # ------------------------------------------------------------

    if _queue_start(
        db,
        device,
        zone,
        soil,
        sun_pct,
    ):
        return "start"

    return "none"


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

            logger.info(
                "Pass complete: no auto-mode zones"
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

                # ------------------------------------------------
                # Broadcast START
                # ------------------------------------------------

                if action == "start":

                    await manager.broadcast(
                        zone.id,
                        {
                            "event":
                                "pump_command_queued",

                            "action":
                                "start",

                            "source":
                                "auto",
                        },
                    )

                    await manager.broadcast(
                        zone.id,
                        {
                            "event":
                                "pump_state",

                            "pump_running":
                                True,

                            "device_id":
                                device.id,
                        },
                    )

                # ------------------------------------------------
                # Broadcast STOP
                # ------------------------------------------------

                elif action == "stop":

                    await manager.broadcast(
                        zone.id,
                        {
                            "event":
                                "pump_command_queued",

                            "action":
                                "stop",

                            "source":
                                "auto",
                        },
                    )

                    await manager.broadcast(
                        zone.id,
                        {
                            "event":
                                "pump_state",

                            "pump_running":
                                False,

                            "device_id":
                                device.id,
                        },
                    )

            except Exception:

                logger.exception(
                    "Failed processing zone %s",
                    zone.id,
                )

    finally:

        db.close()


async def auto_watering_loop() -> None:
    """
    Runs continuously in the background.

    The loop checks the latest soil moisture periodically.

    If moisture is below threshold:
        START / CONTINUE watering.

    If moisture reaches threshold:
        STOP watering.
    """

    logger.info(
        "Auto-watering loop started "
        "(interval=%ss, sunlight minimum=%.1f%%)",
        settings.AUTO_WATER_CHECK_INTERVAL_SECONDS,
        settings.AUTO_WATER_MIN_SUNLIGHT_PCT,
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