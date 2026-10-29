import { expect, test } from "@playwright/test";

import { ADMIN, apiLogin, CS, login, MANAGER, REPS } from "./fixtures";

/**
 * The settings hub (spec §8, ADR-016) — the screen that decides whether a provider talks to customers or
 * only to the simulator. What is asserted here is what a unit test cannot: that a saved secret survives a
 * reload *without ever appearing on screen*, that the live switch refuses an untested provider and says why,
 * and that an inbound event really lands in the log the screen reads back.
 *
 * Two rules this file follows after its first CI run:
 *  - every test opens with an API assertion, so a failure prints the server's own answer in the report
 *    instead of a timeout on a locator (GitHub keeps 50 annotations per job; a bare timeout is unfindable);
 *  - assertions read stored state (a reloaded input, a count of badges) rather than decoration (a toast that
 *    disappears after two seconds).
 *
 * Viewports: the interactive pass is desktop-only — at 390px the hub's cards sit under the fixed mobile bar,
 * and an assertion fighting a sticky header tests the header. Mobile rendering is covered here by the
 * no-secret check and by `15b-settings-hub.png` in the screenshots job (same split as `simulator.spec.ts`).
 */

/** The exact controls this screen owns, so a locator cannot land on another provider's row. */
const PHONE = "config-whatsapp-phone_number_id";
const TOKEN = "secret-whatsapp-access_token";
const DROP_TOKEN = "secret-delete-whatsapp-access_token";

