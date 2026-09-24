"""
Generates a synthetic-but-physically-sensible dataset to train the irrigation
advisor model. There's no real historical dataset yet (this is a new device),
so we encode domain rules (soil physics + crop thresholds, see rules.py) as the
data-generating process. Once real sensor/watering history accumulates in the DB,
`train_model.py` can be pointed at that instead.

NOTE: because labels come from hand-written rules, the trained model is
effectively a smooth approximation of those rules. That's fine as a starting
point, but it can't "discover" anything the rules don't already encode until it
is retrained on real data.
"""
import numpy as np
import pandas as pd

from app.ml.rules import dryness_score, rain_vetoes_watering

CROPS = {
    "wheat":   {"min": 30, "max": 60},
    "tomato":  {"min": 40, "max": 70},
    "rice":    {"min": 60, "max": 90},
    "cotton":  {"min": 25, "max": 55},
    "maize":   {"min": 35, "max": 65},
    "default": {"min": 35, "max": 70},
}


def generate(n_samples: int = 12000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)  # local RNG => reproducible regardless of call order
    rows = []
    crop_names = list(CROPS.keys())

    for _ in range(n_samples):
        crop = rng.choice(crop_names)
        thresh = CROPS[crop]
        moisture_min, moisture_max = thresh["min"], thresh["max"]

        soil_moisture = float(np.clip(rng.normal((moisture_min + moisture_max) / 2, 20), 0, 100))
        temperature = float(np.clip(rng.normal(28, 6), 5, 48))
        humidity = float(np.clip(rng.normal(55, 20), 5, 100))
        rain_probability = float(np.clip(rng.beta(1.5, 4) * 100, 0, 100))
        hours_since_last_watering = float(np.clip(rng.exponential(10), 0, 96))
        light_level = float(np.clip(rng.normal(500, 250), 0, 1200))

        # Rain sensor: it's more likely to be raining right now when the forecast is
        # high, and if it IS raining the forecast for the day is necessarily high too.
        raining_now = bool(rng.random() < 0.03 + 0.5 * (rain_probability / 100) ** 2)
        if raining_now:
            rain_probability = float(max(rain_probability, rng.uniform(60, 100)))

        # --- domain rules: should we water? ---
        dryness = dryness_score(soil_moisture, moisture_min, temperature, humidity)
        wants_water = dryness > 0
        rain_skip = wants_water and rain_vetoes_watering(dryness, rain_probability, raining_now)
        should_water = wants_water and not rain_skip

        # --- liters needed: bigger deficit + hotter + drier air -> more water ---
        base_liters = max(0.0, dryness) * 0.12
        base_liters += max(0.0, temperature - 25) * 0.03
        base_liters += max(0.0, 50 - humidity) * 0.01
        liters = float(np.clip(base_liters + rng.normal(0, 0.15), 0, 8))
        if not should_water:
            liters = 0.0

        rows.append({
            "crop": crop,
            "moisture_min": moisture_min,
            "moisture_max": moisture_max,
            "soil_moisture": round(soil_moisture, 2),
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "dryness": round(dryness, 3),
            "raining_now": int(raining_now),
            "rain_probability": round(rain_probability, 2),
            "hours_since_last_watering": round(hours_since_last_watering, 2),
            "light_level": round(light_level, 2),
            "should_water": int(should_water),
            "rain_skip": int(rain_skip),
            "liters_needed": round(liters, 3),
        })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate()
    df.to_csv("app/ml/synthetic_training_data.csv", index=False)
    print(f"Wrote {len(df)} rows to app/ml/synthetic_training_data.csv")
