/**
 * script.js
 * Main application logic — Nigeria edition. Handles the state/auto location
 * picker, the Dashboard/My Data tabs, wiring to the real backend API, and
 * all interactive dashboard behavior.
 */
(() => {
  "use strict";

  const state = {
    lat: 6.6018, lon: 3.3515,   // default: Ikeja, Lagos (until user sets a real location)
    hasRealLocation: false,
    lastForecast: null,
    ngStates: [],
  };

  const el = (id) => document.getElementById(id);
  const show = (node) => node && node.classList.remove("hidden");
  const hide = (node) => node && node.classList.add("hidden");

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  async function init() {
    const lang = window.i18n.detectInitialLang();
    el("langSelect").value = lang;
    await window.i18n.setLanguage(lang);

    window.solarPwa.registerServiceWorker();
    window.solarPwa.setupInstallPrompt(el("installBtn"));

    el("langSelect").addEventListener("change", (e) => window.i18n.setLanguage(e.target.value));

    el("locTabAuto").addEventListener("click", () => setLocMode("auto"));
    el("locTabManual").addEventListener("click", () => setLocMode("manual"));
    el("locateBtn").addEventListener("click", handleLocateClick);
    el("stateSelect").addEventListener("change", handleStateChange);

    el("pageTabDash").addEventListener("click", () => setPageTab("dash"));
    el("pageTabMyData").addEventListener("click", () => setPageTab("mydata"));

    el("simulateReadingBtn").addEventListener("click", handleSimulateReading);
    el("submitReadingBtn").addEventListener("click", handleSubmitReading);
    el("notifyBtn").addEventListener("click", () => window.solarPwa.requestNotificationPermission());

    el("socInput").addEventListener("input", () => { el("socDisplay").textContent = `${el("socInput").value}%`; });
    el("batteryCapacityInput").addEventListener("input", () => { el("batteryCapacityVal").textContent = el("batteryCapacityInput").value; });
    el("loadInput").addEventListener("input", () => {
      el("loadVal").textContent = el("loadInput").value;
      el("stripUsage").textContent = `${el("loadInput").value} kW`;
    });

    const refetchInputs = [
      "capacityInput", "batteryCapacityInput", "socInput", "loadInput",
    ];
    const debouncedRefetch = debounce(() => refreshForecast(state.lat, state.lon), 600);
    refetchInputs.forEach((id) => {
      el(id).addEventListener("change", debouncedRefetch);
      el(id).addEventListener("input", debouncedRefetch);
    });

    await loadStates();
    // Show the full dashboard immediately with the default Lagos forecast —
    // nothing is gated behind setting a location first.
    refreshForecast(state.lat, state.lon);
    loadContributionsSummary();
  }

  // ------------------------------------------------------------------
  // Nigeria states dropdown
  // ------------------------------------------------------------------
  async function loadStates() {
    try {
      const resp = await fetch("/states");
      state.ngStates = await resp.json();
    } catch (err) {
      console.error("[script] Failed to load /states:", err);
      state.ngStates = [];
    }
    const sel = el("stateSelect");
    sel.innerHTML = "";
    state.ngStates.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = `${s.capital}, ${s.state} State`;
      if (s.state === "Lagos") opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function handleStateChange() {
    const idx = parseInt(el("stateSelect").value, 10);
    const s = state.ngStates[idx];
    if (!s) return;
    state.lat = s.lat;
    state.lon = s.lon;
    state.hasRealLocation = true;
    hide(el("demoBanner"));
    refreshForecast(state.lat, state.lon);
  }

  // ------------------------------------------------------------------
  // Location mode tabs + geolocation
  // ------------------------------------------------------------------
  function setLocMode(mode) {
    el("locTabAuto").classList.toggle("active", mode === "auto");
    el("locTabManual").classList.toggle("active", mode === "manual");
    el("locAutoPane").classList.toggle("hidden", mode !== "auto");
    el("locManualPane").classList.toggle("hidden", mode !== "manual");
  }

  function handleLocateClick() {
    hide(el("locationDeniedMsg"));
    show(el("locateStatus"));

    if (!("geolocation" in navigator)) {
      onLocationError("Your browser doesn't support location detection. Choose your state instead.");
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        hide(el("locateStatus"));
        state.lat = pos.coords.latitude;
        state.lon = pos.coords.longitude;
        state.hasRealLocation = true;
        hide(el("demoBanner"));
        refreshForecast(state.lat, state.lon);
      },
      (err) => {
        hide(el("locateStatus"));
        console.error("[script] Geolocation failed:", err.code, err.message);
        const reasons = {
          1: "Location permission was denied. Check your browser's site settings (the padlock icon next to the address bar → Location → Allow) and your device's location settings, then try again.",
          2: "Your device couldn't determine a location right now (no GPS/Wi-Fi signal to use). Choose your state instead, or try again in a moment.",
          3: "Location detection timed out. Choose your state instead, or try again.",
        };
        onLocationError(reasons[err.code] || "Couldn't detect your location. Choose your state instead.");
      },
      // enableHighAccuracy:false is more reliable on laptops/desktops without a
      // GPS chip — it uses Wi-Fi/IP-based positioning instead of waiting on a
      // GPS fix that may never arrive.
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 }
    );
  }

  function onLocationError(message) {
    const msgEl = el("locationDeniedMsg");
    msgEl.textContent = message || window.i18n.translate("location_denied");
    show(msgEl);
    setLocMode("manual");
  }

  // ------------------------------------------------------------------
  // Page tabs (Dashboard / My Data)
  // ------------------------------------------------------------------
  function setPageTab(tab) {
    el("pageTabDash").classList.toggle("active", tab === "dash");
    el("pageTabMyData").classList.toggle("active", tab === "mydata");
    el("dashPane").classList.toggle("hidden", tab !== "dash");
    el("myDataPane").classList.toggle("hidden", tab !== "mydata");
    if (tab === "mydata") loadContributionsSummary();
  }

  // ------------------------------------------------------------------
  // Forecast fetch + render
  // ------------------------------------------------------------------
  function gatherParams(lat, lon) {
    return new URLSearchParams({
      lat, lon,
      capacity: el("capacityInput").value || 5,
      battery_capacity: el("batteryCapacityInput").value || 10,
      battery_soc: el("socInput").value || 50,
      load_kw: el("loadInput").value || 1.5,
    });
  }

  async function refreshForecast(lat, lon) {
    show(el("loadingBanner"));
    hide(el("errorBanner"));

    try {
      const params = gatherParams(lat, lon);
      const resp = await fetch(`/predict?${params.toString()}`, { signal: AbortSignal.timeout(15000) });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      state.lastForecast = data;
      window.solarPwa.cacheLastForecast(data);
      renderDashboard(data, { fromCache: false });
    } catch (err) {
      console.error("[script] Forecast fetch failed:", err);
      const cached = window.solarPwa.getCachedForecast();
      if (cached?.payload) {
        renderDashboard(cached.payload, { fromCache: true });
      } else {
        el("errorBanner").textContent = window.i18n.translate("error_generic");
        show(el("errorBanner"));
      }
    } finally {
      hide(el("loadingBanner"));
    }
  }

  function renderDashboard(data, { fromCache }) {
    if (fromCache) {
      el("errorBanner").textContent = window.i18n.translate("error_generic") + " (offline — showing last cached forecast)";
      show(el("errorBanner"));
    }

    el("locationLabelText").textContent = data.location || el("locationLabelText").textContent;
    el("cityChartLabel").textContent = data.location || "";

    if (data.current) {
      el("condGhi").textContent = `${data.current.ghi} W/m²`;
      el("condTemp").textContent = `${data.current.temp} °C`;
      el("condWind").textContent = `${data.current.wind_speed} m/s`;
      el("condCloud").textContent = `${data.current.cloud_cover_pct}%`;
      el("condClearSky").textContent = data.current.clear_sky_index;
    }
    el("condZone").textContent = (data.climate_zone || "--").replace(/_/g, " ");

    const capacity = parseFloat(el("capacityInput").value) || 5;
    const currentPower = data.forecast?.["1h"]?.power ?? 0;
    window.solarCharts.renderGauge(currentPower, capacity);
    window.solarCharts.renderForecastChart(data.forecast || {});
    window.solarCharts.renderDailyCurveChart(data.hourly_forecast || []);

    if (data.battery_plan?.length) {
      window.solarBattery.renderBatteryChart(data.battery_plan);
    }

    const dailyTotal = (data.hourly_forecast || []).reduce((sum, e) => sum + e.power, 0);
    el("stripHour").textContent = `${currentPower.toFixed(2)} kW`;
    el("stripDaily").textContent = `${dailyTotal.toFixed(1)} kWh`;
    el("stripUsage").textContent = `${el("loadInput").value} kW`;

    const nowcastEl = el("nowcastBanner");
    if (data.nowcast_alert) {
      nowcastEl.textContent = `${window.i18n.translate("nowcast_title")}: ${window.i18n.translate(data.nowcast_alert.message_key)}`;
      show(nowcastEl);
      window.solarPwa.maybeNotifyForecastDrop(data.nowcast_alert);
    } else {
      hide(nowcastEl);
    }
  }

  // ------------------------------------------------------------------
  // Inverter comparison (simulated demo — kept as-is per project decision)
  // ------------------------------------------------------------------
  async function handleSimulateReading() {
    const apiKey = el("inverterKeyInput").value.trim();
    if (!apiKey) {
      el("inverterKeyInput").focus();
      return;
    }
    const forecastPower = state.lastForecast?.forecast?.["1h"]?.power ?? 0;
    try {
      const params = new URLSearchParams({ forecast_power: forecastPower, api_key: apiKey });
      const resp = await fetch(`/inverter_sim?${params.toString()}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "inverter_sim failed");
      el("measuredPowerValue").textContent = data.measured_power.toFixed(3);
      el("deviationValue").textContent = data.deviation_pct;
      show(el("inverterResult"));
    } catch (err) {
      console.error("[script] Inverter comparison failed:", err);
    }
  }

  // ------------------------------------------------------------------
  // My Data: contribute a reading + view aggregate stats
  // ------------------------------------------------------------------
  async function handleSubmitReading() {
    const actual = parseFloat(el("actualPowerInput").value);
    if (Number.isNaN(actual)) {
      el("actualPowerInput").focus();
      return;
    }
    try {
      const resp = await fetch("/contribute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: state.lat, lon: state.lon, actual_power: actual }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "contribute failed");

      el("contributeResult").textContent = data.note || "Saved — thank you!";
      show(el("contributeResult"));
      el("actualPowerInput").value = "";
      loadContributionsSummary();
    } catch (err) {
      console.error("[script] Contribute failed:", err);
    }
  }

  async function loadContributionsSummary() {
    try {
      const resp = await fetch("/contributions/summary");
      const data = await resp.json();
      el("totalContributions").textContent = data.total ?? 0;
      const myState = state.ngStates[parseInt(el("stateSelect").value || 0, 10)]?.state;
      const stateCount = (myState && data.by_state?.[myState]) || 0;
      el("stateContributions").textContent = stateCount;
    } catch (err) {
      console.error("[script] Failed to load contributions summary:", err);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
