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

router = APIRouter(prefix="/api/pump", tags=["pump"])


def estimate_liters(
    duration_seconds: int,
    flow_rate_lpm: float,
) -> float:
    """Estimate delivered water using calibrated pump flow rate."""

    rate = (
        flow_rate_lpm
        if flow_rate_lpm and flow_rate_lpm > 0
        else settings.PUMP_DEFAULT_FLOW_LPM
    )

    return round(
        max(0, duration_seconds) / 60.0 * rate,
        2,
    )


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

        device.pending_command = "start"
        device.pending_command_duration = duration

        reason = "Manual start requested from dashboard."

        event = models.WateringEvent(
            zone_id=zone.id,
            device_id=device.id,
            trigger_type=models.TriggerType.MANUAL,
            amount_liters=0.0,
            duration_seconds=duration,
            reason=reason,
        )

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    elif payload.action == "stop":

        device.pending_command = "stop"
        device.pending_command_duration = None

        reason = "Manual stop requested from dashboard."

        event = models.WateringEvent(
            zone_id=zone.id,
            device_id=device.id,
            trigger_type=models.TriggerType.MANUAL,
            amount_liters=0.0,
            duration_seconds=0,
            reason=reason,
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="action must be 'start' or 'stop'",
        )

    db.add(event)
    db.commit()
    db.refresh(event)

    await manager.broadcast(
        device.zone_id,
        {
            "event": "pump_command_queued",
            "action": payload.action,
            "source": "manual",
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
    ESP32 polls this endpoint.

    The backend puts either:
        start
        stop
        null

    into pending_command.
    """

    command = device.pending_command
    duration = device.pending_command_duration

    if command:

        if command == "start":
            device.pump_running = True

        elif command == "stop":
            device.pump_running = False

        # Clear command AFTER reading it.
        device.pending_command = None
        device.pending_command_duration = None

        db.commit()

        await manager.broadcast(
            device.zone_id,
            {
                "event": "pump_state",
                "pump_running": device.pump_running,
            },
        )

    return {
        "command": command,
        "duration_seconds": duration,
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
    ESP32 calls this after the pump cycle has finished.

    We determine whether the most recent unfinished event was
    MANUAL or AUTO, then complete that event instead of
    accidentally converting an AUTO cycle into a MANUAL cycle.
    """

    device.pump_running = False

    liters = estimate_liters(
        duration_seconds,
        device.pump_flow_rate_lpm,
    )

    # --------------------------------------------------------
    # Find the most recent unfinished watering event.
    # --------------------------------------------------------

    pending_event = (
        db.query(models.WateringEvent)
        .filter(
            models.WateringEvent.device_id == device.id,
            models.WateringEvent.amount_liters == 0,
            models.WateringEvent.duration_seconds > 0,
            models.WateringEvent.timestamp
            >= datetime.utcnow() - timedelta(hours=1),
        )
        .order_by(
            models.WateringEvent.timestamp.desc()
        )
        .first()
    )

    if pending_event:

        pending_event.amount_liters = liters
        pending_event.duration_seconds = duration_seconds

        if pending_event.trigger_type == models.TriggerType.AUTO:

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

    else:

        # If there is no pending event, treat it as AUTO.
        db.add(
            models.WateringEvent(
                zone_id=device.zone_id,
                device_id=device.id,
                trigger_type=models.TriggerType.AUTO,
                amount_liters=liters,
                duration_seconds=duration_seconds,
                reason=(
                    f"Automatic watering completed: "
                    f"pump ran {duration_seconds}s "
                    f"(~{liters:.2f} L estimated)."
                ),
            )
        )

    db.commit()

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

    get_owned_zone_or_404(
        db,
        zone_id,
        user,
    )

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