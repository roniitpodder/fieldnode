from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.services import chat as chat_service
from app.services.ownership import get_owned_zone_or_404

router = APIRouter(prefix="/api/ai/chat", tags=["ai-chat"])


@router.post("", response_model=schemas.ChatResponse)
def send_message(payload: schemas.ChatRequest, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    """Ask the AI assistant a question about a zone (watering, crops, plant problems...).
    The answer is grounded in the zone's live sensor data. If `suggested_action` is set,
    show a confirm button that calls POST /api/pump/control with those values."""
    zone = get_owned_zone_or_404(db, payload.zone_id, user)
    return chat_service.chat(db, user, zone, payload.message)


@router.get("/history", response_model=List[schemas.ChatMessageOut])
def history(zone_id: str, limit: int = 50, db: Session = Depends(get_db),
            user: models.User = Depends(get_current_user)):
    get_owned_zone_or_404(db, zone_id, user)
    rows = (db.query(models.ChatMessage)
            .filter(models.ChatMessage.user_id == user.id, models.ChatMessage.zone_id == zone_id)
            .order_by(models.ChatMessage.created_at.desc())
            .limit(max(1, min(limit, 200))).all())
    return list(reversed(rows))  # oldest -> newest, ready to render


@router.delete("/history")
def clear_history(zone_id: str, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    get_owned_zone_or_404(db, zone_id, user)
    db.query(models.ChatMessage).filter(
        models.ChatMessage.user_id == user.id, models.ChatMessage.zone_id == zone_id
    ).delete()
    db.commit()
    return {"ok": True}
