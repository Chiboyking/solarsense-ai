"""
app.py
======
Flask backend for SolarSense AI — Nigeria edition.

Routes
------
GET  /                    -> main HTML page
GET  /states                -> list of Nigeria's 36 states + FCT (name, capital, lat, lon)
GET  /predict              -> full prediction payload (weather, forecast,
                               battery plan, nowcast)
GET  /inverter_sim         -> simulated "measured" inverter reading (kept
                               as a manual-comparison demo; see README)
POST /contribute            -> user submits an actual reading to help
                               retrain the model for their zone
GET  /contributions/summary -> aggregate contribution counts (no per-user
                               accuracy scoring — this isn't a leaderboard)

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from datetime import datetime, timezone

import numpy as np
import requests
from flask import Flask, jsonify, request, send_from_directory, render_template
from flask_cors import CORS
from joblib import load as joblib_load

import train_model as tm

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTRIBUTIONS_PATH = os.path.join(BASE_DIR, "contributions.json")

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Model loading / auto-training fallback
# ---------------------------------------------------------------------------
_MODELS: dict = {}  # zone -> {point, lower, upper, scaler, meta}
_WEATHER_CACHE: dict = {}  # (lat_r, lon_r) -> (timestamp, data)
CACHE_TTL_SECONDS = 15 * 60
WEATHER_DISK_CACHE_PATH = os.path.join(BASE_DIR, "weather_disk_cache.json")
SEED_WEATHER_PATH = os.path.join(BASE_DIR, "data", "seed_weather_fallback.json")
_RATE_LIMIT_LOG: dict = {}  # (ip, bucket) -> list[timestamps]


def rate_limited(bucket: str, max_requests: int, window_seconds: int) -> bool:
    """Simple in-memory sliding-window rate limiter, keyed by client IP.
    Good enough for a single-process deployment; swap for Redis/Flask-Limiter
    if this ever runs with multiple processes sharing load."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    key = (ip, bucket)
    now = time.time()
    hits = [t for t in _RATE_LIMIT_LOG.get(key, []) if now - t < window_seconds]
    hits.append(now)
    _RATE_LIMIT_LOG[key] = hits
    return len(hits) > max_requests


def ensure_models() -> None:
    """Load models from disk, training them first if any zone is missing.

    Guarded with a simple file lock so that if a production server starts
    multiple worker processes at once (e.g. gunicorn --workers 2), only one
    of them does the actual training — the others wait for it to finish
    instead of duplicating the work / racing on the same output files.
    """
    missing = [
        zone for zone in tm.NIGERIA_ZONES
        if not os.path.exists(os.path.join(tm.MODEL_DIR, zone, "point_model.joblib"))
    ]

    if missing:
        os.makedirs(tm.MODEL_DIR, exist_ok=True)
        lock_path = os.path.join(tm.MODEL_DIR, ".training.lock")
        acquired = False
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
        except FileExistsError:
            acquired = False

        if acquired:
            print(f"[app] Missing trained models for zones {missing}. Training now "
                  f"(tries real NASA POWER data first, falls back to synthetic if "
                  f"unreachable — this happens once)...")
            try:
                for zone in missing:
                    tm.train_zone(zone)
            finally:
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
        else:
            print("[app] Another worker is already training models — waiting...")
            waited = 0
            while waited < 600 and not os.path.exists(
                os.path.join(tm.MODEL_DIR, missing[-1], "point_model.joblib")
            ):
                time.sleep(2)
                waited += 2

    for zone in tm.NIGERIA_ZONES:
        zone_dir = os.path.join(tm.MODEL_DIR, zone)
        with open(os.path.join(zone_dir, "meta.json")) as f:
            meta = json.load(f)
        _MODELS[zone] = {
            "point": joblib_load(os.path.join(zone_dir, "point_model.joblib")),
            "lower": joblib_load(os.path.join(zone_dir, "lower_model.joblib")),
            "upper": joblib_load(os.path.join(zone_dir, "upper_model.joblib")),
            "scaler": joblib_load(os.path.join(zone_dir, "scaler.joblib")),
            "meta": meta,
        }
    print(f"[app] Loaded models for zones: {list(_MODELS.keys())}")
    for zone, bundle in _MODELS.items():
        print(f"       {zone}: data_source={bundle['meta'].get('data_source')}")


