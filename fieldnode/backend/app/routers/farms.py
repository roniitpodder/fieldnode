from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/farms", tags=["farms"])


def _owned_farm_or_404(db: Session, farm_id: str, user: models.User) -> models.Farm:
    farm = db.query(models.Farm).filter(
        models.Farm.id == farm_id, models.Farm.owner_id == user.id
    ).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    return farm


@router.get("", response_model=List[schemas.FarmOut])
def list_farms(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    farms = db.query(models.Farm).filter(models.Farm.owner_id == user.id).all()
    out = []
    for f in farms:
        item = schemas.FarmOut.model_validate(f)
        item.zone_count = len(f.zones)
        out.append(item)
    return out


@router.post("", response_model=schemas.FarmOut)
def create_farm(payload: schemas.FarmCreate, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    farm = models.Farm(name=payload.name, owner_id=user.id,
                       latitude=payload.latitude, longitude=payload.longitude)
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return schemas.FarmOut.model_validate(farm)


@router.get("/{farm_id}", response_model=schemas.FarmOut)
def get_farm(farm_id: str, db: Session = Depends(get_db),
             user: models.User = Depends(get_current_user)):
    farm = _owned_farm_or_404(db, farm_id, user)
    item = schemas.FarmOut.model_validate(farm)
    item.zone_count = len(farm.zones)
    return item


@router.patch("/{farm_id}", response_model=schemas.FarmOut)
def update_farm(farm_id: str, payload: schemas.FarmCreate, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    farm = _owned_farm_or_404(db, farm_id, user)
    farm.name = payload.name
    if payload.latitude is not None:
        farm.latitude = payload.latitude
    if payload.longitude is not None:
        farm.longitude = payload.longitude
    db.commit()
    db.refresh(farm)
    item = schemas.FarmOut.model_validate(farm)
    item.zone_count = len(farm.zones)
    return item


@router.delete("/{farm_id}")
def delete_farm(farm_id: str, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    farm = _owned_farm_or_404(db, farm_id, user)
    db.delete(farm)
    db.commit()
    return {"ok": True}
