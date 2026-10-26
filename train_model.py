"""
train_model.py
================
Training script for the Solar AI Predictor — Nigeria edition.

DATA SOURCE
-----------
Training now uses **real historical weather measurements** from NASA POWER
(power.larc.nasa.gov), a free, public, no-API-key-required dataset built from
NASA satellite and reanalysis data. For each of Nigeria's six climate zones
(see NIGERIA_ZONES below) we pull a full year of real hourly irradiance,
clear-sky irradiance, temperature, wind, and cloud cover for a representative
location, then derive AC power output using a standard, documented PV
physical model (irradiance-linear with temperature de-rating — the same
approach used by tools like PVGIS). There is no free nationwide dataset of
*actual metered household PV output* for Nigeria, so power itself is modeled
from real weather inputs rather than measured directly — this is disclosed
here and in the README rather than presented as something it isn't.

If NASA POWER is unreachable (offline dev environment, outage, etc.), the
script automatically falls back to physically-realistic *synthetic* weather
via pvlib's clear-sky model, so the project still trains and runs with zero
external dependencies. The fallback is always logged loudly so it's never
silently mistaken for real data.

NIGERIA CLIMATE ZONES
----------------------
Nigeria is grouped into six broad climate zones (Sahel, Sudan Savanna, Guinea
Savanna/Middle Belt, Derived Savanna, Rainforest, Coastal/Niger Delta), each
with a representative state used to pull training data. Every one of
Nigeria's 36 states + the FCT is mapped to one of these six zones (see
NIGERIA_STATES). This mirrors real Köppen-style climate variation across the
country — from the semi-arid north to the humid coast — without requiring a
separate model per state.

For defensibility: this six-zone split follows Nigeria's standard
agro-ecological zone classification (used in Nigerian agricultural and
climate research), which in turn maps onto three real Köppen-Geiger classes
present in the country — BSh (hot semi-arid: Sahel), Aw (tropical savanna:
Sudan/Guinea/Derived Savanna — split three ways here because solar-relevant
cloud cover and humidity still vary meaningfully within Aw across that
band), and Am/Af (tropical monsoon/rainforest: Rainforest, Coastal/Niger
Delta). It is a deliberate simplification from 37 possible per-state models,
not an arbitrary one — the trade-off (and the option to go finer-grained
later, e.g. with a proper Köppen raster lookup) is intentional and
documented here rather than hidden.

Models produced (per zone), saved to model/<zone>/:
  - point_model.joblib, lower_model.joblib, upper_model.joblib (XGBoost)
  - scaler.joblib
  - meta.json (records whether real NASA POWER data or synthetic fallback was used)

Run:
    python train_model.py
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from joblib import dump
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:  # pragma: no cover - graceful degrade
    HAS_XGB = False

import pvlib

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

FEATURES = [
    "ghi", "clear_sky_ghi", "clear_sky_index", "temp_air",
    "wind_speed", "cloud_cover", "hour_sin", "hour_cos",
    "doy_sin", "doy_cos", "solar_zenith",
]

PANEL_KW_REFERENCE = 5.0  # reference system size the models are trained against
NASA_POWER_YEAR = 2024     # most recent full calendar year (avoids NASA's near-real-time data lag)
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
NASA_POWER_PARAMS = "ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,T2M,WS10M,CLOUD_AMT"

# ---------------------------------------------------------------------------
# Nigeria's 36 states + FCT, each mapped to a climate zone.
# (state, capital, lat, lon, zone)
# ---------------------------------------------------------------------------
NIGERIA_STATES = [
    ("Abia", "Umuahia", 5.5333, 7.4833, "rainforest"),
    ("Adamawa", "Yola", 9.2035, 12.4954, "sudan_savanna"),
    ("Akwa Ibom", "Uyo", 5.0377, 7.9128, "coastal_delta"),
    ("Anambra", "Awka", 6.2120, 7.0690, "rainforest"),
    ("Bauchi", "Bauchi", 10.3158, 9.8442, "sudan_savanna"),
    ("Bayelsa", "Yenagoa", 4.9247, 6.2642, "coastal_delta"),
    ("Benue", "Makurdi", 7.7322, 8.5391, "guinea_savanna"),
    ("Borno", "Maiduguri", 11.8333, 13.1500, "sahel"),
    ("Cross River", "Calabar", 4.9500, 8.3167, "rainforest"),
    ("Delta", "Asaba", 6.1987, 6.7346, "rainforest"),
    ("Ebonyi", "Abakaliki", 6.3249, 8.1137, "rainforest"),
    ("Edo", "Benin City", 6.3350, 5.6037, "rainforest"),
    ("Ekiti", "Ado-Ekiti", 7.6211, 5.2214, "derived_savanna"),
    ("Enugu", "Enugu", 6.5244, 7.5186, "rainforest"),
    ("FCT", "Abuja", 9.0765, 7.3986, "guinea_savanna"),
    ("Gombe", "Gombe", 10.2897, 11.1673, "sudan_savanna"),
    ("Imo", "Owerri", 5.4840, 7.0333, "rainforest"),
    ("Jigawa", "Dutse", 11.7564, 9.3350, "sahel"),
    ("Kaduna", "Kaduna", 10.5222, 7.4383, "sudan_savanna"),
    ("Kano", "Kano", 12.0022, 8.5920, "sudan_savanna"),
    ("Katsina", "Katsina", 12.9908, 7.6017, "sahel"),
    ("Kebbi", "Birnin Kebbi", 12.4539, 4.1975, "sahel"),
    ("Kogi", "Lokoja", 7.8023, 6.7333, "guinea_savanna"),
    ("Kwara", "Ilorin", 8.4966, 4.5426, "guinea_savanna"),
    ("Lagos", "Ikeja", 6.6018, 3.3515, "coastal_delta"),
    ("Nasarawa", "Lafia", 8.4939, 8.5169, "guinea_savanna"),
    ("Niger", "Minna", 9.6139, 6.5569, "guinea_savanna"),
    ("Ogun", "Abeokuta", 7.1475, 3.3619, "derived_savanna"),
    ("Ondo", "Akure", 7.2571, 5.2058, "derived_savanna"),
    ("Osun", "Osogbo", 7.7719, 4.5569, "derived_savanna"),
    ("Oyo", "Ibadan", 7.3775, 3.9470, "derived_savanna"),
    ("Plateau", "Jos", 9.8965, 8.8583, "guinea_savanna"),
    ("Rivers", "Port Harcourt", 4.8156, 7.0498, "coastal_delta"),
    ("Sokoto", "Sokoto", 13.0059, 5.2476, "sahel"),
    ("Taraba", "Jalingo", 8.8833, 11.3667, "guinea_savanna"),
    ("Yobe", "Damaturu", 11.7469, 11.9609, "sahel"),
    ("Zamfara", "Gusau", 12.1700, 6.6600, "sahel"),
]

# Representative (lat, lon) used to pull training data for each zone.
NIGERIA_ZONES = {
    "sahel": {"lat": 13.0059, "lon": 5.2476, "label": "Sahel (Sokoto)"},
    "sudan_savanna": {"lat": 12.0022, "lon": 8.5920, "label": "Sudan Savanna (Kano)"},
    "guinea_savanna": {"lat": 9.0765, "lon": 7.3986, "label": "Guinea Savanna / Middle Belt (Abuja)"},
    "derived_savanna": {"lat": 7.3775, "lon": 3.9470, "label": "Derived Savanna (Ibadan)"},
    "rainforest": {"lat": 6.5244, "lon": 7.5186, "label": "Rainforest (Enugu)"},
    "coastal_delta": {"lat": 6.6018, "lon": 3.3515, "label": "Coastal / Niger Delta (Lagos)"},
}


def zone_for_state(state_name: str) -> str:
    for s in NIGERIA_STATES:
        if s[0].lower() == state_name.lower():
            return s[4]
    return "guinea_savanna"  # central fallback


def nearest_state(lat: float, lon: float) -> tuple[str, str, str]:
    """Returns (state, capital, zone) for the Nigerian state whose capital is
    closest to the given coordinates (simple Euclidean distance in degrees,
    which is fine at Nigeria's scale)."""
    best = min(NIGERIA_STATES, key=lambda s: (s[2] - lat) ** 2 + (s[3] - lon) ** 2)
    return best[0], best[1], best[4]


def zone_for_latlon(lat: float, lon: float) -> str:
    return nearest_state(lat, lon)[2]


# ---------------------------------------------------------------------------
# Real data: NASA POWER
# ---------------------------------------------------------------------------
def fetch_nasa_power(lat: float, lon: float, year: int) -> pd.DataFrame:
    """Fetches one year of real hourly weather data from NASA POWER."""
    params = {
        "parameters": NASA_POWER_PARAMS,
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "JSON",
    }
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(NASA_POWER_URL, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            data = payload["properties"]["parameter"]
            break
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"NASA POWER unreachable after retries: {last_err}")

    idx = pd.to_datetime(list(data["ALLSKY_SFC_SW_DWN"].keys()), format="%Y%m%d%H", utc=True)
    df = pd.DataFrame({
        "ghi": list(data["ALLSKY_SFC_SW_DWN"].values()),
        "clear_sky_ghi": list(data["CLRSKY_SFC_SW_DWN"].values()),
        "temp_air": list(data["T2M"].values()),
        "wind_speed": list(data["WS10M"].values()),
        "cloud_cover_pct": list(data["CLOUD_AMT"].values()),
    }, index=idx)

    # NASA POWER uses -999 as a missing-value sentinel; drop those rows.
    df = df[(df["ghi"] > -900) & (df["clear_sky_ghi"] > -900) & (df["temp_air"] > -900)]
    df["cloud_cover"] = (df["cloud_cover_pct"].clip(lower=0, upper=100) / 100.0)
    df["clear_sky_index"] = (df["ghi"] / df["clear_sky_ghi"].replace(0, np.nan)).clip(0, 1.2).fillna(0)
    return df


def derive_power_from_weather(df: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    """Derives simulated AC power output from real weather inputs using a
    standard temperature-derated linear PV model (same approach PVGIS uses
    when metered output isn't available). This is documented, not hidden:
    the *weather* is real; the *panel response to that weather* is a
    physical model, because no free nationwide metered-output dataset for
    Nigerian residential PV systems exists."""
    site = pvlib.location.Location(lat, lon, tz="UTC")
    solpos = site.get_solarposition(df.index)
    df = df.copy()
    df["solar_zenith"] = solpos["apparent_zenith"].values
    df["hour"] = df.index.hour
    df["doy"] = df.index.dayofyear
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["doy_sin"] = np.sin(2 * np.pi * df["doy"] / 365)
    df["doy_cos"] = np.cos(2 * np.pi * df["doy"] / 365)

    temp_derate = 1 - 0.004 * np.clip(df["temp_air"] - 25, -10, 40)
    ac_power = np.clip((df["ghi"] / 1000.0) * PANEL_KW_REFERENCE * temp_derate, 0, PANEL_KW_REFERENCE)
    ac_power = ac_power * (solpos["apparent_elevation"].values > 0)
    df["ac_power"] = np.clip(ac_power, 0, PANEL_KW_REFERENCE)
    return df


# ---------------------------------------------------------------------------
# Fallback: physically-realistic synthetic data (used only if NASA POWER is
# unreachable)
# ---------------------------------------------------------------------------
def synthesize_zone_data(zone: str, days: int = 400) -> pd.DataFrame:
    cfg = NIGERIA_ZONES[zone]
    lat, lon = cfg["lat"], cfg["lon"]
    site = pvlib.location.Location(lat, lon, tz="UTC")
    start = datetime(2024, 1, 1)
    idx = pd.date_range(start, start + timedelta(days=days), freq="1h", tz="UTC")

    clear_sky = site.get_clearsky(idx, model="ineichen")
    rng = np.random.default_rng(SEED + hash(zone) % 1000)

    base_cloud = {
        "sahel": 0.20, "sudan_savanna": 0.35, "guinea_savanna": 0.45,
        "derived_savanna": 0.50, "rainforest": 0.60, "coastal_delta": 0.65,
    }[zone]
    cloud_cover = np.clip(
        base_cloud + 0.3 * np.sin(np.linspace(0, 40 * np.pi, len(idx))) + rng.normal(0, 0.15, len(idx)),
        0, 1,
    )
    clear_sky_index = np.clip(1 - 0.85 * cloud_cover + rng.normal(0, 0.05, len(idx)), 0.05, 1.05)
    ghi = (clear_sky["ghi"] * clear_sky_index).clip(lower=0)
    temp_ambient = {
        "sahel": 30, "sudan_savanna": 28, "guinea_savanna": 27,
        "derived_savanna": 27, "rainforest": 26, "coastal_delta": 27,
    }[zone]
    temp_air = temp_ambient + 6 * np.sin(np.linspace(0, 8 * np.pi, len(idx))) + rng.normal(0, 2, len(idx))
    wind_speed = np.clip(rng.normal(2.8, 1.2, len(idx)), 0, None)

    df = pd.DataFrame({
        "ghi": ghi.values,
        "clear_sky_ghi": clear_sky["ghi"].values,
        "clear_sky_index": clear_sky_index,
        "temp_air": temp_air,
        "wind_speed": wind_speed,
        "cloud_cover": cloud_cover,
    }, index=idx)
    return derive_power_from_weather(df, lat, lon)


def load_zone_training_data(zone: str) -> tuple[pd.DataFrame, str]:
    """Returns (dataframe, source_label). Tries NASA POWER first, falls back
    to synthetic pvlib-based generation if unreachable."""
    cfg = NIGERIA_ZONES[zone]
    csv_path = os.path.join(DATA_DIR, f"{zone}.csv")
    if os.path.exists(csv_path):
        print(f"[train] Zone '{zone}': using local override data at {csv_path}")
        return pd.read_csv(csv_path, parse_dates=True, index_col=0), "local_csv_override"

    try:
        print(f"[train] Zone '{zone}': fetching real NASA POWER data for {cfg['label']}...")
        raw = fetch_nasa_power(cfg["lat"], cfg["lon"], NASA_POWER_YEAR)
        df = derive_power_from_weather(raw, cfg["lat"], cfg["lon"])
        print(f"[train] Zone '{zone}': got {len(df)} real hourly records from NASA POWER.")
        return df, "nasa_power_real"
    except RuntimeError as exc:
        print(f"[train] Zone '{zone}': NASA POWER unreachable ({exc}). "
              f"Falling back to physically-modeled SYNTHETIC data.")
        return synthesize_zone_data(zone), "synthetic_fallback"


def backtest_vs_persistence(df: pd.DataFrame, y_test: np.ndarray, test_start_idx: int) -> dict:
    """Validates the model against a naive persistence baseline (this hour's
    power = same hour, previous day) on the same held-out test window.

    This is the standard way to validate a forecasting model when no
    independent ground-truth metered dataset exists to test against: you
    show it beats a naive baseline on real held-out data, rather than just
    reporting an MAE number with nothing to compare it to. A skill score
    near 0 means the model adds nothing over "assume today looks like
    yesterday"; positive means real, measurable skill.
    """
    ac_power = df["ac_power"].values
    persistence_pred = []
    for i in range(test_start_idx, test_start_idx + len(y_test)):
        prev_day_idx = i - 24
        persistence_pred.append(ac_power[prev_day_idx] if prev_day_idx >= 0 else ac_power[0])
    persistence_pred = np.array(persistence_pred)
    persistence_mae = float(np.mean(np.abs(persistence_pred - y_test)))
    return {"persistence_mae_kw": persistence_mae}


def train_quantile_model(X_train, y_train, quantile: float):
    if HAS_XGB:
        model = xgb.XGBRegressor(
            objective="reg:quantileerror", quantile_alpha=quantile,
            n_estimators=150, max_depth=4, learning_rate=0.08, random_state=SEED,
        )
        model.fit(X_train, y_train)
        return model
    model = LinearRegression()
    model.fit(X_train, y_train)
    return model


def train_zone(zone: str) -> None:
    df, source = load_zone_training_data(zone)
    df = df.dropna(subset=FEATURES + ["ac_power"])

    X = df[FEATURES].values
    y = df["ac_power"].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    split = int(len(X_scaled) * 0.85)
    X_train, X_test = X_scaled[:split], X_scaled[split:]
    y_train, y_test = y[:split], y[split:]

    if HAS_XGB:
        point_model = xgb.XGBRegressor(
            n_estimators=250, max_depth=5, learning_rate=0.06,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED,
        )
    else:
        point_model = LinearRegression()
    point_model.fit(X_train, y_train)

    lower_model = train_quantile_model(X_train, y_train, 0.10)
    upper_model = train_quantile_model(X_train, y_train, 0.90)

    preds = point_model.predict(X_test)
    mae = float(np.mean(np.abs(preds - y_test)))

    baseline = backtest_vs_persistence(df, y_test, split)
    persistence_mae = baseline["persistence_mae_kw"]
    skill_score = (1 - mae / persistence_mae) if persistence_mae > 1e-6 else None

    print(f"[train] Zone '{zone}' ({source}): model MAE = {mae:.4f} kW | "
          f"persistence baseline MAE = {persistence_mae:.4f} kW | "
          f"skill vs. persistence = {skill_score:+.1%}" if skill_score is not None else "")

    zone_dir = os.path.join(MODEL_DIR, zone)
    os.makedirs(zone_dir, exist_ok=True)
    dump(point_model, os.path.join(zone_dir, "point_model.joblib"))
    dump(lower_model, os.path.join(zone_dir, "lower_model.joblib"))
    dump(upper_model, os.path.join(zone_dir, "upper_model.joblib"))
    dump(scaler, os.path.join(zone_dir, "scaler.joblib"))

    with open(os.path.join(zone_dir, "meta.json"), "w") as f:
        json.dump({
            "zone": zone,
            "zone_label": NIGERIA_ZONES[zone]["label"],
            "features": FEATURES,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "test_mae_kw": mae,
            "persistence_baseline_mae_kw": persistence_mae,
            "skill_score_vs_persistence": skill_score,
            "n_test_samples": len(y_test),
            "backend": "xgboost" if HAS_XGB else "linear_fallback",
            "data_source": source,  # "nasa_power_real" | "synthetic_fallback" | "local_csv_override"
            "panel_kw_reference": PANEL_KW_REFERENCE,
        }, f, indent=2)


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)
    for zone in NIGERIA_ZONES:
        train_zone(zone)
    print("[train] All zone models trained and saved to model/")


if __name__ == "__main__":
    main()
