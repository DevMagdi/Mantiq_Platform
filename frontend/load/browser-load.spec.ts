import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

/**
 * R8 · S3 — concurrent browser users, as real Chromium contexts, on the real bundle.
 *
 * This is not a screenshot suite and it is not a loop of `page.goto`. Each virtual user is its own
 * `browser.newContext()` (its own cookie jar, its own storage, its own session) and repeats the flow its
 * role actually performs: a manager checks the dashboard, filters the kanban by rep, reads the inbox and the
 * sales team; a rep works their board; service desk works the queue; a portal client looks at its own file.
 *
 * Every failure that counts is collected, not thrown on the first one:
 *   - `pageerror` (an uncaught exception in the app),
 *   - any response with status ≥ 500 from the API,
 *   - a flow step that times out or lands on an error state.
 * The pass condition (R8 §6.4) is ≥ 95 % of flows completed. The JSON this writes is the evidence, and it is
 * what the load report quotes — nothing is typed by hand.
 *
 * Runs under `playwright.load.config.ts` (its own config, so the regular e2e job does not pay for it) in CI,
 * where Chromium and PostgreSQL exist. Locally in the R8 sandbox the browser download is blocked
 * (`cdn.playwright.dev` is unreachable), which the report states instead of guessing at numbers.
 */

const BASE = process.env.LOAD_WEB_URL ?? "http://127.0.0.1:5173";
const MINUTES = Number(process.env.LOAD_MINUTES ?? "10");
const OUT = process.env.LOAD_OUT ?? "/tmp/r8-browser-load.json";
const PASSWORD = process.env.LOAD_PASSWORD ?? "Br0kerHub!2026";
const REPS = Number(process.env.LOAD_REP_COUNT ?? "10");

type Role = "manager" | "rep" | "cs" | "portal";

const PLAN: { role: Role; count: number }[] = [
  { role: "manager", count: 5 },
  { role: "rep", count: 10 },
  { role: "cs", count: 5 },
  { role: "portal", count: 5 },
];

type StepStat = { role: Role; step: string; ms: number; ok: boolean; detail?: string };

type UserResult = {
  role: Role;
  email: string;
  flows: number;
  flows_ok: number;
  steps: StepStat[];
  pageerrors: string[];
  server5xx: string[];
};

function emailFor(role: Role, index: number): string {
  if (role === "manager") return "manager@brokerhub.test";
  if (role === "cs") return "cs@brokerhub.test";
  if (role === "portal") return "owner.mantiq@company.test";
  return `load.rep${String(index % REPS).padStart(2, "0")}@brokerhub.test`;
}

async function signIn(page: Page, email: string): Promise<void> {
  // the portal owner shares the staff login form: the API returns a portal-scoped session for that account
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel(/البريد الإلكتروني أو اسم المستخدم/).first().fill(email);
  await page.getByLabel(/كلمة المرور|Password/i).first().fill(PASSWORD);
  await page.getByRole("button", { name: "تسجيل الدخول" }).first().click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 45_000 });
}

/** One navigation, timed and judged: the assertion is that the screen rendered, not that HTML came back. */
/**
 * One screen of one flow.
 *
 * A person moves between screens by clicking the sidebar, not by reloading the whole bundle every two
 * seconds; the first version of this spec used `page.goto` for every step, which throws the in-memory access
 * token away each time and makes every step call `POST /auth/refresh` — with several contexts signed in as
 * the same seeded account (5 managers share one, 5 portal clients share one) that is a refresh-token
 * collision, not a load measurement. Clicking the real `href` keeps the session like a human does, and
 * `page.goto` stays as the fallback for screens with no sidebar entry (`/leads/new`, the portal).
 */
async function step(page: Page, role: Role, name: string, pathname: string, marker: RegExp): Promise<StepStat> {
  const started = Date.now();
  try {
    const link = page.locator(`nav[aria-label] a[href="${pathname}"]`).first();
    const inApp = (await link.count()) > 0 && (await link.isVisible().catch(() => false));
    if (inApp) {
      await link.click({ timeout: 15_000 });
    } else {
      await page.goto(`${BASE}${pathname}`, { waitUntil: "domcontentloaded", timeout: 45_000 });
    }
    // scoped to `<main>`: the sidebar prints the same words as the screen title, so an unscoped text match
    // passes even when the screen itself never rendered. `<main>` is the content region in both shells
    // (`app/StaffShell.tsx` and `features/portal/PortalPage.tsx`), and it does not assume an `<h1>` — the
    // inbox titles itself with an `<h2>`, which an `h1` assertion read as "screen missing".
    await expect(page.locator("main").getByText(marker).first()).toBeVisible({ timeout: 20_000 });
    return { role, step: name, ms: Date.now() - started, ok: true };
  } catch (error) {
    return { role, step: name, ms: Date.now() - started, ok: false, detail: (error as Error).message.split("\n")[0] };
  }
}

