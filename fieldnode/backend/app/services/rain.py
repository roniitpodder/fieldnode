import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional

import requests

from app.config import settings

logger = logging.getLogger("fieldnode.rain")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 4


def _mock_probability(zone_id: str, for_date: date) -> float:
    """Deterministic per (zone, date) fallback so the UI never breaks if the
    weather API is unreachable (rate limit, no internet, bad coords, etc.)."""
    seed = f"{zone_id}-{for_date.isoformat()}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    value = int(digest[:4], 16) % 10000 / 100.0
    return round(value, 1)


def _fetch_real_forecast(latitude: float, longitude: float, for_date: date) -> Optional[float]:
    """
    Calls Open-Meteo (https://open-meteo.com) — free, no API key required.
    Returns the max hourly precipitation_probability for `for_date`, or None
    on any failure (network, bad response, no data for that date) so the
    caller can fall back to the mock provider instead of erroring out.
    """
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "precipitation_probability",
                "forecast_days": 3,
                "timezone": "auto",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        times = data["hourly"]["time"]
        probs = data["hourly"]["precipitation_probability"]

        target = for_date.isoformat()
        day_probs = [p for t, p in zip(times, probs) if t.startswith(target)]
        if not day_probs:
            return None
        return round(max(day_probs), 1)
    except Exception as exc:  # noqa: BLE001 — any failure here just means "use the mock"
        logger.warning("Open-Meteo forecast fetch failed, falling back to mock: %s", exc)
        return None


class RainForecast(NamedTuple):
    probability: Optional[float]   # 0-100, or None if no trustworthy forecast exists
    source: str                    # "open-meteo" | "mock" | "unavailable"


def get_rain_forecast(zone_id: str, for_date: date = None,
                      latitude: Optional[float] = None,
                      longitude: Optional[float] = None) -> RainForecast:
    """
    Forecast probability (0-100) plus WHERE it came from, so callers (advisor,
    dashboard) can be honest when the number is simulated.

    Real forecast (Open-Meteo, no API key) is used when farm coordinates exist and
    the API is reachable. Otherwise, if USE_MOCK_RAIN_FALLBACK is on (default, keeps
    demos alive) a deterministic FAKE value is returned with source="mock"; if it is
    off, probability is None and source="unavailable".

    This is only the *forecast*. Whether it is raining right now comes from the
    physical rain sensor (see services/rain_sensor.py).
    """
    for_date = for_date or date.today()

    if latitude is not None and longitude is not None:
        real = _fetch_real_forecast(latitude, longitude, for_date)
        if real is not None:
            return RainForecast(real, "open-meteo")

    if settings.USE_MOCK_RAIN_FALLBACK:
        return RainForecast(_mock_probability(zone_id, for_date), "mock")
    return RainForecast(None, "unavailable")


def get_rain_probability(zone_id: str, for_date: date = None,
                          latitude: Optional[float] = None,
                          longitude: Optional[float] = None) -> float:
    """Backwards-compatible wrapper: plain 0-100 float (0.0 if unavailable)."""
    prob = get_rain_forecast(zone_id, for_date, latitude, longitude).probability
    return prob if prob is not None else 0.0
