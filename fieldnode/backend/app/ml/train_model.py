"""
Trains two models used by the AI advisor:
  1. RandomForestClassifier -> should_water (bool)
  2. RandomForestRegressor  -> liters_needed (float)

Both share the same feature set. Artifacts are saved with joblib so
app/ml/advisor_model.py can load them at API startup without retraining.
A small metadata file records the scikit-learn version and feature list the
artifacts were built with, so the advisor can detect stale/incompatible files
and retrain automatically instead of loading a mismatched pickle.

Run: python -m app.ml.train_model
"""
import os
import joblib
import sklearn
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, mean_absolute_error

from app.ml.generate_data import generate

# "dryness" is the engineered score from rules.py (soil deficit + heat - humid air). Giving it
# to the model directly lets the forests learn the sharp rain-skip cut-off exactly; without it
# they blurred it and refused to water clearly-dry soil when a high rain forecast was present.
FEATURES = [
    "moisture_min", "moisture_max", "soil_moisture", "temperature",
    "humidity", "dryness", "raining_now", "rain_probability",
    "hours_since_last_watering", "light_level",
]

MODEL_DIR = os.path.join(os.path.dirname(__file__), "artifacts")


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    df = generate(n_samples=15000)

    X = df[FEATURES]
    y_clf = df["should_water"]
    y_reg = df["liters_needed"]

    X_train, X_test, yclf_train, yclf_test, yreg_train, yreg_test = train_test_split(
        X, y_clf, y_reg, test_size=0.2, random_state=42
    )

    clf = RandomForestClassifier(n_estimators=150, max_depth=10, random_state=42)
    clf.fit(X_train, yclf_train)
    clf_acc = accuracy_score(yclf_test, clf.predict(X_test))

    reg = RandomForestRegressor(n_estimators=150, max_depth=10, random_state=42)
    reg.fit(X_train, yreg_train)
    reg_mae = mean_absolute_error(yreg_test, reg.predict(X_test))

    joblib.dump(clf, os.path.join(MODEL_DIR, "should_water_clf.joblib"))
    joblib.dump(reg, os.path.join(MODEL_DIR, "liters_reg.joblib"))
    joblib.dump(FEATURES, os.path.join(MODEL_DIR, "features.joblib"))
    joblib.dump({"sklearn": sklearn.__version__, "features": FEATURES},
                os.path.join(MODEL_DIR, "model_meta.joblib"))

    # feature importances, used later to build human-readable reasoning text
    importances = dict(zip(FEATURES, clf.feature_importances_.round(4).tolist()))
    joblib.dump(importances, os.path.join(MODEL_DIR, "feature_importances.joblib"))

    print(f"should_water classifier accuracy: {clf_acc:.3f}")
    print(f"liters_needed regressor MAE: {reg_mae:.3f} L")
    print(f"Feature importances: {importances}")
    print(f"Artifacts saved to {MODEL_DIR} (scikit-learn {sklearn.__version__})")
    return {"accuracy": clf_acc, "mae": reg_mae}


if __name__ == "__main__":
    train()
