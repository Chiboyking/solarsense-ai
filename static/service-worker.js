/**
 * service-worker.js
 * Caches the app shell (HTML/CSS/JS/translations/icons) so SolarSense AI
 * launches and shows the last known forecast even when offline. Network
 * responses for /predict are cached opportunistically so the most recent
 * forecast is available offline too (the UI reads it from localStorage,
 * see pwa.js -> getCachedForecast, as the primary offline path).
 */
const CACHE_NAME = "solarsense-cache-v1";
const APP_SHELL = [
  "/",
  "/static/css/style.css",
  "/static/js/i18n.js",
  "/static/js/charts.js",
  "/static/js/battery.js",
  "/static/js/pwa.js",
  "/static/js/script.js",
  "/static/manifest.json",
  "/static/icons/icon.svg",
  "/static/translations/en.json",
  "/static/translations/es.json",
  "/static/translations/fr.json",
  "/static/translations/de.json",
  "/static/translations/pt.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch((err) => {
      console.warn("[sw] Precache failed for some assets:", err);
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Network-first for the live prediction API, falling back to cache.
  if (url.pathname === "/predict") {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Cache-first for the app shell and static assets.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
