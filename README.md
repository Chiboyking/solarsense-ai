# SolarSense AI — Nigeria Edition

A production-quality, mobile-first Progressive Web App for solar power
forecasting and battery storage optimization, built specifically around
Nigeria's 36 states + FCT. **Zero external credentials or API keys
required** — clone it and run it.

## Features

- **Nigeria-only location**: pick any of the 36 states + FCT (matched to
  its capital) from a dropdown, or tap "Detect My Location" to auto-snap to
  the nearest state via the browser's Geolocation API. The full dashboard
  is visible immediately with a default Lagos forecast — nothing is gated
  behind setting a location first.
- **Real-time weather & solar data** from the free Open-Meteo API (no key),
  with clear-sky index and irradiance features computed server-side.
- **Multi-horizon AI forecasting** (1h / 3h / 6h / 12h / 24h) with 10th/90th
  percentile uncertainty bands, plus a real-data-driven "next 24 hours"
  curve, served from per-climate-zone XGBoost models.
- **Nigeria climate-zone ensemble**: requests are routed to a model trained
  for one of six real Nigerian climate zones (Sahel, Sudan Savanna, Guinea
  Savanna/Middle Belt, Derived Savanna, Rainforest, Coastal/Niger Delta) —
  see "Training data" below.
- **Battery storage optimization**: a greedy dispatch algorithm computes a
  24h charge/discharge schedule to maximize self-consumption, supporting
  flat-rate and time-of-use (Band A/B/C-style) tariffs, priced in ₦/kWh.
- **Economic savings estimate**: "Forecast Value" compares the optimized
  system against a naive no-forecast, no-battery baseline, in Naira.
- **0–2h nowcasting** via smart persistence of the clear-sky index / cloud
  cover trend.
- **Inverter comparison (demo)**: enter any dummy API key to see a simulated
  "measured" reading compared against the forecast. This is explicitly
  flagged in the UI as a demo — there's no generic way to connect to a real
  inverter without picking a specific brand/API, so it's left as a
  placeholder pending that decision (see "Known limitations" below).
- **PWA**: installable, offline-capable (caches the app shell + last
  forecast), with an in-app "Enable Alerts" notification for forecast drops.
- **"My Data" — community data contribution**: users can log an actual
  reading (state auto-detected + power produced). This is stored as raw
  training data for periodic model retraining, aggregated by state count
  only — it is **not** a public accuracy leaderboard and does not score
  individual users.
- **5 languages**: English, Spanish, French, German, Portuguese — switch
  instantly, no reload.

## Quick start

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser (or on your phone, once
you expose the port on your LAN, to test the installable PWA experience).

The first run automatically trains lightweight per-climate-zone models on
synthetic, physically-realistic data (via pvlib clear-sky models) if no
trained models are found in `model/`. This takes a few seconds and only
happens once. To retrain explicitly:

```bash
python train_model.py
```

## Deploying to Render / Railway

The app is ready to deploy as-is, with these platform-specific settings:

**Build command:**
```
pip install -r requirements.txt && python train_model.py
```
Running training during the *build* step (not on first request) matters —
NASA POWER fetches + retries across 6 zones can take a couple of minutes,
which will blow past most platforms' startup/health-check timeouts if it
happens at boot instead.

**Start command** (also provided as a `Procfile`, which Railway/Render both
auto-detect):
```
gunicorn app:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
```
Both platforms inject `$PORT` automatically — no config needed there.

**Health check path:** `/healthz` (returns instantly, doesn't touch models
or external APIs — use this instead of `/` for the platform's health check
setting, since `/` renders the full page).

**⚠️ Persistence — read this before relying on "My Data":**
Render and Railway's default filesystem is **ephemeral** — anything written
to disk (`contributions.json`, and the trained `model/` files if not
pre-built) is wiped on every redeploy or restart. For this project that
means:
- Trained models: fine to regenerate on each deploy via the build command
  above (a couple of minutes, real data if reachable, synthetic fallback if
  not).
