from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_zone_or_404, get_owned_device_or_404, get_owned_farm_ids
from app.config import settings

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _to_out(device: models.Device) -> schemas.DeviceOut:
    is_online = bool(
        device.last_seen
        and (datetime.utcnow() - device.last_seen).total_seconds() < settings.DEVICE_OFFLINE_AFTER_SECONDS
    )
    out = schemas.DeviceOut.model_validate(device)
    out.is_online = is_online
    return out


@router.get("", response_model=List[schemas.DeviceOut])
def list_devices(zone_id: Optional[str] = None, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    farm_ids = get_owned_farm_ids(db, user)
    q = (
        db.query(models.Device)
        .join(models.Zone, models.Device.zone_id == models.Zone.id)
        .filter(models.Zone.farm_id.in_(farm_ids))
    )
    if zone_id:
        q = q.filter(models.Device.zone_id == zone_id)
    return [_to_out(d) for d in q.all()]


@router.post("", response_model=schemas.DeviceOut)
def register_device(payload: schemas.DeviceCreate, db: Session = Depends(get_db),
                     user: models.User = Depends(get_current_user)):
    get_owned_zone_or_404(db, payload.zone_id, user)
    existing = db.query(models.Device).filter(models.Device.device_code == payload.device_code).first()
    if existing:
        raise HTTPException(status_code=400, detail="Device code already registered")

    device = models.Device(**payload.model_dump(),
                            pump_flow_rate_lpm=settings.PUMP_DEFAULT_FLOW_LPM)
    db.add(device)
    db.commit()
    db.refresh(device)
    return _to_out(device)


@router.get("/{device_id}", response_model=schemas.DeviceOut)
def get_device(device_id: str, db: Session = Depends(get_db),
                user: models.User = Depends(get_current_user)):
    device = get_owned_device_or_404(db, device_id, user)
    return _to_out(device)


@router.patch("/{device_id}", response_model=schemas.DeviceOut)
def update_device(device_id: str, payload: schemas.DeviceUpdate, db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    """Set pump calibration (`pump_flow_rate_lpm`) etc. With no flow sensor, every
    'liters used' number is run time x this rate, so calibrating it matters."""
    device = get_owned_device_or_404(db, device_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return _to_out(device)


@router.post("/{device_id}/rotate-key", response_model=schemas.DeviceOut)
def rotate_key(device_id: str, db: Session = Depends(get_db),
                user: models.User = Depends(get_current_user)):
    import uuid
    device = get_owned_device_or_404(db, device_id, user)
    device.api_key = str(uuid.uuid4())
    db.commit()
    db.refresh(device)
    return _to_out(device)
