from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services.advisor import run_advisor
from app.services.ownership import get_owned_zone_or_404

router = APIRouter(prefix="/api/ai", tags=["ai-advisor"])


@router.post("/advise", response_model=schemas.AIAdvisorResponse)
def advise(payload: schemas.AIAdvisorRequest, db: Session = Depends(get_db),
           user: models.User = Depends(get_current_user)):
    zone = get_owned_zone_or_404(db, payload.zone_id, user)
    r = run_advisor(db, zone, payload.rain_probability_override)
    return schemas.AIAdvisorResponse(
        zone_id=zone.id,
        should_water=r["should_water"],
        recommended_time=r["recommended_time"],
        predicted_liters=r["predicted_liters"],
        recommended_duration_seconds=r["recommended_duration_seconds"],
        rain_probability=r["rain_probability"],
        rain_skip=r["rain_skip"],
        raining_now=r["raining_now"],
        rain_forecast_source=r["rain_forecast_source"],
        confidence=r["confidence"],
        reasoning=r["reasoning"],
        warnings=r["warnings"],
    )


@router.post("/generate-schedule", response_model=schemas.ScheduleOut)
def generate_schedule(payload: schemas.AIAdvisorRequest, db: Session = Depends(get_db),
                       user: models.User = Depends(get_current_user)):
    """Runs the advisor and persists the result as a Schedule row, which is
    what the 'Plan watering' button on the dashboard and the Schedule page read from."""
    zone = get_owned_zone_or_404(db, payload.zone_id, user)
    r = run_advisor(db, zone, payload.rain_probability_override)

    schedule = models.Schedule(
        zone_id=zone.id,
        next_run_at=r["recommended_time"],
        recurrence="ai-generated",
        is_auto_generated=True,
        rain_skip=r["rain_skip"],
        rain_probability=r["rain_probability"],
        reasoning=r["reasoning"],
        predicted_liters=r["predicted_liters"],
        status="skipped" if r["rain_skip"] else "pending",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule
