"""End-to-end API tests for the changes: rain sensor in, tank/flow sensors out."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app import models


@pytest.fixture(scope="module")
def ctx():
    with TestClient(app) as c:
        tok = c.post("/api/auth/register", json={"name": "T", "email": "t@x.io", "password": "pw12345"}).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        farm = c.post("/api/farms", json={"name": "F"}, headers=H).json()
        zone = c.post("/api/zones", json={"farm_id": farm["id"], "name": "Z",
                                          "moisture_threshold_low": 40, "moisture_threshold_high": 70},
                      headers=H).json()
        dev = c.post("/api/devices", json={"zone_id": zone["id"], "device_code": "FN-T"}, headers=H).json()
        yield c, H, zone["id"], dev["id"], {"X-Device-Key": dev["api_key"]}


def ingest(c, D, **kw):
    body = {"soil_moisture": 30, "temperature": 30, "humidity": 50, "light_level": 500,
            "rain_detected": False, **kw}
    r = c.post("/api/ingest/reading", json=body, headers=D)
    assert r.status_code == 200, r.text
    return r.json()


def test_ingest_stores_rain_fields_and_has_no_tank_level(ctx):
    c, H, zid, did, D = ctx
    out = ingest(c, D, rain_detected=True, rain_intensity=55)
    assert out["rain_detected"] is True and out["rain_intensity"] == 55
    assert "tank_level" not in out


def test_old_firmware_still_sending_tank_level_does_not_break_ingest(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, tank_level=80)  # extra field is ignored, not a 422


def test_rain_intensity_out_of_range_rejected(ctx):
    c, H, zid, did, D = ctx
    r = c.post("/api/ingest/reading", json={"soil_moisture": 30, "rain_intensity": 250}, headers=D)
    assert r.status_code == 422


def test_dry_soil_and_dry_plate_advises_watering_with_duration(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, soil_moisture=25, rain_detected=False)
    r = c.post("/api/ai/advise", json={"zone_id": zid, "rain_probability_override": 5}, headers=H).json()
    assert r["should_water"] is True and r["raining_now"] is False
    assert r["recommended_duration_seconds"] and r["recommended_duration_seconds"] > 0
    # duration must convert back to ~the predicted liters at the pump rate (1.2 L/min default)
    assert abs(r["recommended_duration_seconds"] / 60 * 1.2 - r["predicted_liters"]) < 0.05


def test_wet_plate_holds_watering_and_shows_in_overview(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, soil_moisture=25, rain_detected=True, rain_intensity=60)
    r = c.post("/api/ai/advise", json={"zone_id": zid, "rain_probability_override": 5}, headers=H).json()
    assert r["raining_now"] is True and r["should_water"] is False and r["rain_skip"] is True
    ov = c.get(f"/api/overview/zones/{zid}", headers=H).json()
    assert ov["raining_now"] is True and ov["rain_protection_on"] is True
    assert ov["rain_sensor_status"] == "wet" and ov["next_cycle_skipped"] is True


def test_rain_notification_fires_only_on_dry_to_wet_transition(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, rain_detected=False)
    before = len([n for n in c.get("/api/notifications", headers=H).json() if n["type"] == "rain_skip"])
    ingest(c, D, rain_detected=True)
    ingest(c, D, rain_detected=True)
    ingest(c, D, rain_detected=True)
    after = len([n for n in c.get("/api/notifications", headers=H).json() if n["type"] == "rain_skip"])
    assert after == before + 1


def test_stuck_wet_sensor_is_ignored_and_flagged(ctx):
    c, H, _, _, _ = ctx
    # isolated zone+device, so no earlier "dry" readings from other tests interfere
    farm = c.post("/api/farms", json={"name": "Stuck"}, headers=H).json()
    zid = c.post("/api/zones", json={"farm_id": farm["id"], "name": "S", "moisture_threshold_low": 40,
                                     "moisture_threshold_high": 70}, headers=H).json()["id"]
    dev = c.post("/api/devices", json={"zone_id": zid, "device_code": "FN-STUCK"}, headers=H).json()
    D = {"X-Device-Key": dev["api_key"]}
    db = SessionLocal()
    now = datetime.utcnow()
    for h in range(14, 0, -1):  # 14h of continuous "wet"
        db.add(models.SensorReading(device_id=dev["id"], timestamp=now - timedelta(hours=h),
                                    soil_moisture=25, temperature=30, humidity=50,
                                    rain_detected=True))
    db.commit(); db.close()
    ingest(c, D, soil_moisture=25, rain_detected=True)
    r = c.post("/api/ai/advise", json={"zone_id": zid, "rain_probability_override": 5}, headers=H).json()
    assert r["raining_now"] is False and r["should_water"] is True
    assert any("corroded" in w for w in r["warnings"])
    assert c.get(f"/api/overview/zones/{zid}", headers=H).json()["rain_sensor_status"] == "suspect_stuck"


def test_a_recent_dry_reading_means_it_is_real_rain_not_stuck(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, rain_detected=False)
    ingest(c, D, rain_detected=True)
    assert c.get(f"/api/overview/zones/{zid}", headers=H).json()["rain_sensor_status"] == "wet"


def test_missing_inputs_are_reported_not_hidden(ctx):
    c, H, zid, did, D = ctx
    c.post("/api/ingest/reading", json={"soil_moisture": 25, "rain_detected": False}, headers=D)
    r = c.post("/api/ai/advise", json={"zone_id": zid, "rain_probability_override": 5}, headers=H).json()
    joined = " ".join(r["warnings"])
    assert "Temperature not reported" in joined and "Humidity not reported" in joined


def test_no_soil_data_means_no_blind_watering(ctx):
    c, H, *_ = ctx
    farm = c.post("/api/farms", json={"name": "F2"}, headers=H).json()
    z2 = c.post("/api/zones", json={"farm_id": farm["id"], "name": "Empty"}, headers=H).json()
    r = c.post("/api/ai/advise", json={"zone_id": z2["id"], "rain_probability_override": 5}, headers=H).json()
    assert r["should_water"] is False and r["confidence"] == 0.0 and "No soil moisture data." in r["warnings"]


def test_forecast_source_is_exposed_and_mock_is_flagged(ctx):
    c, H, zid, did, D = ctx
    ingest(c, D, soil_moisture=25)
    r = c.post("/api/ai/advise", json={"zone_id": zid}, headers=H).json()  # farm has no coordinates
    assert r["rain_forecast_source"] == "mock"
    assert any("SIMULATED" in w for w in r["warnings"])


def test_manual_pump_start_then_ack_estimates_liters_without_duplicate_event(ctx):
    c, H, zid, did, D = ctx
    r = c.post("/api/pump/control", json={"zone_id": zid, "action": "start", "duration_seconds": 60}, headers=H)
    assert r.status_code == 200
    assert c.get("/api/pump/command", headers=D).json() == {"command": "start", "duration_seconds": 60}
    n_before = len(c.get("/api/pump/events", params={"zone_id": zid}, headers=H).json())
    ack = c.post("/api/pump/ack", params={"duration_seconds": 60}, headers=D).json()
    assert ack["amount_liters"] == pytest.approx(1.2)          # 60 s x 1.2 L/min
    events = c.get("/api/pump/events", params={"zone_id": zid}, headers=H).json()
    assert len(events) == n_before                             # completed the manual row, no duplicate
    assert events[0]["trigger_type"] == "manual" and events[0]["amount_liters"] == pytest.approx(1.2)


def test_pump_calibration_changes_estimate(ctx):
    c, H, zid, did, D = ctx
    r = c.patch(f"/api/devices/{did}", json={"pump_flow_rate_lpm": 2.0}, headers=H)
    assert r.status_code == 200 and r.json()["pump_flow_rate_lpm"] == 2.0
    ack = c.post("/api/pump/ack", params={"duration_seconds": 30}, headers=D).json()
    assert ack["amount_liters"] == pytest.approx(1.0)          # 30 s x 2 L/min
    assert c.patch(f"/api/devices/{did}", json={"pump_flow_rate_lpm": 0}, headers=H).status_code == 422


def test_pump_run_length_is_capped_replacing_tank_dry_run_block(ctx):
    c, H, zid, did, D = ctx
    r = c.post("/api/pump/control", json={"zone_id": zid, "action": "start", "duration_seconds": 5000}, headers=H)
    assert r.status_code == 400
