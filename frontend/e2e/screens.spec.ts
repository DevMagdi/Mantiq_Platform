import { expect, test, type Page } from "@playwright/test";

import { ar } from "../src/lib/i18n/ar";

import { ADMIN, apiLogin, login, MANAGER, OWNER } from "./fixtures";

/**
 * Every screen of spec §9 must render its own heading and survive a reload without an uncaught
 * exception. This is the cheap suite that catches a broken import or a wrong field name on a screen
 * nobody clicked in a feature test.
 */
type Screen = { route: string; heading: RegExp; who?: "manager" | "rep" | "owner" | "admin"; phase2?: boolean };

/**
 * A screen's heading has to be *readable*, and the app keeps hidden twins of some phrases for other
 * breakpoints (the login brand panel, the shell's breadcrumb). `.first()` used to land on such a twin and
 * report a perfectly rendered screen as missing, so the grid asks for a visible match instead.
 */
async function expectHeading(page: Page, pattern: RegExp): Promise<void> {
  await expect(page.getByText(pattern).filter({ visible: true }).first()).toBeVisible({ timeout: 20_000 });
}

const SCREENS: Screen[] = [
  // /login's visible title at every width is the form's own «أهلاً بعودتك»; the brand block lives in the
  // desktop hero panel (`hidden` on a phone), which is what made this row fail only on mobile
  { route: "/login", heading: new RegExp(ar.auth.welcome), who: "rep" },
  { route: "/dashboard", heading: /لوحة التحكم/ },
  { route: "/inbox", heading: /صندوق الطلبات الموحد/ },
  // screen 3 titles itself after the rep who owns it («ليدز سارة»), and the phone board says «ليدزي»
  { route: "/leads", heading: /ليدز|ليدزي/ },
  { route: "/leads/new", heading: /ليد جديد/ },
  { route: "/sales-team", heading: /فريق المبيعات|حِمل/ },
  { route: "/clients", heading: /العملاء/ },
  // the users screen lives inside the settings hub now, and the tab needs `users.manage` — which the
  // sales-manager role does not hold, so asserting it as `manager` was the wrong premise (ADR-015/016).
  { route: "/users", heading: /المستخدمون والصلاحيات/, who: "admin" },
  { route: "/settings", heading: /الإعدادات/, who: "manager" },
  { route: "/policies", heading: /الوثائق والأقساط/, phase2: true },
  { route: "/issuing", heading: /حالات الإصدار/, phase2: true },
  { route: "/reports", heading: /التقارير/, phase2: true },
  { route: "/portal", heading: /بوابة العميل/, who: "owner" },
];

async function open(page: Page, screen: Screen) {
  if (screen.route === "/login") {
    await page.goto("/login");
    return;
  }
  await login(page, screen.who === "owner" ? OWNER : screen.who === "admin" ? ADMIN : MANAGER);
  await page.goto(screen.route);
}

test.describe("screen grid (spec §9)", () => {
  test("the Phase-2 screens stay gated until the flag is on", async ({ page, request }) => {
    const auth = await apiLogin(request, "admin@brokerhub.test");
    const before = await request.patch("/api/v1/feature-flags/phase2.enabled", { headers: auth, data: { enabled: false } });
    expect(before.status(), await before.text()).toBe(200);

    await login(page, MANAGER);
    await page.goto("/policies");
    await expect(page.getByText(/غير مفعّلة بعد/)).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("table tbody tr")).toHaveCount(0);

    const after = await request.patch("/api/v1/feature-flags/phase2.enabled", { headers: auth, data: { enabled: true } });
    expect(after.status()).toBe(200);
    await page.reload();
    await expect(page.getByRole("heading", { name: /الوثائق والأقساط/ })).toBeVisible({ timeout: 20_000 });
  });

  for (const screen of SCREENS) {
    test(`${screen.route} renders without an uncaught error`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await open(page, screen);
      await expectHeading(page, screen.heading);
      await expect.poll(() => errors, { timeout: 5_000 }).toHaveLength(0);
    });
  }

  test("the client file, insured roster and lead detail open by id", async ({ page, request }) => {
    const auth = await apiLogin(request, MANAGER);
    const client = (await (await request.get("/api/v1/clients?page_size=1", { headers: auth })).json()).items[0] as { id: string; name: string };
    const clientId = client.id;
    const leadId = (await (await request.get("/api/v1/leads?page_size=1", { headers: auth })).json()).items[0].id as string;

    await login(page, MANAGER);
    // the file screen puts the client's name in the H1 and names its tab group «ملف العميل» for assistive
    // tech, so that accessible name — not a decorative heading — is what proves the screen opened
    await page.goto(`/clients/${clientId}`);
    await expect(page.getByRole("tablist", { name: ar.clients.file.title })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(client.name).first()).toBeVisible();
    await expect(page.getByText(ar.clients.file.contract)).toBeVisible();

    await page.goto(`/clients/${clientId}/insured`);
    await expect(page.getByRole("heading", { name: /الموظفون المؤمَّن عليهم/ })).toBeVisible();

    await page.goto(`/leads/${leadId}`);
    await expect(page.getByText(/تفاصيل الـLead|مسار البيع|جديد/).first()).toBeVisible({ timeout: 20_000 });
  });
});
