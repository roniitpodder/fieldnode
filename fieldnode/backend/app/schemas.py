from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, ConfigDict, Field, computed_field

from app.services.sunlight import sunlight_percent


# ---------- Auth / User ----------
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    language: str = "en"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: EmailStr
    language: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Crop preset ----------
class CropPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    crop_name: str
    moisture_min: float
    moisture_max: float
    notes: Optional[str] = None


# ---------- Farm ----------
class FarmCreate(BaseModel):
    name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class FarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_at: datetime
    zone_count: int = 0


# ---------- Zone ----------
class ZoneCreate(BaseModel):
    farm_id: str
    name: str
    soil_type: str = "loam"
    crop_preset_id: Optional[str] = None
    moisture_threshold_low: float = 35.0
    moisture_threshold_high: float = 70.0
    auto_mode: bool = True


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    crop_preset_id: Optional[str] = None
    moisture_threshold_low: Optional[float] = None
    moisture_threshold_high: Optional[float] = None
    auto_mode: Optional[bool] = None


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    farm_id: str
    name: str
    soil_type: str
    crop_preset_id: Optional[str] = None
    moisture_threshold_low: float
    moisture_threshold_high: float
    auto_mode: bool


# ---------- Device ----------
class DeviceCreate(BaseModel):
    zone_id: str
    device_code: str
    firmware_version: str = "1.0.0"
    gsm_fallback_ready: bool = True


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    zone_id: str
    device_code: str
    firmware_version: str
    gsm_fallback_ready: bool
    battery_health: float
    last_seen: Optional[datetime] = None
    is_online: bool = False
    pump_running: bool = False
    pump_flow_rate_lpm: float = 1.2
    api_key: Optional[str] = None


class DeviceUpdate(BaseModel):
    firmware_version: Optional[str] = None
    gsm_fallback_ready: Optional[bool] = None
    # Calibrated pump output in liters/minute (used to estimate water delivered)
    pump_flow_rate_lpm: Optional[float] = Field(default=None, gt=0, le=60)


# ---------- Sensor readings (device ingest) ----------
class SensorReadingIn(BaseModel):
    soil_moisture: Optional[float] = Field(default=None, ge=0, le=100)
    temperature: Optional[float] = None
    humidity: Optional[float] = Field(default=None, ge=0, le=100)
    light_level: Optional[float] = Field(default=None, ge=0)
    rain_detected: Optional[bool] = None            # rain sensor plate wet?
    rain_intensity: Optional[float] = Field(default=None, ge=0, le=100)  # optional 0-100 %
    sensor_fault: bool = False


class SensorReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    device_id: str
    timestamp: datetime
    soil_moisture: Optional[float] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    light_level: Optional[float] = None
    rain_detected: Optional[bool] = None
    rain_intensity: Optional[float] = None
    sensor_fault: bool

    @computed_field  # sunlight % derived from the LDR (light_level)
    @property
    def sunlight_pct(self) -> Optional[float]:
        return sunlight_percent(self.light_level)


# ---------- Watering / pump ----------
class PumpControlIn(BaseModel):
    zone_id: str
    action: str  # "start" | "stop"
    duration_seconds: Optional[int] = 30


class WateringEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    zone_id: str
    device_id: Optional[str] = None
    timestamp: datetime
    trigger_type: str
    amount_liters: float
    duration_seconds: int
    reason: Optional[str] = None


# ---------- Schedule / AI advisor ----------
class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    zone_id: str
    next_run_at: datetime
    recurrence: str
    is_auto_generated: bool
    rain_skip: bool
    rain_probability: Optional[float] = None
    reasoning: Optional[str] = None
    predicted_liters: Optional[float] = None
    status: str


class AIAdvisorRequest(BaseModel):
    zone_id: str
    rain_probability_override: Optional[float] = None  # 0-100, optional manual override


class AIAdvisorResponse(BaseModel):
    zone_id: str
    should_water: bool
    recommended_time: datetime
    predicted_liters: float
    recommended_duration_seconds: Optional[int] = None   # pump run time to deliver predicted_liters
    rain_probability: float
    rain_skip: bool
    raining_now: bool = False                 # from the physical rain sensor
    rain_forecast_source: str = "unknown"     # "open-meteo" | "mock" | "override"
    confidence: float
    reasoning: str
    warnings: List[str] = []                  # stale/missing inputs, suspect sensors, etc.


# ---------- Dashboard / overview ----------
class LiveFieldStatus(BaseModel):
    zone_id: str
    zone_name: str
    soil_moisture: Optional[float] = None
    soil_moisture_delta_vs_yesterday: Optional[float] = None
    temperature: Optional[float] = None
    feels_like: Optional[float] = None
    sunlight_pct: Optional[float] = None      # from the LDR
    water_used_today_liters: float = 0.0
    water_used_delta_vs_fixed_schedule_pct: Optional[float] = None
    field_health_score: int = 100
    sensors_reporting_pct: float = 100.0
    device_online: bool = False
    rain_protection_on: bool = True
    rain_probability: Optional[float] = None
    raining_now: bool = False                 # live, from the rain sensor
    rain_sensor_status: str = "no_data"       # dry | wet | suspect_stuck | offline | no_data
    rain_forecast_source: Optional[str] = None
    next_cycle_skipped: bool = False
    pump_running: bool = False
    auto_mode: bool = True


class AnalyticsPoint(BaseModel):
    timestamp: datetime
    soil_moisture: Optional[float] = None
    temperature: Optional[float] = None
    water_used_liters: Optional[float] = None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    message: str
    read: bool
    created_at: datetime


# ---------- AI chat assistant ----------
class ChatRequest(BaseModel):
    zone_id: str
    message: str = Field(min_length=1, max_length=1000)


class SuggestedAction(BaseModel):
    """A one-tap action the UI can offer. The chatbot NEVER runs it itself: the UI shows a
    confirm button that calls POST /api/pump/control with these values."""
    type: str                       # currently only "start_pump"
    zone_id: str
    duration_seconds: int
    liters: float
    label: str


class ChatResponse(BaseModel):
    reply: str
    suggested_action: Optional[SuggestedAction] = None
    degraded: bool = False          # True = the LLM was unreachable; reply is the rule-based advisor's
    model: Optional[str] = None     # which LLM model answered (None when degraded)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    created_at: datetime
