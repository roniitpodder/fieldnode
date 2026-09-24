from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models


def get_owned_zone_or_404(db: Session, zone_id: str, user: models.User) -> models.Zone:
    zone = (
        db.query(models.Zone)
        .join(models.Farm, models.Zone.farm_id == models.Farm.id)
        .filter(models.Zone.id == zone_id, models.Farm.owner_id == user.id)
        .first()
    )
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


def get_owned_device_or_404(db: Session, device_id: str, user: models.User) -> models.Device:
    device = (
        db.query(models.Device)
        .join(models.Zone, models.Device.zone_id == models.Zone.id)
        .join(models.Farm, models.Zone.farm_id == models.Farm.id)
        .filter(models.Device.id == device_id, models.Farm.owner_id == user.id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def get_owned_farm_ids(db: Session, user: models.User):
    return [f.id for f in db.query(models.Farm.id).filter(models.Farm.owner_id == user.id).all()]
