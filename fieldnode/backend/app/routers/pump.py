from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, get_device_from_api_key
from app.services.ownership import get_owned_zone_or_404
from app.services.ws_manager import manager


router = APIRouter(
    prefix="/api/pump",
    tags=["pump"],
)


# ============================================================
# HELPERS
# ============================================================

def estimate_liters(
    duration_seconds: int,
    flow_rate_lpm: float,
) -> float:
    """
    Estimate delivered water using the calibrated pump flow rate.
    """

    rate = (
        flow_rate_lpm
        if flow_rate_lpm and flow_rate_lpm > 0
        else settings.PUMP_DEFAULT_FLOW_LPM
    )

    return round(
        max(0, duration_seconds) / 60.0 * rate,
        2,
    )


def validate_duration(duration: int) -> int:
    """
    Validate pump duration against backend safety limits.
    """

    if (
        duration < 1
        or duration > settings.MAX_PUMP_RUN_SECONDS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"duration_seconds must be between "
                f"1 and {settings.MAX_PUMP_RUN_SECONDS}."
            ),
        )

    return duration


# ============================================================
# MANUAL CONTROL
# ============================================================

@router.post(
    "/control",
    response_model=schemas.WateringEventOut,
)
async def control_pump(
    payload: schemas.PumpControlIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Dashboard/manual pump control.

    IMPORTANT:
    This endpoint only QUEUES a command for the ESP32.

    It does NOT set device.pump_running=True because the backend
    has not yet confirmed that the physical relay actually turned on.
    """

    zone = get_owned_zone_or_404(
        db,
        payload.zone_id,
        user,
    )

    if not zone.devices:
        raise HTTPException(
            status_code=400,
            detail="Zone has no registered device",
        )

    device = zone.devices[0]

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if payload.action == "start":

        duration = (
            payload.duration_seconds
            if payload.duration_seconds is not None
            else 30
        )

        duration = validate_duration(duration)

        # Queue command for ESP32.
        device.pending_command = "start"
        device.pending_command_duration = duration

        # IMPORTANT:
        # This tells ESP32 that this is a MANUAL command.
        device.pending_command_source = "manual"

        reason = (
            "Manual start requested from dashboard."
        )

        event = models.WateringEvent(
            zone_id=zone.id,
            device_id=device.id,
            trigger_type=models.TriggerType.MANUAL,
            amount_liters=0.0,
            duration_seconds=duration,
            reason=reason,
        )

        source = "manual"

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    elif payload.action == "stop":

        # Queue stop command for ESP32.
        device.pending_command = "stop"
        device.pending_command_duration = None

        # IMPORTANT:
        # Stop command is also manual when it comes from dashboard.
        device.pending_command_source = "manual"

        reason = (
            "Manual stop requested from dashboard."
        )

        event = models.WateringEvent(
            zone_id=zone.id,
            device_id=device.id,
            trigger_type=models.TriggerType.MANUAL,
            amount_liters=0.0,
            duration_seconds=0,
            reason=reason,
        )

        source = "manual"

    # --------------------------------------------------------
    # INVALID ACTION
    # --------------------------------------------------------

    else:

        raise HTTPException(
            status_code=400,
            detail="action must be 'start' or 'stop'",
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    db.add(event)
    db.commit()
    db.refresh(event)

    # --------------------------------------------------------
    # WEBSOCKET
    # --------------------------------------------------------

    await manager.broadcast(
        device.zone_id,
        {
            "event": "pump_command_queued",
            "action": payload.action,
            "source": source,
            "mode": source,
        },
    )

    return event


# ============================================================
# ESP32 COMMAND POLLING
# ============================================================

@router.get("/command")
async def poll_command(
    db: Session = Depends(get_db),
    device: models.Device = Depends(get_device_from_api_key),
):
    """
    ESP32 polls this endpoint for a pending command.

    Returns live zone thresholds and true mode so ESP32
    firmware stays in sync.
    """

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    device.last_seen = datetime.utcnow()

    # --------------------------------------------------------
    # GET PARENT ZONE
    # --------------------------------------------------------

    zone = (
        db.query(models.Zone)
        .filter(
            models.Zone.id == device.zone_id
        )
        .first()
    )

    # --------------------------------------------------------
    # DETERMINE ACTIVE ZONE MODE
    # --------------------------------------------------------

    zone_mode = (
        "auto"
        if (zone and zone.auto_mode)
        else "manual"
    )

    # --------------------------------------------------------
    # READ PENDING COMMAND
    # --------------------------------------------------------

    command = device.pending_command
    duration = device.pending_command_duration

    source = (
        device.pending_command_source
        or zone_mode
    )

    # --------------------------------------------------------
    # CLEAR PENDING COMMAND AFTER READING
    # --------------------------------------------------------

    device.pending_command = None
    device.pending_command_duration = None
    device.pending_command_source = None

    db.commit()

    # --------------------------------------------------------
    # SMS MESSAGE
    # --------------------------------------------------------

    sms_text = None

    if command == "start":

        sms_text = (
            f"SU-Krishi: Pump START command sent "
            f"for {duration}s ({source})."
        )

    elif command == "stop":

        sms_text = (
            f"SU-Krishi: Pump STOP command sent "
            f"({source})."
        )

    # --------------------------------------------------------
    # RESPONSE TO ESP32
    # --------------------------------------------------------

    return {
        "command": command,
        "duration_seconds": duration,

        # If there is a queued command, return its source.
        # Otherwise return the actual current zone mode.
        "mode": source if command else zone_mode,

        # Live crop/zone thresholds.
        "moisture_threshold_low": (
            float(zone.moisture_threshold_low)
            if zone
            else 35.0
        ),

        "moisture_threshold_high": (
            float(zone.moisture_threshold_high)
            if zone
            else 70.0
        ),

        "sunlight_threshold": (
            float(zone.sunlight_threshold)
            if zone
            else 30.0
        ),

        "sms_alert": sms_text,
    }


# ============================================================
# ESP32 ACK
# ============================================================

@router.post("/ack")
async def ack_command(
    duration_seconds: int = 0,
    db: Session = Depends(get_db),
    device: models.Device = Depends(get_device_from_api_key),
):
    """
    ESP32 calls this after a pump cycle has finished.

    This endpoint:
    - updates device heartbeat
    - marks pump as OFF
    - calculates estimated water usage
    - completes the corresponding watering event
    """

    # --------------------------------------------------------
    # VALIDATE DURATION
    # --------------------------------------------------------

    if duration_seconds < 0:
        duration_seconds = 0

    if (
        duration_seconds >
        settings.MAX_PUMP_RUN_SECONDS
    ):
        duration_seconds = (
            settings.MAX_PUMP_RUN_SECONDS
        )

    # --------------------------------------------------------
    # HEARTBEAT / PHYSICAL STATE
    # --------------------------------------------------------

    device.last_seen = datetime.utcnow()

    # ESP32 is telling us the cycle has finished.
    device.pump_running = False

    # --------------------------------------------------------
    # WATER ESTIMATION
    # --------------------------------------------------------

    liters = estimate_liters(
        duration_seconds,
        device.pump_flow_rate_lpm,
    )

    # --------------------------------------------------------
    # FIND MOST RECENT UNFINISHED EVENT
    # --------------------------------------------------------

    pending_event = (
        db.query(models.WateringEvent)
        .filter(
            models.WateringEvent.device_id == device.id,

            # Event has not yet been completed.
            models.WateringEvent.amount_liters == 0,

            # Ignore manual STOP events.
            models.WateringEvent.duration_seconds > 0,

            # Avoid matching very old events.
            models.WateringEvent.timestamp
            >= (
                datetime.utcnow()
                - timedelta(hours=1)
            ),
        )
        .order_by(
            models.WateringEvent.timestamp.desc()
        )
        .first()
    )

    # --------------------------------------------------------
    # COMPLETE EXISTING EVENT
    # --------------------------------------------------------

    if pending_event:

        pending_event.amount_liters = liters
        pending_event.duration_seconds = (
            duration_seconds
        )

        if (
            pending_event.trigger_type
            == models.TriggerType.AUTO
        ):

            pending_event.reason = (
                f"Automatic watering completed: "
                f"pump ran {duration_seconds}s "
                f"(~{liters:.2f} L estimated)."
            )

        else:

            pending_event.reason = (
                f"Manual watering completed: "
                f"pump ran {duration_seconds}s "
                f"(~{liters:.2f} L estimated)."
            )

    # --------------------------------------------------------
    # NO MATCHING EVENT
    # --------------------------------------------------------

    else:

        # This can happen if the pump was started locally
        # or if the original event is no longer available.
        #
        # We create a fallback event so the physical
        # watering cycle is still recorded.

        db.add(
            models.WateringEvent(
                zone_id=device.zone_id,
                device_id=device.id,
                trigger_type=models.TriggerType.AUTO,
                amount_liters=liters,
                duration_seconds=duration_seconds,
                reason=(
                    f"Watering completed without a "
                    f"matching queued event: "
                    f"pump ran {duration_seconds}s "
                    f"(~{liters:.2f} L estimated)."
                ),
            )
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    db.commit()

    # --------------------------------------------------------
    # WEBSOCKET
    # --------------------------------------------------------

    await manager.broadcast(
        device.zone_id,
        {
            "event": "pump_state",
            "pump_running": False,
            "amount_liters": liters,
        },
    )

    return {
        "ok": True,
        "amount_liters": liters,
    }


# ============================================================
# WATERING EVENTS
# ============================================================

@router.get(
    "/events",
    response_model=List[schemas.WateringEventOut],
)
def list_events(
    zone_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Return recent watering events for an owned zone.
    """

    # --------------------------------------------------------
    # OWNERSHIP CHECK
    # --------------------------------------------------------

    get_owned_zone_or_404(
        db,
        zone_id,
        user,
    )

    # --------------------------------------------------------
    # LIMIT SAFETY
    # --------------------------------------------------------

    limit = max(
        1,
        min(limit, 200),
    )

    # --------------------------------------------------------
    # QUERY
    # --------------------------------------------------------

    events = (
        db.query(models.WateringEvent)
        .filter(
            models.WateringEvent.zone_id == zone_id
        )
        .order_by(
            models.WateringEvent.timestamp.desc()
        )
        .limit(limit)
        .all()
    )

    return events