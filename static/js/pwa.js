/**
 * pwa.js
 * Registers the service worker and handles the "install app" prompt plus
 * a local (client-side only) notification for significant forecast drops.
 * No real push server is required: we use the Notifications API directly
 * for a same-session alert, which is enough to demonstrate the UX without
 * needing external push infrastructure or credentials.
 */
let deferredInstallPrompt = null;

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    console.warn("[pwa] Service workers not supported in this browser.");
    return;
  }
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("/static/service-worker.js");
      console.log("[pwa] Service worker registered:", reg.scope);
    } catch (err) {
      console.error("[pwa] Service worker registration failed:", err);
    }
  });
}

function setupInstallPrompt(buttonEl) {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (buttonEl) buttonEl.classList.remove("hidden");
  });

  if (buttonEl) {
    buttonEl.addEventListener("click", async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      const { outcome } = await deferredInstallPrompt.userChoice;
      console.log(`[pwa] Install prompt outcome: ${outcome}`);
      deferredInstallPrompt = null;
      buttonEl.classList.add("hidden");
    });
  }

  window.addEventListener("appinstalled", () => {
    console.log("[pwa] App installed successfully.");
    if (buttonEl) buttonEl.classList.add("hidden");
  });
}

async function requestNotificationPermission() {
  if (!("Notification" in window)) {
    console.warn("[pwa] Notifications not supported in this browser.");
    return "unsupported";
  }
  try {
    const permission = await Notification.requestPermission();
    console.log("[pwa] Notification permission:", permission);
    return permission;
  } catch (err) {
    console.error("[pwa] Notification permission request failed:", err);
    return "denied";
  }
}

/** Fires a local notification when the forecast indicates a significant drop. */
function maybeNotifyForecastDrop(nowcastAlert) {
  if (!nowcastAlert || nowcastAlert.level !== "warning") return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(window.i18n.translate("app_title"), {
      body: window.i18n.translate(nowcastAlert.message_key),
      icon: "/static/icons/icon.svg",
      tag: "solarsense-nowcast",
    });
  } catch (err) {
    console.error("[pwa] Failed to show notification:", err);
  }
}

/** Caches the most recent forecast payload for offline access. */
function cacheLastForecast(payload) {
  try {
    localStorage.setItem("solarsense_last_forecast", JSON.stringify({
      payload,
      cachedAt: Date.now(),
    }));
  } catch (err) {
    console.warn("[pwa] Could not cache forecast:", err);
  }
}

function getCachedForecast() {
  try {
    const raw = localStorage.getItem("solarsense_last_forecast");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

window.solarPwa = {
  registerServiceWorker,
  setupInstallPrompt,
  requestNotificationPermission,
  maybeNotifyForecastDrop,
  cacheLastForecast,
  getCachedForecast,
};
