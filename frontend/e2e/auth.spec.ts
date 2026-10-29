import { expect, test } from "@playwright/test";

import { apiLogin, LOGIN_BUTTON, login, OWNER, PASSWORD, REPS } from "./fixtures";

test.describe("authentication", () => {
  test("a rep lands on the board their role can load", async ({ page, request }) => {
    /*
     * The dashboard's summary requires `requests.view`, which a sales rep does not hold — landing there
     * produced four empty tiles and a 403 in the log. Landing is by permission now, so the rep starts on
     * their own leads board, and the KPI label the old assertion used ("نسبة الالتزام بالـ SLA") never
     * existed: the tile is "الالتزام بالـSLA" (`dashboard.kpiSla` in both dictionaries).
     */
    await login(page, REPS.sara);
    await expect(page).toHaveURL(/\/leads/);
    await expect(page.getByText("عرض البياع")).toBeVisible();
    const auth = await apiLogin(request, REPS.sara);
    const refused = await request.get("/api/v1/dashboard/summary", { headers: auth });
    expect(refused.status()).toBe(403);
    expect((await refused.json()).code).toBe("forbidden");
  });

  test("a wrong password is refused with the server's own message", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/البريد الإلكتروني/).fill(REPS.sara);
    await page.getByLabel(/كلمة المرور/).fill("not-the-password");
    await page.getByRole("button", LOGIN_BUTTON).click();
    await expect(page.getByText(/بيانات الدخول غير صحيحة|خطوة|غير صحيحة/).first()).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("the client owner is routed to the portal, never to the staff shell", async ({ page }) => {
    await login(page, OWNER);
    await expect(page.getByText("بوابة العميل")).toBeVisible();
    await expect(page.getByRole("navigation")).toHaveCount(0);
  });

  test("an authenticated session survives a reload (refresh cookie rotation)", async ({ page }) => {
    /*
     * Two premises were wrong here. A rep holds no `requests.view`, so their landing screen is their own
     * board, never the dashboard (ADR-015) — the reload is proven on the screen the role actually gets.
     * And the rotation is a property of the cookie, not of the page: the access token also lives in
     * localStorage, so "the app still works" cannot show whether the refresh cookie was accepted,
     * rotated, or never sent. Both halves are asserted where they live.
     */
    await login(page, REPS.nour);
    await expect(page).toHaveURL(/\/leads/);
    await expect(page.getByText("عرض البياع")).toBeVisible();

    await page.reload();
    await expect(page.getByText("عرض البياع")).toBeVisible({ timeout: 15_000 });
    expect(page.url()).not.toContain("/login");

    // `page.request` shares the browser context's jar, so these three calls are the *same* session the UI
    // holds. The `request` fixture keeps its own jar: a call that answered 200 there had simply collected
    // the `Set-Cookie` of the previous call, and `clearCookies()` on the page could not take it away.
    const jar = async (): Promise<string> => (await page.context().cookies()).find((row) => row.name === "bh_refresh")?.value ?? "";

    const before = await jar();
    expect(before.length, "the login must set a refresh cookie on /api/v1/auth").toBeGreaterThan(20);

    const rotated = await page.request.post("/api/v1/auth/refresh");
    expect(rotated.status(), await rotated.text()).toBe(200);
    expect((await rotated.json()).access_token as string).toMatch(/^\S+\.\S+\.\S+$/);

    const after = await jar();
    expect(after.length, "the answer must set a new refresh cookie").toBeGreaterThan(20);
    expect(after, "a rotation that leaves the jar holding the same value is not a rotation").not.toBe(before);

    // and the app can still reach the API afterwards — the reload promise, not only the cookie math
    await page.reload();
    await expect(page.getByText("عرض البياع")).toBeVisible({ timeout: 15_000 });

    await page.context().clearCookies();
    const refused = await page.request.post("/api/v1/auth/refresh");
    expect(refused.status(), await refused.text()).toBe(403);
    expect((await refused.json()).code).toBe("forbidden");
    expect(PASSWORD.length).toBeGreaterThan(8);
  });
});
