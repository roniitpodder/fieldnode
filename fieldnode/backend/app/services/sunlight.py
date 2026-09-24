from typing import Optional


def sunlight_percent(light_level: Optional[float]) -> Optional[float]:
    if light_level is None:
        return None

    return round(
        max(0.0, min(100.0, light_level)),
        1
    )