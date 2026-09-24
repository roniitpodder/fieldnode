from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_zone_or_404, get_owned_farm_ids
from app.routers.farms import _owned_farm_or_404

router = APIRouter(prefix="/api/zones", tags=["zones"])


def _apply_preset_thresholds(db: Session, zone: models.Zone, crop_preset_id: Optional[str]):
    """Automatically sync zone moisture thresholds from the selected crop preset."""
    if crop_preset_id:
        preset = db.query(models.CropPreset).filter(models.CropPreset.id == crop_preset_id).first()
        if not preset:
            raise HTTPException(status_code=404, detail="Crop preset not found")
        zone.crop_preset_id = preset.id
        zone.moisture_threshold_low = preset.moisture_min
        zone.moisture_threshold_high = preset.moisture_max


@router.get("", response_model=List[schemas.ZoneOut])
def list_zones(farm_id: Optional[str] = None, db: Session = Depends(get_db),
               user: models.User = Depends(get_current_user)):
    farm_ids = [farm_id] if farm_id else get_owned_farm_ids(db, user)
    zones = db.query(models.Zone).filter(models.Zone.farm_id.in_(farm_ids)).all()
    return zones


@router.post("", response_model=schemas.ZoneOut)
def create_zone(payload: schemas.ZoneCreate, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    _owned_farm_or_404(db, payload.farm_id, user)
    
    zone_data = payload.model_dump()
    crop_preset_id = zone_data.pop("crop_preset_id", None)
    
    zone = models.Zone(**zone_data)
    if crop_preset_id:
        _apply_preset_thresholds(db, zone, crop_preset_id)
        
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@router.get("/{zone_id}", response_model=schemas.ZoneOut)
def get_zone(zone_id: str, db: Session = Depends(get_db),
             user: models.User = Depends(get_current_user)):
    return get_owned_zone_or_404(db, zone_id, user)


@router.patch("/{zone_id}", response_model=schemas.ZoneOut)
def update_zone(zone_id: str, payload: schemas.ZoneUpdate, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    zone = get_owned_zone_or_404(db, zone_id, user)
    update_data = payload.model_dump(exclude_unset=True)

    # If crop preset is updated, automatically pull and apply its min/max thresholds
    if "crop_preset_id" in update_data:
        crop_preset_id = update_data.pop("crop_preset_id")
        _apply_preset_thresholds(db, zone, crop_preset_id)

    for field, value in update_data.items():
        setattr(zone, field, value)

    db.commit()
    db.refresh(zone)
    return zone


@router.delete("/{zone_id}")
def delete_zone(zone_id: str, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    zone = get_owned_zone_or_404(db, zone_id, user)
    db.delete(zone)
    db.commit()
    return {"ok": True}