# ---------------------------------------------------------------------------
# Weather fetching (Open-Meteo, real-time, no API key required)
# ---------------------------------------------------------------------------
def _load_disk_cache() -> dict:
    if not os.path.exists(WEATHER_DISK_CACHE_PATH):
        return {}
    try:
        with open(WEATHER_DISK_CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_to_disk_cache(lat: float, lon: float, data: dict) -> None:
    try:
        cache = _load_disk_cache()
        key = f"{round(lat, 2)},{round(lon, 2)}"
        cache[key] = {"saved_at": time.time(), "data": data}
        # cap to the most recent 50 locations so the file can't grow forever
        if len(cache) > 50:
            oldest = sorted(cache.items(), key=lambda kv: kv[1]["saved_at"])[: len(cache) - 50]
            for k, _ in oldest:
                del cache[k]
        with open(WEATHER_DISK_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError as exc:
        print(f"[app] Could not write weather disk cache: {exc}")


def _nearest_disk_cache_entry(lat: float, lon: float) -> dict | None:
    cache = _load_disk_cache()
    if not cache:
        return None
    best_key, best_dist = None, None
    for key, entry in cache.items():
        try:
            klat, klon = (float(v) for v in key.split(","))
        except ValueError:
            continue
        dist = (klat - lat) ** 2 + (klon - lon) ** 2
        if best_dist is None or dist < best_dist:
            best_key, best_dist = key, dist
    return cache.get(best_key) if best_key else None


def fetch_weather(lat: float, lon: float) -> tuple[dict, str]:
    """Returns (weather_json, source) where source is one of:
    "live", "disk_cache", or "seed_fallback". This is always reported back
    to the caller (never silently substituted) — see /predict's
    `data_freshness` field."""
    key = (round(lat, 2), round(lon, 2))
    now = time.time()
    cached = _WEATHER_CACHE.get(key)
    if cached and (now - cached[0] < CACHE_TTL_SECONDS):
        return cached[1], "live"

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=shortwave_radiation,temperature_2m,relative_humidity_2m,"
        "wind_speed_10m,pressure_msl,shortwave_radiation_clear_sky,cloud_cover"
        "&past_days=1&forecast_days=2&timeformat=unixtime&timezone=auto"
    )
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            _WEATHER_CACHE[key] = (now, data)
            _save_to_disk_cache(lat, lon, data)
            return data, "live"
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))

    # Live fetch failed — fall back rather than showing a hard error,
    # important for demo reliability on flaky networks. Always transparent
    # about which tier was used.
    print(f"[app] Live weather fetch failed ({last_err}); falling back.")
    disk_entry = _nearest_disk_cache_entry(lat, lon)
    if disk_entry:
        return disk_entry["data"], "disk_cache"

    if os.path.exists(SEED_WEATHER_PATH):
        try:
            with open(SEED_WEATHER_PATH) as f:
                return json.load(f), "seed_fallback"
        except (json.JSONDecodeError, OSError):
            pass

    raise RuntimeError(f"Weather API unreachable after retries and no fallback available: {last_err}")


def locate(lat: float, lon: float) -> dict:
    state, capital, zone = tm.nearest_state(lat, lon)
    return {"state": state, "capital": capital, "zone": zone, "label": f"{capital}, {state} State"}


