/**
 * i18n.js
 * Lightweight internationalization engine.
 * Loads a translation JSON file per language and applies it to any element
 * carrying a `data-i18n="key"` attribute (text content) or
 * `data-i18n-placeholder="key"` (input placeholder).
 */
const SUPPORTED_LANGS = ["en", "es", "fr", "de", "pt"];
const DEFAULT_LANG = "en";

let currentTranslations = {};
let currentLang = DEFAULT_LANG;

/** Detects a saved preference, falling back to the browser language. */
function detectInitialLang() {
  const saved = localStorage.getItem("solarsense_lang");
  if (saved && SUPPORTED_LANGS.includes(saved)) return saved;
  const browserLang = (navigator.language || "en").slice(0, 2);
  return SUPPORTED_LANGS.includes(browserLang) ? browserLang : DEFAULT_LANG;
}

/** Fetches and caches the translation dictionary for a language. */
async function loadLanguage(lang) {
  if (!SUPPORTED_LANGS.includes(lang)) lang = DEFAULT_LANG;
  try {
    const resp = await fetch(`/static/translations/${lang}.json`, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`Failed to load ${lang}.json`);
    currentTranslations = await resp.json();
    currentLang = lang;
    localStorage.setItem("solarsense_lang", lang);
    document.documentElement.setAttribute("lang", lang);
    console.log(`[i18n] Loaded language: ${lang}`);
  } catch (err) {
    console.error("[i18n] Error loading language", lang, err);
    if (lang !== DEFAULT_LANG) await loadLanguage(DEFAULT_LANG);
  }
  applyTranslations();
}

/** Returns the translated string for a key, or the key itself if missing. */
function translate(key, vars = {}) {
  let text = currentTranslations[key] ?? key;
  Object.entries(vars).forEach(([k, v]) => {
    text = text.replace(new RegExp(`{${k}}`, "g"), v);
  });
  return text;
}

/** Walks the DOM applying translations to all tagged elements. */
function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    el.textContent = translate(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    el.setAttribute("placeholder", translate(key));
  });
  document.dispatchEvent(new CustomEvent("translationsApplied"));
}

async function setLanguage(lang) {
  await loadLanguage(lang);
}

window.i18n = { translate, setLanguage, detectInitialLang, get currentLang() { return currentLang; } };
