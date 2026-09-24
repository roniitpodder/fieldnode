"""
The AI Advisor: wraps the trained classifier + regressor and turns their
raw output into the structured, explainable response the dashboard's
"AI-generated schedule with reasoning" feature needs (per the project report,
section 3: target moisture, required water, rain probability, one-line reason).

The ML model makes the watering decision; `rules.py` acts as a hard safety net
(e.g. it is NEVER allowed to water while the rain sensor is wet).
"""
import os
import joblib
import pandas as pd
import sklearn

from app.ml.rules import dryness_score, rain_vetoes_watering
from app.ml.train_model import FEATURES, MODEL_DIR, train

_clf = None
_reg = None
_importances = None


def _artifacts_current() -> bool:
    """True only if artifacts exist AND were built with this scikit-learn version and
    this exact feature list. Loading a pickle from another sklearn version can give
    silently wrong predictions, and old files may expect features we no longer send."""
    needed = ["should_water_clf.joblib", "liters_reg.joblib", "feature_importances.joblib",
              "model_meta.joblib"]
    if not all(os.path.exists(os.path.join(MODEL_DIR, f)) for f in needed):
        return False
    try:
        meta = joblib.load(os.path.join(MODEL_DIR, "model_meta.joblib"))
    except Exception:  # noqa: BLE001 — unreadable metadata => retrain
        return False
    return meta.get("sklearn") == sklearn.__version__ and meta.get("features") == FEATURES


def _load():
    global _clf, _reg, _importances
    if _clf is None or _reg is None:
        if not _artifacts_current():
            train()  # seconds on a laptop; keeps the API working out of the box
        _clf = joblib.load(os.path.join(MODEL_DIR, "should_water_clf.joblib"))
        _reg = joblib.load(os.path.join(MODEL_DIR, "liters_reg.joblib"))
        _importances = joblib.load(os.path.join(MODEL_DIR, "feature_importances.joblib"))
    return _clf, _reg, _importances


def _build_reasoning(f: dict, should_water: bool, rain_skip: bool, raining_now: bool,
                     liters: float) -> str:
    moisture, target = f["soil_moisture"], f["moisture_min"]
    temp, rain_p = f["temperature"], f["rain_probability"]

    if raining_now:
        if rain_skip:
            return (f"It's raining right now (rain sensor is wet), so watering is on hold even "
                    f"though soil moisture is {moisture:.0f}% against a {target:.0f}% target. "
                    f"The rain will soak in; the advisor will re-check once the sensor dries.")
        return (f"It's raining right now (rain sensor is wet) and soil moisture is {moisture:.0f}% "
                f"\u2014 no watering needed.")

    if rain_skip:
        return (f"Soil moisture is {moisture:.0f}% (target {target:.0f}%), but the rain probability "
                f"is {rain_p:.0f}% and the deficit is small enough that the cycle is being skipped "
                f"to avoid overwatering ahead of rainfall.")

    if should_water:
        bits = [f"soil moisture is {moisture:.0f}%, below the {target:.0f}% threshold for this crop"]
        if temp > 30:
            bits.append(f"temperature is elevated at {temp:.1f}\u00b0C, increasing evaporation")
        if rain_p < 30:
            bits.append(f"rain probability is low ({rain_p:.0f}%), so natural rainfall won't cover the deficit")
        return f"Watering {liters:.2f}L recommended \u2014 " + "; ".join(bits) + "."

    text = (f"Soil moisture ({moisture:.0f}%) is within the healthy range for this crop, "
            f"and conditions don't indicate a deficit \u2014 no watering needed right now.")
    if rain_p >= 65:
        text += f" Rain is also likely ({rain_p:.0f}%)."
    return text


def predict(
    soil_moisture: float,
    temperature: float,
    humidity: float,
    rain_probability: float,
    hours_since_last_watering: float,
    light_level: float,
    moisture_min: float,
    moisture_max: float,
    raining_now: bool = False,
) -> dict:
    clf, reg, importances = _load()
    dryness = dryness_score(soil_moisture, moisture_min, temperature, humidity)

    row = pd.DataFrame([{
        "moisture_min": moisture_min,
        "moisture_max": moisture_max,
        "soil_moisture": soil_moisture,
        "temperature": temperature,
        "humidity": humidity,
        "dryness": dryness,
        "raining_now": int(raining_now),
        "rain_probability": rain_probability,
        "hours_since_last_watering": hours_since_last_watering,
        "light_level": light_level,
    }])[FEATURES]

    p_water = float(clf.predict_proba(row)[0][list(clf.classes_).index(1)])
    model_says_water = p_water >= 0.5

    vetoed = rain_vetoes_watering(dryness, rain_probability, raining_now)

    # rain_skip = "a cycle that WOULD have run was held back because of rain".
    # (Not simply "rain is likely" \u2014 if the soil is already fine nothing was skipped.)
    rain_skip = bool(dryness > 0 and vetoed)
    should_water = bool(model_says_water and not vetoed)

    liters = float(max(0.0, reg.predict(row)[0])) if should_water else 0.0
    confidence = p_water if should_water else 1.0 - p_water

    reasoning = _build_reasoning(
        {"soil_moisture": soil_moisture, "moisture_min": moisture_min,
         "temperature": temperature, "rain_probability": rain_probability},
        should_water, rain_skip, raining_now, liters,
    )

    return {
        "should_water": should_water,
        "predicted_liters": round(liters, 2),
        "rain_probability": rain_probability,
        "rain_skip": rain_skip,
        "raining_now": bool(raining_now),
        "confidence": round(confidence, 3),
        "reasoning": reasoning,
        "feature_importances": importances,
    }