# ---------------------------------------------------------------------------
# Feature engineering + prediction
# ---------------------------------------------------------------------------
def build_features(hourly: dict, idx: int, lat: float) -> dict | None:
    try:
        ghi = hourly["shortwave_radiation"][idx] or 0.0
        clear_ghi = hourly["shortwave_radiation_clear_sky"][idx] or 0.0
        temp = hourly["temperature_2m"][idx]
        wind = hourly["wind_speed_10m"][idx]
        cloud = (hourly.get("cloud_cover", [0] * len(hourly["shortwave_radiation"]))[idx] or 0) / 100.0
        ts = hourly["time"][idx]
    except (KeyError, IndexError, TypeError):
        return None

    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    clear_sky_index = (ghi / clear_ghi) if clear_ghi > 5 else 0.0
    clear_sky_index = max(0.0, min(1.2, clear_sky_index))

    doy = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60.0
    decl = -23.44 * math.cos(math.radians(360 / 365 * (doy + 10)))
    hour_angle = 15 * (hour - 12)
    zenith = math.degrees(math.acos(
        max(-1, min(1,
            math.sin(math.radians(lat)) * math.sin(math.radians(decl)) +
            math.cos(math.radians(lat)) * math.cos(math.radians(decl)) * math.cos(math.radians(hour_angle))
        ))
    ))

    return {
        "ghi": ghi,
        "clear_sky_ghi": clear_ghi,
        "clear_sky_index": clear_sky_index,
        "temp_air": temp,
        "wind_speed": wind,
        "cloud_cover": cloud,
        "hour_sin": math.sin(2 * math.pi * dt.hour / 24),
        "hour_cos": math.cos(2 * math.pi * dt.hour / 24),
        "doy_sin": math.sin(2 * math.pi * doy / 365),
        "doy_cos": math.cos(2 * math.pi * doy / 365),
        "solar_zenith": zenith,
        "_dt": dt,
        "_raw_cloud_pct": cloud * 100,
    }


def predict_at(zone: str, feat: dict, capacity_kw: float) -> dict:
    bundle = _MODELS[zone]
    x = np.array([[feat[f] for f in tm.FEATURES]])
    x_scaled = bundle["scaler"].transform(x)

    ref_kw = bundle["meta"].get("panel_kw_reference", 5.0)
    scale = capacity_kw / ref_kw if ref_kw else 1.0

    point = float(bundle["point"].predict(x_scaled)[0]) * scale
    lower = float(bundle["lower"].predict(x_scaled)[0]) * scale
    upper = float(bundle["upper"].predict(x_scaled)[0]) * scale

    point = max(0.0, point)
    lower = max(0.0, min(lower, point))
    upper = max(upper, point)
    upper = min(upper, capacity_kw * 1.05)

    return {"power": round(point, 3), "lower": round(lower, 3), "upper": round(upper, 3)}


# ---------------------------------------------------------------------------
# Battery optimization (greedy self-consumption maximizer)
# ---------------------------------------------------------------------------
def optimize_battery(forecast_series: list, capacity_kwh: float, soc_pct: float,
                      efficiency: float, load_kw: float = 1.0) -> list:
    """Physical battery dispatch only — maximizes self-consumption (charge
    from surplus solar, discharge to cover deficit) hour by hour. No pricing
    or cost math; this is charge/discharge scheduling, not a savings
    estimate."""
    soc_kwh = capacity_kwh * (soc_pct / 100.0)
    schedule = []

    for entry in forecast_series:
        hour = entry["_hour"]
        gen = entry["power"]
        net = gen - load_kw
        grid_import = 0.0
        grid_export = 0.0

        if net >= 0:
            charge_room = (capacity_kwh - soc_kwh) / max(efficiency, 0.01)
            charge = min(net, charge_room)
            soc_kwh += charge * efficiency
            leftover = net - charge
            grid_export = leftover
        else:
            deficit = -net
            discharge_avail = soc_kwh * efficiency
            discharge = min(deficit, discharge_avail)
            soc_kwh -= discharge / max(efficiency, 0.01)
            remaining_deficit = deficit - discharge
            grid_import = remaining_deficit

        schedule.append({
            "hour": hour,
            "generation_kw": round(gen, 3),
            "soc_kwh": round(soc_kwh, 3),
            "soc_pct": round(100 * soc_kwh / capacity_kwh, 1) if capacity_kwh else 0,
            "grid_import_kw": round(grid_import, 3),
            "grid_export_kw": round(grid_export, 3),
        })

    return schedule



