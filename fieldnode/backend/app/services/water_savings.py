from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app import models

# A "fixed schedule" baseline for comparison: how much a naive timer-based
# system would use per day, absent smart/rain-aware decisions.
FIXED_SCHEDULE_DAILY_LITERS_BASELINE = 6.0


def water_used_today(db: Session, zone_id: str) -> float:
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        db.query(models.WateringEvent)
        .filter(models.WateringEvent.zone_id == zone_id, models.WateringEvent.timestamp >= start_of_day)
        .all()
    )
    return round(sum(e.amount_liters for e in total), 2)


def savings_vs_fixed_schedule_pct(actual_liters_today: float,
                                   baseline: float = FIXED_SCHEDULE_DAILY_LITERS_BASELINE) -> float:
    if baseline <= 0:
        return 0.0
    pct = ((actual_liters_today - baseline) / baseline) * 100
    return round(pct, 1)  # negative => using less than the fixed-schedule baseline
