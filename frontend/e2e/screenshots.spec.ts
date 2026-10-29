import { mkdirSync, readdirSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { ADMIN, apiLogin, login, MANAGER, OWNER } from "./fixtures";

/**
 * Design-review evidence (spec §10): one capture per screen of §9, at 1440×900 — except screen 4,
 * which the spec fixes at 390×844 — written under the fixed names `01-dashboard.png` … `16-login.png`,
 * plus `15b-settings-hub.png` for the hub that now carries screen 15 (ADR-016), and a contact sheet the
 * owner can scan in one image. Files land in docs/screenshots/ and are
 * uploaded as a CI artifact. Nothing here asserts pixel values: it captures them, and
 * docs/ui-parity/ measures them (see docs/ui-parity/screens.md for the deltas that stay open).
 */
const OUT = path.resolve(import.meta.dirname, "../../docs/screenshots");

type Shot = {
  /** Fixed file name, `NN-slug.png`, matching the screen order of spec §9. */
  file: string;
  route: string;
  who: "manager" | "owner" | "anon" | "admin";
  ready: RegExp;
  viewport: "desktop" | "mobile";
  fullPage?: boolean;
};

/** `{clientId}` / `{leadId}` are resolved in beforeAll from the seeded data — no invented ids. */
const SHOTS: Shot[] = [
  { file: "01-dashboard.png", route: "/dashboard", who: "manager", ready: /لوحة التحكم/, viewport: "desktop" },
  { file: "02-inbox.png", route: "/inbox", who: "manager", ready: /صندوق الطلبات الموحد/, viewport: "desktop", fullPage: false },
  { file: "03-leads-kanban.png", route: "/leads", who: "manager", ready: /جديد|الـLeads/, viewport: "desktop", fullPage: false },
  { file: "04-mobile-leads.png", route: "/mobile/leads", who: "manager", ready: /ليدزي|Leads/, viewport: "mobile" },
  { file: "05-client-file.png", route: "/clients/{clientId}", who: "manager", ready: /عميل منذ|نظرة عامة/, viewport: "desktop" },
  { file: "06-portal.png", route: "/portal", who: "owner", ready: /بوابة العميل/, viewport: "desktop" },
  { file: "07-sales-team.png", route: "/sales-team", who: "manager", ready: /فريق المبيعات|حِمل/, viewport: "desktop" },
  { file: "08-clients.png", route: "/clients", who: "manager", ready: /العملاء/, viewport: "desktop" },
  { file: "09-lead-detail.png", route: "/leads/{leadId}", who: "manager", ready: /تحويل لعميل|سجل النشاط/, viewport: "desktop" },
  { file: "10-new-lead.png", route: "/leads/new", who: "manager", ready: /ليد جديد/, viewport: "desktop" },
  { file: "11-insured.png", route: "/clients/{clientId}/insured", who: "manager", ready: /الموظفون المؤمَّنون|الموظفون/, viewport: "desktop" },
  { file: "12-policies.png", route: "/policies", who: "manager", ready: /الوثائق والأقساط/, viewport: "desktop" },
  { file: "13-issuing.png", route: "/issuing", who: "manager", ready: /حالات الإصدار|إصدار/, viewport: "desktop" },
  { file: "14-reports.png", route: "/reports", who: "manager", ready: /التقارير/, viewport: "desktop" },
  // `users.manage` belongs to the admin role, and screen 15 now lives inside the settings hub (ADR-016)
  { file: "15-users.png", route: "/users", who: "admin", ready: /المستخدمون والصلاحيات/, viewport: "desktop" },
  { file: "15b-settings-hub.png", route: "/settings", who: "manager", ready: /الإعدادات/, viewport: "desktop" },
  { file: "16-login.png", route: "/login", who: "anon", ready: /أهلاً بعودتك|تسجيل الدخول/, viewport: "desktop" },
  // extras the review asked for beyond the 16: the same inbox on a phone, and the portal on a phone
  /*
   * The phone capture waits for the screen's own title, not for «صندوق الطلبات»: below `lg` the sidebar —
   * the only place that label lived — is not rendered at all (ADR-018), so `.first()` resolved to the hidden
   * span and reported a perfectly painted inbox as missing (R3, mobile).
   */
  { file: "02-inbox@390x844.png", route: "/inbox", who: "manager", ready: /صندوق الطلبات الموحد/, viewport: "mobile", fullPage: false },
  { file: "06-portal@390x844.png", route: "/portal", who: "owner", ready: /بوابة العميل/, viewport: "mobile", fullPage: false },
];

const ids: { clientId: string; leadId: string } = { clientId: "", leadId: "" };

function resolve(route: string): string {
  return route.replace("{clientId}", ids.clientId).replace("{leadId}", ids.leadId);
}

test.describe("screenshots for design review", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async ({ request }) => {
    mkdirSync(OUT, { recursive: true });
    // phase 2 must be captured unlocked, otherwise half the sheet is a gate card (ADR-008)
    const auth = await apiLogin(request, "admin@brokerhub.test");
    const enabled = await request.patch("/api/v1/feature-flags/phase2.enabled", { headers: auth, data: { enabled: true } });
    expect(enabled.status()).toBe(200);

    const manager = await apiLogin(request, MANAGER);
    const clients = await (await request.get("/api/v1/clients?page_size=1", { headers: manager })).json();
    const leads = await (await request.get("/api/v1/leads?page_size=1", { headers: manager })).json();
    ids.clientId = clients.items[0]?.id ?? "";
    ids.leadId = leads.items[0]?.id ?? "";
    expect(ids.clientId, "the seed must contain a client to photograph").toBeTruthy();
    expect(ids.leadId, "the seed must contain a lead to photograph").toBeTruthy();
  });

  for (const shot of SHOTS) {
    test(shot.file, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== shot.viewport, `${shot.file} is a ${shot.viewport} capture`);
      if (shot.who !== "anon") await login(page, shot.who === "owner" ? OWNER : shot.who === "admin" ? ADMIN : MANAGER);
      await page.goto(resolve(shot.route));
      await expect(page.getByText(shot.ready).first()).toBeVisible({ timeout: 20_000 });
      // let charts, avatars and skeletons settle before freezing pixels
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(OUT, shot.file), fullPage: shot.fullPage !== false });
    });
  }

  test("contact sheet", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the desktop pass assembles the sheet");
    const files = readdirSync(OUT)
      // `15b-` is the settings hub, which is not a 16th screen of §9 but the screen that now carries 15
      .filter((file) => /^\d{2}[a-z]?-.+\.png$/.test(file) && !file.includes("@"))
      // `17-`/`18-` are R6 evidence (the overdue deep link and the gated live switch), not screens of the
      // design sheet — they belong in the report, not in the parity sheet
      .filter((file) => !/^1[78]-/.test(file))
      .sort();
    expect(files.length).toBeGreaterThanOrEqual(14);

    const cells = files
      .map((file) => `<figure><img src="file://${path.join(OUT, file)}"><figcaption>${file.replace(/\.png$/, "")}</figcaption></figure>`)
      .join("\n");
    await page.setViewportSize({ width: 1800, height: 1200 });
    await page.setContent(
      `<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>
       body{margin:0;background:#0E1424;color:#E5E7EB;font:600 14px system-ui;padding:24px}
       h1{font-size:20px;margin:0 0 16px}
       .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}
       figure{margin:0;background:#151B2E;border-radius:14px;overflow:hidden;border:1px solid #26304a}
       img{display:block;width:100%;height:auto;background:#fff}
       figcaption{padding:8px 10px;font-size:12px}
     </style></head><body><h1>Broker Hub — مراجعة الواجهات</h1><div class="grid">${cells}</div></body></html>`,
      { waitUntil: "load" },
    );
    await page.waitForTimeout(1_200);
    await page.screenshot({ path: path.join(OUT, "contact-sheet.png"), fullPage: true });
  });
});
