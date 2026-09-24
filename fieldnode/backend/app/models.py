import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Enum, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_id():
    return str(uuid.uuid4())


class TriggerType(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    SKIPPED = "skipped"


class NotificationType(str, enum.Enum):
    PUMP_FAULT = "pump_fault"
    SENSOR_FAULT = "sensor_fault"
    RAIN_SKIP = "rain_skip"
    SCHEDULE = "schedule"
    INFO = "info"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    language = Column(String, default="en")  # for multilingual UI preference
    created_at = Column(DateTime, default=datetime.utcnow)

    farms = relationship("Farm", back_populates="owner", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")


class Farm(Base):
    __tablename__ = "farms"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="farms")
    zones = relationship("Zone", back_populates="farm", cascade="all, delete-orphan")


class CropPreset(Base):
    __tablename__ = "crop_presets"
    id = Column(String, primary_key=True, default=gen_id)
    crop_name = Column(String, nullable=False, unique=True)
    moisture_min = Column(Float, nullable=False)  # %
    moisture_max = Column(Float, nullable=False)  # %
    notes = Column(String, nullable=True)

    zones = relationship("Zone", back_populates="crop_preset")


class Zone(Base):
    __tablename__ = "zones"
    id = Column(String, primary_key=True, default=gen_id)
    farm_id = Column(String, ForeignKey("farms.id"), nullable=False)
    name = Column(String, nullable=False)  # e.g. "North greenhouse"
    soil_type = Column(String, default="loam")
    crop_preset_id = Column(String, ForeignKey("crop_presets.id"), nullable=True)
    moisture_threshold_low = Column(Float, default=35.0)   # below -> water
    moisture_threshold_high = Column(Float, default=70.0)  # above -> stop
    auto_mode = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    farm = relationship("Farm", back_populates="zones")
    crop_preset = relationship("CropPreset", back_populates="zones")
    devices = relationship("Device", back_populates="zone", cascade="all, delete-orphan")
    watering_events = relationship("WateringEvent", back_populates="zone", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="zone", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="zone", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"
    id = Column(String, primary_key=True, default=gen_id)
    zone_id = Column(String, ForeignKey("zones.id"), nullable=False)
    device_code = Column(String, unique=True, nullable=False)  # e.g. FN-001
    firmware_version = Column(String, default="1.0.0")
    gsm_fallback_ready = Column(Boolean, default=True)
    battery_health = Column(Float, default=100.0)  # %
    last_seen = Column(DateTime, nullable=True)
    api_key = Column(String, default=gen_id, unique=True)  # device auth token for ingest endpoints
    pump_running = Column(Boolean, default=False)
    pending_command = Column(String, nullable=True)  # "start" | "stop" | None — polled by ESP32
    pending_command_duration = Column(Integer, nullable=True)
    # No flow sensor: liters are estimated as run_time * this rate. Calibrate per pump.
    pump_flow_rate_lpm = Column(Float, default=1.2)  # liters per minute
    created_at = Column(DateTime, default=datetime.utcnow)

    zone = relationship("Zone", back_populates="devices")
    readings = relationship("SensorReading", back_populates="device", cascade="all, delete-orphan")
    watering_events = relationship("WateringEvent", back_populates="device")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    id = Column(String, primary_key=True, default=gen_id)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    soil_moisture = Column(Float, nullable=True)   # %
    temperature = Column(Float, nullable=True)     # C
    humidity = Column(Float, nullable=True)        # %
    light_level = Column(Float, nullable=True)     # lux / raw LDR
    # Rain sensor (water-detection plate). The firmware resolves the module's
    # polarity/threshold and reports a clean boolean; intensity is optional.
    rain_detected = Column(Boolean, nullable=True)   # True = plate is wet
    rain_intensity = Column(Float, nullable=True)    # 0-100 % wetness, optional
    sensor_fault = Column(Boolean, default=False)

    device = relationship("Device", back_populates="readings")


class WateringEvent(Base):
    __tablename__ = "watering_events"
    id = Column(String, primary_key=True, default=gen_id)
    zone_id = Column(String, ForeignKey("zones.id"), nullable=False)
    device_id = Column(String, ForeignKey("devices.id"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    trigger_type = Column(Enum(TriggerType), default=TriggerType.AUTO)
    amount_liters = Column(Float, default=0.0)  # estimated: run time x pump flow rate
    duration_seconds = Column(Integer, default=0)
    reason = Column(String, nullable=True)  # human-readable reason / AI reasoning

    zone = relationship("Zone", back_populates="watering_events")
    device = relationship("Device", back_populates="watering_events")


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(String, primary_key=True, default=gen_id)
    zone_id = Column(String, ForeignKey("zones.id"), nullable=False)
    next_run_at = Column(DateTime, nullable=False)
    recurrence = Column(String, default="daily")  # daily / custom cron-ish text
    is_auto_generated = Column(Boolean, default=False)  # True if AI advisor generated it
    rain_skip = Column(Boolean, default=False)
    rain_probability = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    predicted_liters = Column(Float, nullable=True)
    status = Column(String, default="pending")  # pending / completed / skipped / cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    zone = relationship("Zone", back_populates="schedules")


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    type = Column(Enum(NotificationType), default=NotificationType.INFO)
    message = Column(String, nullable=False)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notifications")


class ChatMessage(Base):
    """One turn of the AI-assistant conversation. Only the visible text of the user's
    question and the assistant's reply is stored (not the field snapshot, which is
    rebuilt fresh for every message so answers never rely on stale sensor data)."""
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    zone_id = Column(String, ForeignKey("zones.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="chat_messages")
    zone = relationship("Zone", back_populates="chat_messages")