# ---------------------------------------------------------------------------
# Nowcast (0-2h smart persistence of clear-sky index)
# ---------------------------------------------------------------------------
def compute_nowcast(features_by_hour: list) -> dict | None:
    if len(features_by_hour) < 3:
        return None
    now_cloud = features_by_hour[0]["_raw_cloud_pct"]
    future_cloud = features_by_hour[2]["_raw_cloud_pct"]
    delta = future_cloud - now_cloud
    if delta > 20:
        return {"level": "warning", "message_key": "nowcast_clouds_approaching", "delta_pct": round(delta, 1)}
    if delta < -20:
        return {"level": "good", "message_key": "nowcast_clearing_up", "delta_pct": round(delta, 1)}
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    """Lightweight health check for hosting platforms (Render/Railway).
    Returns immediately without touching models or external APIs."""
    return jsonify({"status": "ok", "models_loaded": list(_MODELS.keys())})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/accuracy")
def accuracy_page():
    return render_template("accuracy.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/model_info")
def model_info():
    """Exposes honest per-zone validation metrics: model MAE vs. a naive
    persistence baseline on the same held-out real data, plus which data
    source trained it. No inflated claims — this is what a defense panel
    should be shown directly rather than told about."""
    return jsonify({
        zone: {
            "zone_label": b["meta"].get("zone_label"),
            "data_source": b["meta"].get("data_source"),
            "trained_at": b["meta"].get("trained_at"),
            "test_mae_kw": b["meta"].get("test_mae_kw"),
            "persistence_baseline_mae_kw": b["meta"].get("persistence_baseline_mae_kw"),
            "skill_score_vs_persistence": b["meta"].get("skill_score_vs_persistence"),
            "n_test_samples": b["meta"].get("n_test_samples"),
        }
        for zone, b in _MODELS.items()
    })


@app.route("/states")
def states():
    return jsonify([
        {"state": s, "capital": c, "lat": lat, "lon": lon, "zone": z}
        for s, c, lat, lon, z in tm.NIGERIA_STATES
    ])


