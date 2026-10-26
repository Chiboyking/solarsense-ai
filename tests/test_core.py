"""
tests/test_core.py
====================
Automated tests for SolarSense AI's core logic. Runs fully offline (no
network calls) — model training uses whatever is already in model/ (the
synthetic fallback is deterministic via SEED, so this is reproducible even
without NASA POWER access).

Run:
    pytest tests/ -v
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import train_model as tm
import app as A


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def loaded_models():
    A.ensure_models()
    yield


@pytest.fixture
def client():
    A.app.config["TESTING"] = True
    A._RATE_LIMIT_LOG.clear()
    with A.app.test_client() as c:
        yield c


def make_synthetic_hourly(n=48, base_ghi=500):
    import math
    now = time.time()
    times = [int(now - 3600) + i * 3600 for i in range(n)]
    return {
        "time": times,
        "shortwave_radiation": [max(0, base_ghi * math.sin(math.pi * (i % 24) / 24)) for i in range(n)],
        "shortwave_radiation_clear_sky": [max(0, (base_ghi + 100) * math.sin(math.pi * (i % 24) / 24)) for i in range(n)],
        "temperature_2m": [28 + 4 * math.sin(i / 5) for i in range(n)],
        "wind_speed_10m": [3.0] * n,
        "cloud_cover": [30] * n,
    }


# ---------------------------------------------------------------------------
# Nigeria states / zone routing
# ---------------------------------------------------------------------------
def test_nigeria_states_count():
    # 36 states + FCT
    assert len(tm.NIGERIA_STATES) == 37


def test_nigeria_states_unique_names():
    names = [s[0] for s in tm.NIGERIA_STATES]
    assert len(names) == len(set(names))


def test_nearest_state_matches_lagos():
    state, capital, zone = tm.nearest_state(6.6018, 3.3515)
    assert state == "Lagos"
    assert capital == "Ikeja"
    assert zone == "coastal_delta"


def test_nearest_state_matches_kano():
    state, capital, zone = tm.nearest_state(12.0, 8.5)
    assert state == "Kano"
    assert zone == "sudan_savanna"


def test_nearest_state_handles_out_of_bounds_coords():
    # Coordinates nowhere near Nigeria should still resolve to *some* state
    # (nearest by distance), not crash.
    state, capital, zone = tm.nearest_state(0.0, 0.0)
    assert state in [s[0] for s in tm.NIGERIA_STATES]


def test_every_state_zone_is_a_known_zone():
    for state, capital, lat, lon, zone in tm.NIGERIA_STATES:
        assert zone in tm.NIGERIA_ZONES, f"{state} references unknown zone '{zone}'"


# ---------------------------------------------------------------------------
# Feature engineering + prediction
# ---------------------------------------------------------------------------
def test_build_features_returns_expected_keys():
    hourly = make_synthetic_hourly()
    feat = A.build_features(hourly, 12, 6.6018)
    assert feat is not None
    for key in tm.FEATURES:
        assert key in feat


def test_build_features_handles_bad_index_gracefully():
    hourly = make_synthetic_hourly(n=5)
    feat = A.build_features(hourly, 999, 6.6018)  # out of range
    assert feat is None


def test_predict_at_returns_sane_bounds():
    hourly = make_synthetic_hourly()
    feat = A.build_features(hourly, 12, 6.6018)
    feat["_hour"] = feat["_dt"].hour
    pred = A.predict_at("coastal_delta", feat, capacity_kw=5.0)
    assert pred["lower"] <= pred["power"] <= pred["upper"]
    assert pred["power"] >= 0
    assert pred["upper"] <= 5.0 * 1.05 + 1e-6


def test_predict_at_scales_with_capacity():
    hourly = make_synthetic_hourly()
    feat = A.build_features(hourly, 12, 6.6018)
    feat["_hour"] = feat["_dt"].hour
    small = A.predict_at("coastal_delta", feat, capacity_kw=2.0)
    large = A.predict_at("coastal_delta", feat, capacity_kw=10.0)
    # a bigger system shouldn't predict less power for the same conditions
    assert large["power"] >= small["power"]


# ---------------------------------------------------------------------------
# Battery optimization
# ---------------------------------------------------------------------------
def test_optimize_battery_soc_stays_in_bounds():
    series = [{"power": 2.0 if 8 <= h <= 17 else 0.0, "_hour": h} for h in range(24)]
    schedule = A.optimize_battery(
        series, capacity_kwh=10.0, soc_pct=50.0, efficiency=0.9, load_kw=1.0,
    )
    assert len(schedule) == 24
    for entry in schedule:
        assert 0 <= entry["soc_pct"] <= 100.01


def test_optimize_battery_charges_from_surplus_solar():
    # Midday surplus (generation > load) should raise the state of charge.
    series = [{"power": 3.0, "_hour": 12}, {"power": 3.0, "_hour": 13}]
    schedule = A.optimize_battery(series, capacity_kwh=10.0, soc_pct=20.0, efficiency=0.9, load_kw=1.0)
    assert schedule[-1]["soc_pct"] > 20.0


def test_optimize_battery_discharges_to_cover_deficit():
    # No generation, load present -> either discharges (soc drops) or pulls
    # from the grid once the battery is empty.
    series = [{"power": 0.0, "_hour": 20}, {"power": 0.0, "_hour": 21}]
    schedule = A.optimize_battery(series, capacity_kwh=10.0, soc_pct=50.0, efficiency=0.9, load_kw=1.0)
    assert schedule[-1]["soc_pct"] < 50.0 or schedule[-1]["grid_import_kw"] > 0



# ---------------------------------------------------------------------------
# Nowcast
# ---------------------------------------------------------------------------
def test_compute_nowcast_detects_clouds_approaching():
    features = [
        {"_raw_cloud_pct": 10}, {"_raw_cloud_pct": 20}, {"_raw_cloud_pct": 50},
    ]
    result = A.compute_nowcast(features)
    assert result is not None
    assert result["level"] == "warning"


def test_compute_nowcast_stable_returns_none():
    features = [{"_raw_cloud_pct": 30}, {"_raw_cloud_pct": 32}, {"_raw_cloud_pct": 31}]
    assert A.compute_nowcast(features) is None


def test_compute_nowcast_needs_at_least_three_points():
    assert A.compute_nowcast([{"_raw_cloud_pct": 10}]) is None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_rate_limiter_blocks_after_threshold(client):
    for _ in range(5):
        client.post("/contribute", json={"lat": 6.6, "lon": 3.35, "actual_power": 1.0})
    # exceed the 10/min bucket quickly with more calls
    last_status = None
    for _ in range(10):
        resp = client.post("/contribute", json={"lat": 6.6, "lon": 3.35, "actual_power": 1.0})
        last_status = resp.status_code
    assert last_status == 429


# ---------------------------------------------------------------------------
# Contribute endpoint validation
# ---------------------------------------------------------------------------
def test_contribute_rejects_out_of_range_power(client):
    resp = client.post("/contribute", json={"lat": 6.6, "lon": 3.35, "actual_power": 9999})
    assert resp.status_code == 400


def test_contribute_rejects_missing_fields(client):
    resp = client.post("/contribute", json={"lat": 6.6})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Endpoints that don't need the network
# ---------------------------------------------------------------------------
def test_states_endpoint_returns_37(client):
    resp = client.get("/states")
    assert resp.status_code == 200
    assert len(resp.get_json()) == 37


def test_healthz_endpoint(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert len(body["models_loaded"]) == 6


def test_model_info_endpoint_has_skill_scores(client):
    resp = client.get("/model_info")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "coastal_delta" in body
    assert "test_mae_kw" in body["coastal_delta"]


def test_inverter_sim_requires_api_key(client):
    resp = client.get("/inverter_sim?forecast_power=2.0")
    assert resp.status_code == 400


def test_inverter_sim_returns_reading(client):
    resp = client.get("/inverter_sim?forecast_power=2.0&api_key=test123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "measured_power" in body
