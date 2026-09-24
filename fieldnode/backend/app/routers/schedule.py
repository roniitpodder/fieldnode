from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.ownership import get_owned_zone_or_404

router = APIRouter(prefix="/api/schedules", tags=["schedule"])


class ManualScheduleCreate(BaseModel):
    zone_id: str
    next_run_at: datetime
    recurrence: str = "daily"


@router.get("", response_model=List[schemas.ScheduleOut])
def list_schedules(zone_id: str, db: Session = Depends(get_db),
                    user: models.User = Depends(get_current_user)):
    get_owned_zone_or_404(db, zone_id, user)
    return (
        db.query(models.Schedule)
        .filter(models.Schedule.zone_id == zone_id)
        .order_by(models.Schedule.next_run_at.desc())
        .all()
    )


@router.post("", response_model=schemas.ScheduleOut)
def create_manual_schedule(payload: ManualScheduleCreate, db: Session = Depends(get_db),
                            user: models.User = Depends(get_current_user)):
    zone = get_owned_zone_or_404(db, payload.zone_id, user)
    schedule = models.Schedule(
        zone_id=zone.id,
        next_run_at=payload.next_run_at,
        recurrence=payload.recurrence,
        is_auto_generated=False,
        status="pending",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.post("/{schedule_id}/cancel", response_model=schemas.ScheduleOut)
def cancel_schedule(schedule_id: str, db: Session = Depends(get_db),
                     user: models.User = Depends(get_current_user)):
    schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    get_owned_zone_or_404(db, schedule.zone_id, user)  # ownership check
    schedule.status = "cancelled"
    db.commit()
    db.refresh(schedule)
    return schedule
