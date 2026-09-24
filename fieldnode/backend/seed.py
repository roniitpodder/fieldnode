"""
Seeds demo data matching the reference frontend screenshot: user Arjun Mehta,
"Mehta Farm" with a "North greenhouse" zone, Field Node FN-001, plus a day of
plausible sensor readings and a few watering events so the dashboard has
something to show immediately after boot.

Run: ./venv/bin/python seed.py
"""
import random
from datetime import datetime, timedelta

from app.database import Base, engine, SessionLocal
from app import models
from app.security import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

CROPS = [
    ("wheat", 30, 60, "Moderate water needs; sensitive to waterlogging."),
    ("tomato", 40, 70, "Consistent moisture improves fruit quality."),
    ("rice", 60, 90, "High water requirement; tolerates standing water."),
    ("cotton", 25, 55, "Drought-tolerant once established."),
    ("maize", 35, 65, "Peak water need during tasseling."),
]

if not db.query(models.CropPreset).first():
    for name, lo, hi, notes in CROPS:
        db.add(models.CropPreset(crop_name=name, moisture_min=lo, moisture_max=hi, notes=notes))
    db.commit()

user = db.query(models.User).filter(models.User.email == "arjun@mehtafarm.example").first()
if not user:
    user = models.User(
        name="Arjun Mehta",
        email="arjun@mehtafarm.example",
        hashed_password=hash_password("password123"),
        language="en",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

farm = db.query(models.Farm).filter(models.Farm.owner_id == user.id).first()
if not farm:
    farm = models.Farm(name="Mehta Farm", owner_id=user.id)
    db.add(farm)
    db.commit()
    db.refresh(farm)

tomato = db.query(models.CropPreset).filter(models.CropPreset.crop_name == "tomato").first()

zone_names = ["North greenhouse", "East field", "South orchard"]
zones = []
for name in zone_names:
    zone = db.query(models.Zone).filter(models.Zone.farm_id == farm.id, models.Zone.name == name).first()
    if not zone:
        zone = models.Zone(
            farm_id=farm.id, name=name, soil_type="loam",
            crop_preset_id=tomato.id if tomato else None,
            moisture_threshold_low=40.0, moisture_threshold_high=70.0, auto_mode=True,
        )
        db.add(zone)
        db.commit()
        db.refresh(zone)
    zones.append(zone)

main_zone = zones[0]
device = db.query(models.Device).filter(models.Device.device_code == "FN-001").first()
if not device:
    device = models.Device(
        zone_id=main_zone.id, device_code="FN-001", firmware_version="1.0.0",
        gsm_fallback_ready=True, battery_health=98.0, last_seen=datetime.utcnow(),
        pump_flow_rate_lpm=1.2,  # calibrate for your pump (see README)
    )
    db.add(device)
    db.commit()
    db.refresh(device)

# --- synthetic sensor history for the last 48h, every 30 minutes ---
# Rain plate: dry except one wet spell ~29-31h ago (matches the "skipped: rain" event below).
if db.query(models.SensorReading).filter(models.SensorReading.device_id == device.id).count() < 10:
    now = datetime.utcnow()
    for i in range(96, 0, -1):
        ts = now - timedelta(minutes=30 * i)
        hours_ago = i * 0.5
        wet = 29 <= hours_ago <= 31
        db.add(models.SensorReading(
            device_id=device.id,
            timestamp=ts,
            soil_moisture=round(60 + random.uniform(-10, 10), 1),
            temperature=round(27 + random.uniform(-4, 4), 1),
            humidity=round(55 + random.uniform(-15, 15), 1),
            light_level=round(max(0, 600 + random.uniform(-300, 300)), 1),
            rain_detected=wet,
            rain_intensity=round(random.uniform(30, 70), 1) if wet else 0.0,
            sensor_fault=False,
        ))
    db.commit()

    # a few watering events over the last 2 days. No flow sensor, so liters are
    # ESTIMATED from pump run time x pump flow rate, exactly like /api/pump/ack does.
    for i in [40, 20, 5]:
        run_s = random.choice([45, 60, 75, 90])
        db.add(models.WateringEvent(
            zone_id=main_zone.id, device_id=device.id,
            timestamp=now - timedelta(hours=i),
            trigger_type=models.TriggerType.AUTO,
            amount_liters=round(run_s / 60 * device.pump_flow_rate_lpm, 2),
            duration_seconds=run_s,
            reason="Soil moisture dropped below threshold.",
        ))
    db.add(models.WateringEvent(
        zone_id=main_zone.id, device_id=device.id,
        timestamp=now - timedelta(hours=30),
        trigger_type=models.TriggerType.SKIPPED,
        amount_liters=0.0, duration_seconds=0,
        reason="Skipped: rain sensor wet (rain detected).",
    ))
    db.commit()

print("Seed complete.")
print(f"  User:   {user.email} / password123")
print(f"  Farm:   {farm.name} ({farm.id})")
print(f"  Zone:   {main_zone.name} ({main_zone.id})")
print(f"  Device: {device.device_code} ({device.id})  api_key={device.api_key}")
db.close()
