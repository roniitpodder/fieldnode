from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_farm_ids

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", response_model=List[schemas.WateringEventOut])
def list_activity(zone_id: Optional[str] = None, limit: int = 100,
                   db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    farm_ids = get_owned_farm_ids(db, user)
    q = (
        db.query(models.WateringEvent)
        .join(models.Zone, models.WateringEvent.zone_id == models.Zone.id)
        .filter(models.Zone.farm_id.in_(farm_ids))
    )
    if zone_id:
        q = q.filter(models.WateringEvent.zone_id == zone_id)
    return q.order_by(models.WateringEvent.timestamp.desc()).limit(limit).all()