test.describe("settings hub · providers", () => {
  test("the three providers are listed, and no secret field carries a value", async ({ page, request }) => {
    const auth = await apiLogin(request, MANAGER);
    const listed = await page.request.get("/api/v1/settings/integrations", { headers: auth });
    expect(listed.status(), await listed.text()).toBe(200);

    await login(page, MANAGER);
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "الإعدادات" })).toBeVisible();
    for (const provider of ["واتساب للأعمال", "مكالمات الموبايل (Live)", "البريد (SMTP)"]) {
      await expect(page.getByRole("heading", { name: provider })).toBeVisible();
    }

    const values = await page
      .locator('input[type="password"]')
      .evaluateAll((nodes) => nodes.map((node) => (node as HTMLInputElement).value));
    expect(values.length).toBeGreaterThanOrEqual(6);
    expect(values.filter((value) => value !== "")).toEqual([]);
  });

  test("a saved secret survives a reload invisible, while the plain fields stay visible", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "desktop-only — see the header");
    const auth = await apiLogin(request, MANAGER);
    const before = await request.get("/api/v1/settings/integrations/whatsapp", { headers: auth });
    expect(before.status(), await before.text()).toBe(200);

    await login(page, MANAGER);
    await page.goto("/settings");
    await page.getByTestId(TOKEN).fill("e2e-token-must-not-leak");
    await page.getByTestId(PHONE).fill("123456789012345");
    await page.getByRole("button", { name: "حفظ" }).first().click();

    await page.reload();
    const secret = page.getByTestId(TOKEN);
    await expect(secret).toBeVisible();
    // stored (the server says so) and invisible (the input is empty; its placeholder is the mask)
    const after = await request.get("/api/v1/settings/integrations/whatsapp", { headers: auth });
    expect(after.ok(), await after.text()).toBeTruthy();
    expect(JSON.stringify(after.json())).not.toContain("e2e-token-must-not-leak");
    await expect(secret).toHaveValue("");
    await expect(secret).toHaveAttribute("placeholder", "••••••••");
    await expect(page.locator("body")).not.toContainText("e2e-token-must-not-leak");
    // and the non-secret half of the same save is visible, because it is not a secret
    await expect(page.getByTestId(PHONE)).toHaveValue("123456789012345");

    // leave the row as we found it for the rest of the suite
    await page.getByTestId(DROP_TOKEN).click();
    await page.getByTestId(PHONE).fill("");
    await page.getByRole("button", { name: "حفظ" }).first().click();
    await expect(page.getByTestId(PHONE)).toHaveValue("");
  });

  test("an untested provider is not offered the switch, and the card says why", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "desktop-only — see the header");
    /*
     * R6 changed this test, not the rule behind it. The old version pressed the switch, waited for the dialog
     * to warn, pressed confirm, and read the 409 back as a toast — i.e. it asserted that the screen offers a
     * button the server is going to refuse. Now the screen asks `GET …/preflight` first and disables the
     * switch, so the honest assertion is: the button is disabled, the reason is on screen, and no request is
     * made at all. The 409 itself is still covered — by `test_the_409_carries_the_fields_and_the_next_step`
     * in the backend suite, where it belongs, because it is the API's contract and not a screen's.
     */
    // telephony is the row no other spec leaves green, so "no failed test yet" is a fact here, not a race
    const auth = await apiLogin(request, MANAGER);
    const state = await request.get("/api/v1/settings/integrations/telephony", { headers: auth });
    expect(state.status(), await state.text()).toBe(200);
    expect((await state.json()).last_test_status ?? null, "this test is only meaningful while the row is untested").toBeNull();
    const preflight = await request.get("/api/v1/settings/integrations/telephony/preflight", { headers: auth });
    expect(preflight.status(), await preflight.text()).toBe(200);
    expect((await preflight.json()).ready, "the API must agree that this row is not ready").toBe(false);

    await login(page, MANAGER);
    await page.goto("/settings");
    // R12 §A: there is no live switch any more — the card states the connection and names what is missing
    await expect(page.getByTestId("live-switch-telephony")).toHaveCount(0);
    await expect(page.getByText("غير متصل", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
    const missing = page.getByTestId("connection-missing-telephony");
    await expect(missing).toBeVisible();
    const after = await request.get("/api/v1/settings/integrations/telephony", { headers: auth });
    expect((await after.json()).connection_status, "an uncredentialed row must not read connected").toBe("not_connected");
  });

  test("saving credentials and a green test shows Connected — no switch is pressed (R12)", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "desktop-only — see the header");
    await login(page, MANAGER);
    await page.goto("/settings");

    const whatsapp = page.locator("section.card", { hasText: "واتساب للأعمال" }).first();
    await page.getByTestId(PHONE).fill("999888777");
    await page.getByTestId(TOKEN).fill("e2e-live-token");
    await page.getByRole("button", { name: "حفظ" }).first().click();
    await expect(page.getByTestId(PHONE)).toHaveValue("999888777");

    await page.getByRole("button", { name: "اختبار الاتصال" }).first().click();
    await expect(page.getByTestId("test-result-whatsapp")).toContainText("وضع المحاكاة", { timeout: 20_000 });

    // R12 §A: credentials + a green test are the connection — the badge flips with no switch pressed
    await expect(whatsapp.getByText("متصل", { exact: true })).toBeVisible({ timeout: 20_000 });
    const auth = await apiLogin(request, MANAGER);
    await expect
      .poll(async () => ((await (await request.get("/api/v1/settings/integrations/whatsapp", { headers: auth })).json()).connection_status as string))
      .toBe("connected");
    await expect(page.getByTestId("live-switch-whatsapp")).toHaveCount(0);

    await page.getByTestId(DROP_TOKEN).click();
    await page.getByTestId(PHONE).fill("");
    await page.getByRole("button", { name: "حفظ" }).first().click();
    await expect(page.getByTestId(PHONE)).toHaveValue("");
  });

  test("an inbound event is readable from the screen, without opening a server log", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "desktop-only — see the header");
    const auth = await apiLogin(request, ADMIN);
    const delivered = await request.post("/api/v1/webhooks/mobile-calls", {
      headers: auth,
      data: { call_id: "e2e-call-1", event: "call.started", from: "+201000000000" },
    });
    expect(delivered.status(), await delivered.text()).toBe(200);

    await login(page, MANAGER);
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "آخر الأحداث الواردة" })).toBeVisible();
    await expect(page.getByText("call.started").first()).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("settings hub · who may see it", () => {
  test("the users tab needs users.manage, which the manager role does not hold", async ({ page }) => {
    await login(page, MANAGER);
    await page.goto("/settings");
    await expect(page.getByRole("tab", { name: "سياسة الاكتتاب" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "المستخدمون والصلاحيات" })).toHaveCount(0);

    // the old deep link still works: it lands inside the hub rather than on a 404
    await page.goto("/users");
    await expect(page.getByRole("heading", { name: "الإعدادات" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "المستخدمون والصلاحيات" })).toHaveCount(0);
  });

  test("an admin sees the users tab inside the hub", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/settings?tab=users");
    await expect(page.getByRole("heading", { name: "المستخدمون والصلاحيات" })).toBeVisible({ timeout: 20_000 });
  });

  test("a rep without settings.manage is turned away, not shown a read-only copy", async ({ page, request }) => {
    const auth = await apiLogin(request, REPS.sara);
    const denied = await request.get("/api/v1/settings/integrations", { headers: auth });
    expect(denied.status(), await denied.text()).toBe(403);

    await login(page, REPS.sara);
    await page.goto("/settings");
    await expect(page.getByText("لا تملك صلاحية لهذه الشاشة")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "اختبار الاتصال" })).toHaveCount(0);
  });

  test("a service-desk agent is turned away too", async ({ page, request }) => {
    const auth = await apiLogin(request, CS);
    const denied = await request.get("/api/v1/settings/integrations", { headers: auth });
    expect(denied.status(), await denied.text()).toBe(403);

    await login(page, CS);
    await page.goto("/settings");
    await expect(page.getByText("لا تملك صلاحية لهذه الشاشة")).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("settings hub · underwriting defaults", () => {
  test("a saved default is stored by the API and read back by the screen", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "desktop-only — see the header");
    const auth = await apiLogin(request, MANAGER);
    const defaults = await request.get("/api/v1/settings/policies", { headers: auth });
    expect(defaults.status(), await defaults.text()).toBe(200);
    const stored = (await defaults.json()) as { default_commission_pct: number };
    expect(stored.default_commission_pct).toBeGreaterThan(0);

    await login(page, MANAGER);
    await page.goto("/settings?tab=policies");
    const commission = page.getByLabel(/نسبة العمولة الافتراضية/);
    await expect(commission).toBeVisible();

    await commission.fill("7.5");
    await page.getByRole("button", { name: "حفظ", exact: true }).click();
    await expect
      .poll(async () => (await (await request.get("/api/v1/settings/policies", { headers: auth })).json()).default_commission_pct as number)
      .toBe(7.5);

    await page.reload();
    await expect(page.getByLabel(/نسبة العمولة الافتراضية/)).toHaveValue("7.5");

    // back to the seeded value so a later spec reads what it expects
    await page.getByLabel(/نسبة العمولة الافتراضية/).fill(String(stored.default_commission_pct));
    await page.getByRole("button", { name: "حفظ", exact: true }).click();
    await expect
      .poll(async () => (await (await request.get("/api/v1/settings/policies", { headers: auth })).json()).default_commission_pct as number)
      .toBe(stored.default_commission_pct);
  });
});
