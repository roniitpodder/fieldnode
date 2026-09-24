"""
Single source of truth for the irrigation decision rules.

Used by BOTH the synthetic-data generator (to label training rows) and the live
predictor (as a hard safety net around the model). Previously these were two
slightly different formulas, so the model and the override could disagree.
"""

RAIN_FORECAST_SKIP_PCT = 65.0   # forecast at/above this can veto a cycle...
RAIN_SKIP_MAX_DRYNESS = 12.0    # ...unless the soil is this much drier than target


def dryness_score(soil_moisture: float, moisture_min: float,
                  temperature: float, humidity: float) -> float:
    """>0 means the soil needs water. Deficit vs crop target, plus heat, minus humid air."""
    deficit = moisture_min - soil_moisture
    heat_pressure = max(0.0, (temperature - 30) * 0.6)
    return deficit + heat_pressure - (humidity - 50) * 0.05


def rain_vetoes_watering(dryness: float, rain_probability: float, raining_now: bool) -> bool:
    """
    Rain-aware protection.
      * Raining right now (physical rain sensor is wet) -> ALWAYS hold watering.
      * High forecast -> hold watering, unless the soil is critically dry.
    """
    if raining_now:
        return True
    return rain_probability >= RAIN_FORECAST_SKIP_PCT and dryness < RAIN_SKIP_MAX_DRYNESS