- `contributions.json` (My Data submissions): **will be lost on redeploy**
  unless you either (a) add a persistent volume/disk in your platform's
  dashboard and point `CONTRIBUTIONS_PATH` at it, or (b) swap
  `_load_contributions`/`_save_contributions` in `app.py` for a real
  database (Render and Railway both offer managed Postgres — this is the
  better long-term fix once you have real users submitting data).

**Environment variables:** none are required to start. `PORT` is set
automatically by the platform. Set `FLASK_DEBUG=1` only for local debugging
— never in production (the app defaults to `0`/off).

## Folder structure

```
solar-ai/
├── app.py                     # Flask app: routes, weather fetch, prediction, battery, leaderboard
├── train_model.py             # Synthetic-data training pipeline (per climate zone)
├── requirements.txt
├── leaderboard.json           # Created automatically on first accuracy report
├── model/
│   ├── __init__.py
│   └── <zone>/                # point_model.joblib, lower_model.joblib, upper_model.joblib, scaler.joblib, meta.json
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    ├── js/
    │   ├── i18n.js
    │   ├── charts.js
    │   ├── battery.js
    │   ├── pwa.js
    │   └── script.js
    ├── icons/icon.svg
    ├── manifest.json
    ├── service-worker.js
    └── translations/{en,es,fr,de,pt}.json
```

## Pages beyond the app itself

- `/about` — the front door for anyone evaluating this outside the defense (a company, a grant reviewer): what it does, how it works, and an honest "what this doesn't do yet" box, on purpose.
- `/accuracy` — a public page rendering `/model_info` in plain English, per zone. This is the credibility move: most commercial forecasting tools don't show their real error rate against a real baseline. This one does.
- `/privacy` and `/terms` — draft stubs, NDPA 2023-aware. Not legal advice; have someone review before this is used in any real pilot conversation.
- `.github/workflows/ci.yml` — runs the 24-test suite on every push, so "does it still work" is answered automatically, not just claimed.

## API

- `GET /states` → all 36 Nigerian states + FCT with capital, lat/lon, and climate zone.
- `GET /predict?lat=&lon=&capacity=&battery_capacity=&battery_soc=&tariff=&price=&feed_in=&load_kw=`
  → full forecast + battery plan + savings (₦) + nowcast + `data_freshness` JSON payload.
- `GET /model_info` → per-zone validation metrics (MAE, persistence-baseline MAE, skill score).
- `GET /healthz` → instant health check for hosting platforms (no model/network dependency).
- `GET /inverter_sim?forecast_power=&api_key=` → simulated inverter reading (demo, see below).
- `POST /contribute` `{lat, lon, actual_power}` → logs a reading as future retraining data (rate-limited, validated).
- `GET /contributions/summary` → total + per-state contribution counts (no individual scoring).

## Training data — what's real and what's modeled

- **Weather is 100% real and live**: every forecast comes from Open-Meteo,
  fetched at request time — nothing about the live app is simulated.
- **Model training now defaults to real historical data**: `train_model.py`
  pulls a full year of real hourly irradiance, clear-sky irradiance,
  temperature, wind, and cloud cover from **NASA POWER**
  (power.larc.nasa.gov) — free, public, no API key — for a representative
  location in each of Nigeria's six climate zones.
- **Power output is still a physical model, not metered data**: there is no
  free, nationwide dataset of actual metered residential PV output for
  Nigeria, so AC power is derived from the real weather inputs using a
  standard temperature-derated linear PV model (the same approach PVGIS
  uses). This is disclosed rather than hidden — see the docstring in
  `derive_power_from_weather()`.
- **Automatic fallback**: if NASA POWER is unreachable (offline dev
  environment, outage, rate limiting), `train_model.py` falls back to
  physically-realistic *synthetic* weather via pvlib's clear-sky model, and
  logs this loudly (`meta.json` per zone records `data_source`:
  `nasa_power_real`, `synthetic_fallback`, or `local_csv_override`) so it's
  never silently mistaken for real data.
- **Bring your own data**: drop a CSV into `data/<zone>.csv` (columns:
  `ghi, clear_sky_ghi, temp_air, wind_speed, cloud_cover, ac_power`) to
  override NASA POWER entirely for a given zone — useful once real Nigerian
  utility or IoT inverter data becomes available.
