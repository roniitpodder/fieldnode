from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/crops", tags=["crops"])


class CropPresetIn(BaseModel):
    crop_name: str
    moisture_min: float
    moisture_max: float
    notes: Optional[str] = None


@router.get("", response_model=List[schemas.CropPresetOut])
def list_crops(db: Session = Depends(get_db)):
    return db.query(models.CropPreset).all()


@router.post("", response_model=schemas.CropPresetOut)
def create_crop(payload: CropPresetIn, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    existing = db.query(models.CropPreset).filter(models.CropPreset.crop_name == payload.crop_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Crop preset already exists")
    crop = models.CropPreset(**payload.model_dump())
    db.add(crop)
    db.commit()
    db.refresh(crop)
    return crop
