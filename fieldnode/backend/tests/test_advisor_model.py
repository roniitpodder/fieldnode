"""Behaviour tests for the ML advisor (no DB / no HTTP)."""
import warnings
import numpy as np
import pytest

from app.ml import advisor_model as adv
from app.ml.generate_data import generate
from app.ml.rules import dryness_score, rain_vetoes_watering
from app.ml.train_model import FEATURES

BASE = dict(soil_moisture=50, temperature=28, humidity=55, rain_probability=10,
            hours_since_last_watering=12, light_level=500,
            moisture_min=40, moisture_max=70, raining_now=False)


def run(**kw):
    return adv.predict(**{**BASE, **kw})


def test_artifacts_match_installed_sklearn_and_features():
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any InconsistentVersionWarning fails the test
        adv._clf = adv._reg = None
        adv._load()
    assert adv._artifacts_current()


def test_moist_soil_no_rain_does_not_water():
    r = run()
    assert r["should_water"] is False and r["predicted_liters"] == 0.0 and r["rain_skip"] is False


def test_dry_hot_soil_waters_with_positive_liters():
    r = run(soil_moisture=25, temperature=36, humidity=30)
    assert r["should_water"] is True and r["predicted_liters"] > 0.5


def test_more_deficit_means_more_water():
    small = run(soil_moisture=35)["predicted_liters"]
    big = run(soil_moisture=15)["predicted_liters"]
    assert big > small > 0


def test_raining_now_holds_watering_even_when_soil_is_very_dry():
    r = run(soil_moisture=10, raining_now=True, rain_probability=20)
    assert r["should_water"] is False
    assert r["rain_skip"] is True and r["raining_now"] is True
    assert "raining right now" in r["reasoning"]


def test_raining_now_with_wet_soil_is_not_reported_as_a_skipped_cycle():
    r = run(soil_moisture=60, raining_now=True)
    assert r["should_water"] is False and r["rain_skip"] is False


def test_high_forecast_skips_a_small_deficit():
    r = run(soil_moisture=35, rain_probability=85)
    assert r["should_water"] is False and r["rain_skip"] is True


def test_high_forecast_does_not_skip_critically_dry_soil():
    r = run(soil_moisture=10, rain_probability=90)
    assert r["should_water"] is True and r["rain_skip"] is False


def test_high_forecast_with_fine_soil_is_not_a_skip():
    """Regression: used to say 'cycle skipped to avoid overwatering' when nothing was due."""
    r = run(soil_moisture=55, rain_probability=85)
    assert r["should_water"] is False and r["rain_skip"] is False


def test_hard_veto_never_waters_while_raining_over_many_random_inputs():
    rng = np.random.default_rng(7)
    for _ in range(400):
        r = run(soil_moisture=float(rng.uniform(0, 100)), temperature=float(rng.uniform(5, 48)),
                humidity=float(rng.uniform(5, 100)), rain_probability=float(rng.uniform(0, 100)),
                raining_now=True)
        assert r["should_water"] is False


def test_model_agrees_with_rules_on_unseen_data():
    df = generate(n_samples=4000, seed=999)  # different seed than training
    clf, _, _ = adv._load()
    acc = (clf.predict(df[FEATURES]) == df["should_water"]).mean()
    assert acc > 0.95, f"model/rule agreement only {acc:.3f}"


def test_rules_are_single_source_of_truth():
    assert rain_vetoes_watering(dryness=50, rain_probability=0, raining_now=True) is True
    assert rain_vetoes_watering(dryness=5, rain_probability=70, raining_now=False) is True
    assert rain_vetoes_watering(dryness=20, rain_probability=70, raining_now=False) is False
    assert dryness_score(20, 40, 25, 50) == pytest.approx(20)


@pytest.mark.parametrize("forecast", [0, 40, 64, 66, 70, 80, 90, 100])
def test_clearly_dry_soil_is_watered_at_every_forecast_level(forecast):
    """Regression: the model used to refuse to water dry soil (dryness 16, above the rain-skip
    cut-off of 12) once the forecast passed ~66%, contradicting the safety rule."""
    r = run(soil_moisture=25, temperature=31.5, humidity=48, rain_probability=forecast)
    assert r["should_water"] is True and r["rain_skip"] is False