async function runUser(browser: Browser, role: Role, index: number, stopAt: number): Promise<UserResult> {
  const email = emailFor(role, index);
  const context: BrowserContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "ar-EG" });
  const page = await context.newPage();
  const result: UserResult = { role, email, flows: 0, flows_ok: 0, steps: [], pageerrors: [], server5xx: [] };

  page.on("pageerror", (error) => result.pageerrors.push(String(error).slice(0, 200)));
  page.on("response", (response) => {
    if (response.status() >= 500) result.server5xx.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });

  try {
    await signIn(page, email);
    while (Date.now() < stopAt) {
      result.flows += 1;
      const steps: StepStat[] =
        role === "manager"
          ? [
              // the marker is the screen's own title, taken from `src/lib/i18n/ar.ts` — not a guess
              await step(page, role, "dashboard", "/dashboard", /لوحة التحكم/),
              await step(page, role, "kanban", "/leads", /ليدز/),
              await step(page, role, "inbox", "/inbox", /صندوق الطلبات/),
              await step(page, role, "sales-team", "/sales-team", /فريق المبيعات/),
            ]
          : role === "rep"
            ? [
                await step(page, role, "kanban", "/leads", /ليدز/),
                await step(page, role, "new-lead", "/leads/new", /ليد جديد/),
              ]
            : role === "cs"
              ? [
                  await step(page, role, "inbox", "/inbox", /صندوق الطلبات/),
                  await step(page, role, "dashboard", "/dashboard", /لوحة التحكم/),
                ]
              : [
                  // the portal greets the client by name and lists their request/policy tabs
                  await step(page, role, "portal", "/portal", /أهلاً بك|وثائقي|الأقساط/),
                ];
      result.steps.push(...steps);
      if (steps.every((s) => s.ok)) result.flows_ok += 1;
    }
  } catch (error) {
    result.pageerrors.push(`fatal: ${(error as Error).message.split("\n")[0]}`);
  } finally {
    await context.close().catch(() => undefined);
  }
  return result;
}

function percentile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const index = Math.min(ordered.length - 1, Math.max(0, Math.round(q * (ordered.length - 1))));
  return Math.round(ordered[index] ?? 0);
}

test.describe("R8 · browser load", () => {
  test("25 concurrent users keep their flows alive for the whole window", async ({ browser }) => {
    test.setTimeout((MINUTES + 5) * 60_000);
    const stopAt = Date.now() + MINUTES * 60_000;

    const users: Promise<UserResult>[] = [];
    for (const { role, count } of PLAN) {
      for (let i = 0; i < count; i += 1) users.push(runUser(browser, role, i, stopAt));
    }
    const results = await Promise.all(users);

    const steps = results.flatMap((r) => r.steps);
    const ok = steps.filter((s) => s.ok);
    const flows = results.reduce((sum, r) => sum + r.flows, 0);
    const flowsOk = results.reduce((sum, r) => sum + r.flows_ok, 0);
    const byStep: Record<string, { n: number; p50: number | null; p95: number | null; failed: number }> = {};
    for (const name of new Set(steps.map((s) => s.step))) {
      const mine = steps.filter((s) => s.step === name);
      byStep[name] = {
        n: mine.length,
        p50: percentile(mine.map((s) => s.ms), 0.5),
        p95: percentile(mine.map((s) => s.ms), 0.95),
        failed: mine.filter((s) => !s.ok).length,
      };
    }

    const report = {
      scenario: "S3 concurrent browser users",
      web_url: BASE,
      minutes: MINUTES,
      users: results.length,
      users_by_role: Object.fromEntries(PLAN.map((p) => [p.role, p.count])),
      flows,
      flows_ok: flowsOk,
      flows_ok_pct: flows ? Math.round((flowsOk / flows) * 1000) / 10 : 0,
      steps: steps.length,
      steps_failed: steps.length - ok.length,
      steps_failed_pct: steps.length ? Math.round(((steps.length - ok.length) / steps.length) * 1000) / 10 : 0,
      step_p50_ms: percentile(ok.map((s) => s.ms), 0.5),
      step_p95_ms: percentile(ok.map((s) => s.ms), 0.95),
      step_p99_ms: percentile(ok.map((s) => s.ms), 0.99),
      by_step: byStep,
      pageerrors: results.flatMap((r) => r.pageerrors).slice(0, 20),
      pageerror_count: results.reduce((sum, r) => sum + r.pageerrors.length, 0),
      server_5xx: results.flatMap((r) => r.server5xx).slice(0, 20),
      server_5xx_count: results.reduce((sum, r) => sum + r.server5xx.length, 0),
      per_user: results.map((r) => ({ role: r.role, email: r.email, flows: r.flows, flows_ok: r.flows_ok })),
    };

    mkdirSync(path.dirname(OUT), { recursive: true });
    writeFileSync(OUT, JSON.stringify(report, null, 2), "utf8");
    // the same numbers in the job log, so a reader without artifact access still sees them
    // eslint-disable-next-line no-console -- this is the load report's own output channel, not app logging
    console.log(JSON.stringify({ ...report, per_user: undefined, by_step: undefined }, null, 2));

    expect(report.users).toBe(25);
    expect(report.flows_ok_pct).toBeGreaterThanOrEqual(95);
    expect(report.pageerror_count).toBe(0);
    expect(report.server_5xx_count).toBe(0);
  });
});
