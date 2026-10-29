/**
 * One warm-up page load before the suite starts.
 *
 * Without it the first tests of the desktop project absorb Vite's cold start (dependency
 * optimization + first transform of the entry graph) and fail on 8–20s expectations even though the
 * very same specs pass seconds later. Warm-up also proves the SPA really serves /login, so a dead
 * front end fails here with one clear message instead of as a dozen unrelated timeouts.
 *
 * `E2E_SKIP_WARMUP=1` disables it (only useful when you point the suite at a deployment you warmed
 * yourself).
 */
import { chromium } from "@playwright/test";

export default async function globalSetup(): Promise<void> {
  if (process.env.E2E_SKIP_WARMUP === "1") return;
  const usePreview = process.env.E2E_USE_PREVIEW === "1";
  const port = usePreview ? (process.env.E2E_WEB_PREVIEW_PORT ?? "5173") : (process.env.E2E_WEB_PORT ?? "5173");
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/login`, { waitUntil: "networkidle", timeout: 60_000 });
    await page.getByLabel(/البريد الإلكتروني|Email/).waitFor({ timeout: 30_000 });
  } finally {
    await browser.close();
  }
}
