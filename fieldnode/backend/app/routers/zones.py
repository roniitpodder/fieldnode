from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_zone_or_404, get_owned_farm_ids
from app.routers.farms import _owned_farm_or_404

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("", response_model=List[schemas.ZoneOut])
def list_zones(farm_id: Optional[str] = None, db: Session = Depends(get_db),
                user: models.User = Depends(get_current_user)):
    farm_ids = [farm_id] if farm_id else get_owned_farm_ids(db, user)
    zones = db.query(models.Zone).filter(models.Zone.farm_id.in_(farm_ids)).all()
    return zones


@router.post("", response_model=schemas.ZoneOut)
def create_zone(payload: schemas.ZoneCreate, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    _owned_farm_or_404(db, payload.farm_id, user)  # ensures the farm belongs to the user
    zone = models.Zone(**payload.model_dump())
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
    for field, value in payload.model_dump(exclude_unset=True).items():
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
