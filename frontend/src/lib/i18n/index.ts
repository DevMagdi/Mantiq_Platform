import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { ar } from "./ar";
import { en } from "./en";

export const SUPPORTED = ["ar", "en"] as const;
export type Lang = (typeof SUPPORTED)[number];

const STORAGE_KEY = "bh.lang";

export function initialLanguage(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "en" ? "en" : "ar";
}

export function applyDirection(lang: Lang) {
  const root = document.documentElement;
  root.lang = lang;
  root.dir = lang === "ar" ? "rtl" : "ltr";
}

export async function setupI18n() {
  const lang = initialLanguage();
  await i18n.use(initReactI18next).init({
    resources: { ar: { translation: ar }, en: { translation: en } },
    lng: lang,
    fallbackLng: "ar",
    // The dictionaries read as `+{n}%` (the wording straight from the design sheet), so the
    // interpolation delimiters are single braces — `escapeValue` stays off because React escapes.
    interpolation: { escapeValue: false, prefix: "{", suffix: "}" },
    returnNull: false,
  });
  applyDirection(lang);
  return i18n;
}

export async function changeLanguage(lang: Lang) {
  await i18n.changeLanguage(lang);
  localStorage.setItem(STORAGE_KEY, lang);
  applyDirection(lang);
}

export default i18n;
