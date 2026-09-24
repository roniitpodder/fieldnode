"""Converts the LDR reading into a sunlight percentage (0-100).

The firmware sends `light_level` on a 0..1200 scale (raw LDR 0..4095 scaled down),
where higher = brighter. 1200 therefore means "as bright as the LDR can read".

If full sunlight only reaches, say, 80% on your dashboard, lower LDR_FULL_SCALE
(e.g. to 960) so that full sun shows as 100%.
"""
from typing import Optional

LDR_FULL_SCALE = 1200.0


def sunlight_percent(light_level: Optional[float]) -> Optional[float]:
    if light_level is None:
        return None
    pct = light_level / LDR_FULL_SCALE * 100.0
    return round(max(0.0, min(100.0, pct)), 1)