@app.route("/predict")
def predict():
    if rate_limited("predict", max_requests=60, window_seconds=60):
        return jsonify({"error": "rate_limited", "detail": "Too many requests — please slow down."}), 429

    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required numeric query params"}), 400

    capacity = float(request.args.get("capacity", 5.0))
    battery_capacity = float(request.args.get("battery_capacity", 10.0))
    battery_soc = float(request.args.get("battery_soc", 50.0))
    efficiency = float(request.args.get("efficiency", 0.90))
    load_kw = float(request.args.get("load_kw", max(0.5, capacity * 0.35)))

    try:
        weather, weather_source = fetch_weather(lat, lon)
    except RuntimeError as exc:
        return jsonify({"error": "weather_unavailable", "detail": str(exc)}), 503

    hourly = weather.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return jsonify({"error": "no_weather_data"}), 503

    now_ts = time.time()
    start_idx = 0
    for i, t in enumerate(times):
        if t >= now_ts:
            start_idx = i
            break

    loc = locate(lat, lon)
    zone = loc["zone"] if loc["zone"] in _MODELS else next(iter(_MODELS))

    horizons = {"1h": 1, "3h": 3, "6h": 6, "12h": 12, "24h": 24}
    forecast = {}
    hourly_series_for_battery = []
    features_window = []

    max_needed = max(horizons.values())
    for offset in range(0, max_needed + 1):
        idx = start_idx + offset
        if idx >= len(times):
            break
        feat = build_features(hourly, idx, lat)
        if feat is None:
            continue
        pred = predict_at(zone, feat, capacity)
        feat["_hour"] = feat["_dt"].hour
        pred["_hour"] = feat["_hour"]
        hourly_series_for_battery.append(pred)
        features_window.append(feat)
        label = next((k for k, v in horizons.items() if v == offset), None)
        if label:
            forecast[label] = {"power": pred["power"], "lower": pred["lower"], "upper": pred["upper"]}

    battery_plan = optimize_battery(
        hourly_series_for_battery[:24], battery_capacity, battery_soc,
        efficiency, load_kw=load_kw,
    )

    nowcast_alert = compute_nowcast(features_window)

    current_feat = features_window[0] if features_window else None
    current = None
    if current_feat:
        current = {
            "ghi": round(current_feat["ghi"], 1),
            "temp": round(current_feat["temp_air"], 1),
            "wind_speed": round(current_feat["wind_speed"], 1),
            "cloud_cover_pct": round(current_feat["_raw_cloud_pct"], 1),
            "clear_sky_index": round(current_feat["clear_sky_index"], 2),
        }

    response = {
        "location": loc["label"],
        "state": loc["state"],
        "capital": loc["capital"],
        "climate_zone": zone,
        "current": current,
        "forecast": forecast,
        "hourly_forecast": [
            {"hour": e["_hour"], "power": e["power"]} for e in hourly_series_for_battery[:24]
        ],
        "battery_plan": battery_plan,
        "nowcast_alert": nowcast_alert,
        "data_freshness": weather_source,  # "live" | "disk_cache" | "seed_fallback"
        "model_backend": _MODELS[zone]["meta"].get("backend"),
        "model_data_source": _MODELS[zone]["meta"].get("data_source"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(response)


@app.route("/inverter_sim")
def inverter_sim():
    """Manual inverter comparison (kept as-is per project decision, pending
    a real per-brand inverter integration). Given a forecast value, returns
    a comparison reading derived from it plus small noise so the UI can
    demonstrate the soiling/degradation-detection workflow without a real
    device connected."""
    try:
        forecast_power = float(request.args.get("forecast_power", 0))
    except (TypeError, ValueError):
        forecast_power = 0.0
    api_key = request.args.get("api_key", "")
    if not api_key:
        return jsonify({"error": "api_key required"}), 400

    noise = np.random.normal(0, 0.06) * max(forecast_power, 0.5)
    degradation_factor = 0.97
    measured = max(0.0, forecast_power * degradation_factor + noise)
    return jsonify({
        "measured_power": round(measured, 3),
        "forecast_power": round(forecast_power, 3),
        "deviation_pct": round(((measured - forecast_power) / forecast_power * 100), 1) if forecast_power > 0 else 0,
        "note": "Simulated comparison for demo purposes — no real inverter connected yet.",
    })


def _load_contributions() -> list:
    if not os.path.exists(CONTRIBUTIONS_PATH):
        return []
    try:
        with open(CONTRIBUTIONS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_contributions(entries: list) -> None:
    with open(CONTRIBUTIONS_PATH, "w") as f:
        json.dump(entries, f, indent=2)


@app.route("/contribute", methods=["POST"])
def contribute():
    """Users submit an actual reading (state + timestamp + power produced).
    This is stored as raw training data for periodic model retraining — it
    is NOT a public accuracy leaderboard, and nothing here scores individual
    users."""
    if rate_limited("contribute", max_requests=10, window_seconds=60):
        return jsonify({"error": "rate_limited", "detail": "Too many submissions — please wait a minute and try again."}), 429

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
        actual_power = float(data["actual_power"])
        recorded_at = data.get("recorded_at") or datetime.now(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat, lon, actual_power are required"}), 400

    # Plausibility bounds — rejects obvious garbage/abuse without needing
    # real auth. 50kW comfortably covers residential and small-commercial
    # systems; -90..90 / -180..180 are valid lat/lon ranges.
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({"error": "lat/lon out of range"}), 400
    if not (0 <= actual_power <= 50):
        return jsonify({"error": "actual_power out of plausible range (0-50 kW)"}), 400

    loc = locate(lat, lon)
    entries = _load_contributions()
    entries.append({
        "state": loc["state"],
        "zone": loc["zone"],
        "actual_power_kw": actual_power,
        "recorded_at": recorded_at,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    entries = entries[-20000:]  # cap file growth
    _save_contributions(entries)

    state_count = sum(1 for e in entries if e["state"] == loc["state"])
    total_count = len(entries)
    return jsonify({
        "status": "saved",
        "state": loc["state"],
        "state_contributions": state_count,
        "total_contributions": total_count,
        "note": "Thanks — this helps sharpen forecasts for your area over time.",
    })


@app.route("/contributions/summary")
def contributions_summary():
    entries = _load_contributions()
    by_state: dict = {}
    for e in entries:
        by_state[e["state"]] = by_state.get(e["state"], 0) + 1
    return jsonify({
        "total": len(entries),
        "by_state": by_state,
    })


@app.route("/static/translations/<lang>.json")
def translations(lang):
    return send_from_directory(os.path.join(BASE_DIR, "static", "translations"), f"{lang}.json")


if __name__ == "__main__":
    ensure_models()
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
else:
    ensure_models()
