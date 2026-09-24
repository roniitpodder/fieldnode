"""
Wipes the FAKE demo history that seed.py creates, so the dashboard only shows
what your real ESP32 sends. Keeps: the user, farm, zones, crop presets, and
the FN-001 device (with its API key).

Run from the backend folder, with the backend STOPPED:

    Windows:      venv\\Scripts\\python reset_demo_data.py
    Mac / Linux:  ./venv/bin/python reset_demo_data.py
"""
from app.database import SessionLocal
from app import models

db = SessionLocal()

removed = {}
for name, model in [
    ("sensor readings", models.SensorReading),
    ("watering events", models.WateringEvent),
    ("schedules", models.Schedule),
    ("notifications", models.Notification),
    ("chat messages", models.ChatMessage),
]:
    removed[name] = db.query(model).delete()

for d in db.query(models.Device).all():
    d.pump_running = False
    d.pending_command = None
    d.pending_command_duration = None
    d.last_seen = None          # shows "offline" until your ESP32 really reports in

db.commit()

print("Removed:", ", ".join(f"{n}: {c}" for n, c in removed.items()))
print()
print("Your device(s) - paste the api_key into the firmware (DEVICE_API_KEY):")
for d in db.query(models.Device).all():
    print(f"  {d.device_code}   api_key = {d.api_key}")
db.close()