- **More sources planned**: additional Nigeria-specific data sources (e.g.
  utility/DisCo data, IoT inverter fleets) can be layered in the same way —
  add a fetcher function and register it in `load_zone_training_data()`.

## Model validation

Every zone's model is validated against a **persistence baseline** — the
standard approach when no independent metered dataset exists to test
against: "does the model beat naive 'today looks like yesterday'
forecasting, on the same real held-out data?" rather than reporting an MAE
number with nothing to compare it to.

Check it live: `GET /model_info` returns, per zone, `test_mae_kw`,
`persistence_baseline_mae_kw`, `skill_score_vs_persistence`, and
`n_test_samples` — no inflated claims, just what's measured.

## Reliability

- **Demo-safe weather fallback**: `/predict` never hard-fails on a network
  hiccup. It tries live Open-Meteo data first, falls back to the most
  recent successful fetch cached on disk (`weather_disk_cache.json`), and
  — if that's empty too (e.g. a fresh clone with no prior successful
  fetch) — falls back to a bundled physically-realistic fixture
  (`data/seed_weather_fallback.json`). Every response reports which tier
  was used via `data_freshness: "live" | "disk_cache" | "seed_fallback"` —
  never silently substituted.
- **Rate limiting**: `/predict` (60/min) and `/contribute` (10/min) are
  rate-limited per IP (simple in-memory sliding window — swap for
  Flask-Limiter/Redis if this ever runs multi-process) to stop trivial
  abuse or accidental spamming of the training-data pool.
- **Input validation**: `/contribute` rejects out-of-range lat/lon and
  implausible power readings (0–50 kW) before they ever touch storage.
- **Automated tests**: `pytest tests/ -v` — 23 tests covering state/zone
  routing, feature engineering, prediction bounds, battery optimization,
  nowcasting, rate limiting, and every endpoint that doesn't require live
  network access. Install with `pip install -r requirements-dev.txt`.

## Known limitations (by design, for now)

- **Inverter comparison is a demo.** There's no universal inverter API —
  every manufacturer (Huawei, Growatt, Deye, SMA, etc.) has its own. It's
  kept in the UI, clearly labeled, until a specific brand/integration is
  chosen to build against.
- **"My Data" contributions aren't retrained automatically yet.** They're
  captured to `contributions.json` (state, zone, reading, timestamp) ready
  for a periodic retraining job to consume — that job isn't wired up yet.

## Engineering decisions worth knowing about

- **XGBoost instead of LightGBM/TensorFlow**: point regression + native
  quantile regression for the 10th/90th percentile bounds, instead of
  LightGBM or an LSTM. Keeps the dependency footprint small and installation
  reliable, while still delivering genuine probabilistic forecasts.
- **Six Nigerian climate zones, not 37 separate per-state models**: Nigeria
  spans real climate variation (semi-arid Sahel north to humid coastal
  south). Grouping the 36 states + FCT into six zones (see
  `NIGERIA_STATES`/`NIGERIA_ZONES` in `train_model.py`) captures that
  variation with one real-data fetch + model per zone instead of 37.
- **Nearest-state matching is Euclidean on lat/lon** (`nearest_state()`), not
  an official boundary/shapefile lookup. It's simple and accurate enough at
  Nigeria's scale, documented rather than hidden.
- **No raster images**: per the design spec, all visuals are SVG (the app
  icon) or emoji/Font Awesome icons — nothing to optimize or replace later.
- **Contribution storage** is a flat JSON file for zero-setup persistence.
  For real multi-user production traffic, swap `_load_contributions` /
  `_save_contributions` in `app.py` for a real database.

## Requirements

- Python 3.10+
- Internet access to reach Open-Meteo (weather + geocoding) and CDN assets
  (Bootstrap, Font Awesome, Chart.js, Google Fonts) — the app itself needs
  no API keys.

## Notes

- Forecasts are estimates based on public weather data and synthetic model
  training; they are not guarantees of actual solar output.
- The inverter comparison and push-notification features are simulated /
  local-only, by design, so the app runs without any external hardware or
  push-server infrastructure.
"# solarsense-ai" 
"# solarsense-ai" 
