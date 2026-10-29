/** Tiny wrapper so non-hook modules (table column defs, states) can translate too. */
import i18n from "i18next";

export function t(key: string, options?: Record<string, unknown>): string {
  return String(i18n.t(key, options ?? {}));
}

export function isRTL(): boolean {
  return document.documentElement.dir !== "ltr";
}
