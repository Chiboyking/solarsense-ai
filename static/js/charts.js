/**
 * charts.js
 * Chart.js setup for: the multi-horizon forecast line chart (with 80%
 * confidence band), the semi-circular "current power" gauge, and helpers
 * shared with battery.js.
 */
const CHART_COLORS = {
  cyan: "#00d2ff",
  blue: "#3a7bd5",
  purple: "#8e6bff",
  bandFill: "rgba(0, 210, 255, 0.15)",
  grid: "rgba(255, 255, 255, 0.08)",
  text: "rgba(255, 255, 255, 0.75)",
};

let forecastChart = null;
let gaugeChart = null;

Chart.defaults.color = CHART_COLORS.text;
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";

/** Renders/updates the multi-horizon forecast line chart with bounds band. */
function renderForecastChart(forecast) {
  const horizonOrder = ["1h", "3h", "6h", "12h", "24h"];
  const labels = horizonOrder.map((h) => window.i18n.translate(`horizon_${h}`));
  const points = horizonOrder.map((h) => forecast[h]?.power ?? null);
  const lower = horizonOrder.map((h) => forecast[h]?.lower ?? null);
  const upper = horizonOrder.map((h) => forecast[h]?.upper ?? null);

  const ctx = document.getElementById("forecastChart").getContext("2d");

  const datasets = [
    {
      label: window.i18n.translate("predicted_power"),
      data: points,
      borderColor: CHART_COLORS.cyan,
      backgroundColor: CHART_COLORS.cyan,
      borderWidth: 3,
      tension: 0.35,
      pointRadius: 5,
      pointBackgroundColor: CHART_COLORS.cyan,
      fill: false,
      order: 1,
    },
    {
      label: window.i18n.translate("confidence_range"),
      data: upper,
      borderColor: "transparent",
      backgroundColor: CHART_COLORS.bandFill,
      pointRadius: 0,
      fill: "+1",
      order: 3,
      tension: 0.35,
    },
    {
      label: "lower_bound_hidden",
      data: lower,
      borderColor: "transparent",
      backgroundColor: CHART_COLORS.bandFill,
      pointRadius: 0,
      fill: false,
      order: 3,
      tension: 0.35,
    },
  ];

  if (forecastChart) {
    forecastChart.data.labels = labels;
    forecastChart.data.datasets = datasets;
    forecastChart.update();
    return;
  }

  forecastChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: {
            filter: (item) => item.text !== "lower_bound_hidden",
          },
        },
        tooltip: {
          filter: (item) => item.dataset.label !== "lower_bound_hidden",
        },
      },
      scales: {
        x: { grid: { color: CHART_COLORS.grid } },
        y: {
          beginAtZero: true,
          title: { display: true, text: "kW" },
          grid: { color: CHART_COLORS.grid },
        },
      },
    },
  });
}

/** Renders a semi-circular gauge showing current predicted power vs capacity. */
function renderGauge(currentPowerKw, capacityKw) {
  const canvas = document.getElementById("gaugeChart");
  const ctx = canvas.getContext("2d");
  const pct = capacityKw > 0 ? Math.min(1, currentPowerKw / capacityKw) : 0;

  const data = {
    datasets: [{
      data: [pct, 1 - pct],
      backgroundColor: [CHART_COLORS.cyan, "rgba(255,255,255,0.08)"],
      borderWidth: 0,
      circumference: 180,
      rotation: 270,
      cutout: "75%",
    }],
  };

  if (gaugeChart) {
    gaugeChart.data = data;
    gaugeChart.update();
  } else {
    gaugeChart = new Chart(ctx, {
      type: "doughnut",
      data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }

  document.getElementById("gaugeValue").textContent = `${currentPowerKw.toFixed(2)} kW`;
}

/** Renders the "next 24 hours" area chart from the real hourly forecast series. */
let dailyCurveChart = null;
function renderDailyCurveChart(hourlyForecast) {
  const canvas = document.getElementById("dailyCurveChart");
  if (!canvas || !hourlyForecast?.length) return;
  const ctx = canvas.getContext("2d");
  const labels = hourlyForecast.map((e) => `${e.hour}:00`);
  const values = hourlyForecast.map((e) => e.power);

  const data = {
    labels,
    datasets: [{
      label: window.i18n.translate("predicted_power"),
      data: values,
      borderColor: "#ffb454",
      backgroundColor: "rgba(255, 180, 84, 0.18)",
      fill: true,
      tension: 0.4,
      pointRadius: 0,
    }],
  };

  if (dailyCurveChart) {
    dailyCurveChart.data = data;
    dailyCurveChart.update();
    return;
  }

  dailyCurveChart = new Chart(ctx, {
    type: "line",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, title: { display: true, text: "kW" }, grid: { color: CHART_COLORS.grid } },
        x: { grid: { display: false } },
      },
    },
  });
}

window.solarCharts = { renderForecastChart, renderGauge, renderDailyCurveChart, CHART_COLORS };
