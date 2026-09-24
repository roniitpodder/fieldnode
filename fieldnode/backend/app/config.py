import os
from pathlib import Path

# Load .env automatically. (uvicorn does NOT do this unless you pass --env-file, so
# without this SECRET_KEY / GROQ_API_KEY in .env would be silently ignored.)
# Looks in the project root first (works no matter which folder you launch from), then the
# current folder. Real environment variables always win over .env values.
def _load_env(project_root: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv missing: plain environment variables only
        return
    load_dotenv(project_root / ".env")
    load_dotenv(Path.cwd() / ".env")


_load_env(Path(__file__).resolve().parent.parent)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    return default if val is None else val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    APP_NAME: str = "FieldNode API"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./fieldnode.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    DEVICE_OFFLINE_AFTER_SECONDS: int = 120  # if no ping in this window -> offline
    CORS_ORIGINS: list = ["*"]  # tighten in production to your frontend domain

    # --- Pump / water accounting (no flow sensor: liters are ESTIMATED) ---
    # liters = run_time_seconds / 60 * pump_flow_rate_lpm. Calibrate per pump:
    # run it for 60 s into a measuring jug and PATCH /api/devices/{id} with the result.
    PUMP_DEFAULT_FLOW_LPM: float = float(os.getenv("PUMP_DEFAULT_FLOW_LPM", "1.2"))
    # Software cap on one pump run. Replaces the old tank-level "dry-run" block,
    # which needed a water-level sensor we don't have.
    MAX_PUMP_RUN_SECONDS: int = int(os.getenv("MAX_PUMP_RUN_SECONDS", "300"))

    # --- Rain sensor (a water-detection plate used as a rain sensor) ---
    # A "wet" reading only counts as "raining now" if it is this fresh.
    RAIN_SENSOR_FRESH_SECONDS: int = int(os.getenv("RAIN_SENSOR_FRESH_SECONDS", "900"))
    # Wet continuously for this long => plate is probably stuck/corroded/shorted.
    # The rain veto is lifted (and a warning raised) so it can't block watering forever.
    RAIN_SENSOR_STUCK_HOURS: float = float(os.getenv("RAIN_SENSOR_STUCK_HOURS", "12"))

    # --- AI advisor ---
    # Readings older than this make the advisor attach a "stale data" warning.
    READING_STALE_SECONDS: int = int(os.getenv("READING_STALE_SECONDS", "1800"))
    # If the forecast API is unreachable / farm has no coordinates, fall back to a
    # deterministic FAKE probability (keeps demos alive). Set to false in real use
    # so the advisor never decides on made-up rain numbers.
    USE_MOCK_RAIN_FALLBACK: bool = _env_bool("USE_MOCK_RAIN_FALLBACK", True)

    # --- Fully automatic watering (backend-driven, soil moisture + sunlight) ---
    # Master switch for the background loop; each zone's own "Auto mode" toggle
    # (auto_mode) still gates whether that specific zone is acted on.
    AUTO_WATER_ENABLED: bool = _env_bool("AUTO_WATER_ENABLED", True)
    # How often the backend re-checks every auto_mode zone.
    AUTO_WATER_CHECK_INTERVAL_SECONDS: int = int(os.getenv("AUTO_WATER_CHECK_INTERVAL_SECONDS", "60"))
    # Minimum sunlight % (from the LDR, same 0-100 scale as the dashboard's
    # "Sunlight Percentage" card) required before auto-watering fires. This is
    # what "sunlight is still sufficient" means in practice, and is what stops
    # it from watering in the dark / at night. Raise or lower to taste.
    AUTO_WATER_MIN_SUNLIGHT_PCT: float = float(os.getenv("AUTO_WATER_MIN_SUNLIGHT_PCT", "15"))
    # How long each automatic pump run lasts.
    AUTO_WATER_RUN_SECONDS: int = int(os.getenv("AUTO_WATER_RUN_SECONDS", "30"))
    # Cooldown: minimum minutes between one auto watering finishing and the
    # next one being allowed to start for the same zone.
    AUTO_WATER_MIN_GAP_MINUTES: float = float(os.getenv("AUTO_WATER_MIN_GAP_MINUTES", "60"))

    # --- AI chat assistant (Groq, OpenAI-compatible API) ---
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    # Tried once if the main model is rate-limited / overloaded (limits are per model).
    GROQ_FALLBACK_MODEL: str = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    CHAT_MAX_REPLY_TOKENS: int = int(os.getenv("CHAT_MAX_REPLY_TOKENS", "700"))
    CHAT_HISTORY_MESSAGES: int = int(os.getenv("CHAT_HISTORY_MESSAGES", "10"))
    CHAT_RATE_LIMIT_PER_MIN: int = int(os.getenv("CHAT_RATE_LIMIT_PER_MIN", "15"))


settings = Settings()