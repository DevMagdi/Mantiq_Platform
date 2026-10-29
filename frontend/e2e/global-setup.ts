/**
 * One warm-up pass before the suite starts.
 *
 * Without it the first tests absorb Vite's cold start (dependency optimization + first transform of
 * the entry graph) and fail on 8–20s expectations even though the very same specs pass seconds
 * later. Warm-up also proves the SPA really serves /login, so a dead front end fails here with one
 * clear message instead of as a dozen unrelated timeouts.
 *
 * Vite dev compiles a route's chunk on its first visit, so warming `/login` alone still left
 * `/settings` cold: `settings.spec.ts` hit its 8s heading expectation while the chunk was being
 * built. The pass therefore signs in with the seeded admin and opens each screen once. It is
 * skipped for the built preview, where the chunks already exist.
 *
 * `E2E_SKIP_WARMUP=1` disables it (only useful when you point the suite at a deployment you warmed
 * yourself); `E2E_WARM_ROUTES=0` keeps the login check but skips the authed screens.
 */
import { chromium } from "@playwright/test";

import { ADMIN, LOGIN_BUTTON, PASSWORD } from "./fixtures";

const SCREENS = ["/dashboard", "/inbox", "/leads", "/clients", "/policies", "/reports", "/settings"];

export default async function globalSetup(): Promise<void> {
  if (process.env.E2E_SKIP_WARMUP === "1") return;
  const usePreview = process.env.E2E_USE_PREVIEW === "1";
  const port = usePreview ? (process.env.E2E_WEB_PREVIEW_PORT ?? "5173") : (process.env.E2E_WEB_PORT ?? "5173");
  const origin = `http://127.0.0.1:${port}`;
  // Same escape hatch the projects use for a Chromium Playwright did not download — without it this
  // pass cannot start in a sandbox whose egress blocks cdn.playwright.dev (see playwright.config.ts).
  const executablePath = process.env.E2E_CHROMIUM_PATH;
  const browser = await chromium.launch(executablePath ? { executablePath } : {});
  try {
    const page = await browser.newPage();
    await page.goto(`${origin}/login`, { waitUntil: "networkidle", timeout: 60_000 });
    await page.getByLabel(/البريد الإلكتروني|Email/).waitFor({ timeout: 30_000 });

    if (usePreview || process.env.E2E_WARM_ROUTES === "0") return;
    // The seeded admin sees every screen; if this database was never seeded, warning and carrying on
    // is better than failing the whole suite in setup.
    try {
      await page.getByLabel(/البريد الإلكتروني|Email/).fill(ADMIN);
      await page.getByLabel(/كلمة المرور|Password/).fill(PASSWORD);
      await page.getByRole("button", LOGIN_BUTTON).click();
      await page.waitForURL(/\/(dashboard|portal|inbox|leads)/, { timeout: 30_000 });
      for (const screen of SCREENS) {
        await page.goto(`${origin}${screen}`, { waitUntil: "networkidle", timeout: 60_000 });
      }
    } catch (error) {
      console.warn(`[global-setup] route warm-up skipped: ${(error as Error).message}`);
    }
  } finally {
    await browser.close();
  }
}
