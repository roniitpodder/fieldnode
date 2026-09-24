from app.database import SessionLocal
from app import models

db = SessionLocal()
for model in (models.SensorReading, models.WateringEvent, models.Schedule,
              models.Notification, models.ChatMessage):
    db.query(model).delete()
for d in db.query(models.Device).all():
    d.pump_running = False
    d.pending_command = None
    d.pending_command_duration = None
    d.last_seen = None
db.commit()
print("Demo data cleared.")
for d in db.query(models.Device).all():
    print(d.device_code, "api_key =", d.api_key)
db.close()
