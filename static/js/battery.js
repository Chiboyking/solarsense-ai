/**
 * battery.js
 * Renders the battery dispatch chart (solar generation, state of charge,
 * grid import/export) from the schedule returned by the backend's
 * /predict endpoint (see app.py -> optimize_battery for the algorithm).
 *
 * A lightweight client-side re-estimator is also provided so the UI can
 * give instant feedback when the user drags the SoC slider, without
 * waiting for a full server round-trip.
 */
let batteryChart = null;

function renderBatteryChart(schedule) {
  const canvas = document.getElementById("batteryChart");
  const ctx = canvas.getContext("2d");
  const labels = schedule.map((e) => `${e.hour}:00`);

  const datasets = [
    {
      type: "bar",
      label: window.i18n.translate("solar_generation"),
      data: schedule.map((e) => e.generation_kw),
      backgroundColor: "rgba(0, 210, 255, 0.55)",
      yAxisID: "y",
      order: 2,
    },
    {
      type: "line",
      label: window.i18n.translate("soc"),
      data: schedule.map((e) => e.soc_pct),
      borderColor: "#8e6bff",
      backgroundColor: "#8e6bff",
      yAxisID: "y1",
      tension: 0.3,
      pointRadius: 2,
      order: 1,
    },
    {
      type: "bar",
      label: window.i18n.translate("grid_import"),
      data: schedule.map((e) => -e.grid_import_kw),
      backgroundColor: "rgba(255, 99, 132, 0.55)",
      yAxisID: "y",
      order: 2,
    },
    {
      type: "bar",
      label: window.i18n.translate("grid_export"),
      data: schedule.map((e) => e.grid_export_kw),
      backgroundColor: "rgba(58, 123, 213, 0.55)",
      yAxisID: "y",
      order: 2,
    },
  ];

  if (batteryChart) {
    batteryChart.data.labels = labels;
    batteryChart.data.datasets = datasets;
    batteryChart.update();
    return;
  }

  batteryChart = new Chart(ctx, {
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: {
          stacked: true,
          title: { display: true, text: "kW" },
          grid: { color: "rgba(255,255,255,0.08)" },
        },
        y1: {
          position: "right",
          min: 0,
          max: 100,
          title: { display: true, text: "SoC %" },
          grid: { display: false },
        },
      },
    },
  });
}

/**
 * Fast client-side heuristic used only for instant slider feedback.
 * Mirrors the server's greedy dispatch logic in simplified form.
 */
function quickBatteryEstimate(forecastPowers, capacityKwh, socPct, efficiency, loadKw) {
  let soc = capacityKwh * (socPct / 100);
  return forecastPowers.map((gen) => {
    const net = gen - loadKw;
    if (net >= 0) {
      const room = (capacityKwh - soc) / efficiency;
      const charge = Math.min(net, room);
      soc += charge * efficiency;
    } else {
      const deficit = -net;
      const avail = soc * efficiency;
      const discharge = Math.min(deficit, avail);
      soc -= discharge / efficiency;
    }
    soc = Math.max(0, Math.min(capacityKwh, soc));
    return Math.round((soc / capacityKwh) * 1000) / 10;
  });
}

window.solarBattery = { renderBatteryChart, quickBatteryEstimate };
