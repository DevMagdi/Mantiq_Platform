/**
 * Brand evidence capture (R13 / ADR-034 §verification 3).
 *
 * Captures the five screens the brief names — login, dashboard, inbox (sidebar active), the ERP hub and
 * the client portal — at 1440×900 into `docs/ui-parity/mantiq-brand/`, plus a mobile login.
 *
 * Why a plain script instead of a Playwright test: `e2e/screenshots.spec.ts` already owns the *design-sheet*
 * capture set and asserts parity copy. This one has exactly one job — proof of the new brand — and it must
 * also run where Playwright's browser bundle is not installed. So it drives Playwright's API but accepts a
 * CDP endpoint:
 *
 *   # normal (Playwright's own Chromium, as in CI)
 *   node scripts/capture-brand-screens.mjs
 *
 *   # a browser you started yourself, e.g. a headless shell in a sandbox
 *   CDP_URL=http://127.0.0.1:9222 node scripts/capture-brand-screens.mjs
 *
 * The web app and API must already be running (`npm run dev` + the FastAPI app with the demo seed);
 * the script does not boot them, so it can never silently photograph an empty database.
 */
import { mkdirSync } from "node:fs";
import path from "node:path";

import { chromium } from "playwright-core";

const BASE = process.env.BRAND_BASE_URL ?? "http://127.0.0.1:5173";
const CDP_URL = process.env.CDP_URL;
const OUT = path.resolve(import.meta.dirname, "../../docs/ui-parity/mantiq-brand");

// Demo credentials from `python -m app.seed`; override for a deployment that set `SEED_PASSWORD`.
const PASSWORD = process.env.BRAND_PASSWORD ?? "Mantiq!2026";
const ACCOUNTS = { manager: "manager@mantiq.test", owner: "owner.mantiq@company.test" };

const VIEWPORT = { width: 1440, height: 900 };

/** @type {{file: string, route: string, who: "manager"|"owner"|"anon", ready: RegExp, mobile?: boolean}[]} */
const SHOTS = [
  { file: "01-login.png", route: "/login", who: "anon", ready: /أهلاً بعودتك|تسجيل الدخول/ },
  { file: "02-dashboard.png", route: "/dashboard", who: "manager", ready: /لوحة التحكم/ },
  { file: "03-inbox.png", route: "/inbox", who: "manager", ready: /صندوق الطلبات الموحد/ },
  { file: "04-erp-hub.png", route: "/erp", who: "manager", ready: /ERP|المحاسبي/ },
  { file: "05-portal.png", route: "/portal", who: "owner", ready: /بوابة العميل/ },
  { file: "06-login-mobile.png", route: "/login", who: "anon", ready: /أهلاً بعودتك|تسجيل الدخول/, mobile: true },
];

async function signIn(page, email) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel(/البريد الإلكتروني/).fill(email);
  await page.getByLabel(/كلمة المرور/).fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول", exact: true }).click();
  await page.waitForURL(/\/(dashboard|portal|inbox)/, { timeout: 30_000 });
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = CDP_URL ? await chromium.connectOverCDP(CDP_URL) : await chromium.launch();
  const errors = [];

  // one context per identity: a shared context leaked a refresh cookie across the sign-out boundary and
  // logged 403s that had nothing to do with the brand (noise that hides a real failure later)
  const contexts = new Map();
  const contextFor = async (who) => {
    if (contexts.has(who)) return contexts.get(who);
    const context = await browser.newContext({ viewport: VIEWPORT, locale: "ar-EG", timezoneId: "Africa/Cairo" });
    const page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`${who}: ${message.text()}`);
    });
    if (who !== "anon") await signIn(page, ACCOUNTS[who]);
    contexts.set(who, { context, page });
    return contexts.get(who);
  };

  for (const shot of SHOTS) {
    const { page } = await contextFor(shot.who);
    await page.setViewportSize(shot.mobile ? { width: 390, height: 844 } : VIEWPORT);
    await page.goto(`${BASE}${shot.route}`, { waitUntil: "domcontentloaded" });
    await page.getByText(shot.ready).first().waitFor({ state: "visible", timeout: 30_000 });
    // let the webfont settle so the Arabic is shaped with IBM Plex, not a fallback
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, shot.file) });
    const title = await page.title();
    const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const sidebar = await page.evaluate(() => {
      const aside = document.querySelector("aside");
      return aside ? getComputedStyle(aside).backgroundColor : null;
    });
    console.log(`${shot.file}  ${shot.route}  title="${title}"  body=${background}  sidebar=${sidebar ?? "-"}`);
  }

  await browser.close();
  if (errors.length) {
    console.log(`\n${errors.length} console error(s) during capture:`);
    for (const error of errors.slice(0, 10)) console.log(`  - ${error}`);
    process.exitCode = 1;
  }
}

await main();
