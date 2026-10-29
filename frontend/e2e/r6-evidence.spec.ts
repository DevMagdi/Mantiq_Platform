import path from "node:path";

import { expect, test } from "@playwright/test";

import { apiLogin, login, MANAGER } from "./fixtures";

/**
 * R6 evidence — the two flows this round exists for, captured *and* recorded.
 *
 * A still cannot show "the KPI card was clicked and the queue behind it is the filtered one", and the owner
 * asked for a short clip as well as screenshots. `test.use({ video })` is only legal at file level (it forces
 * a worker), which is why this is a file rather than a block inside `screenshots.spec.ts`; keeping it separate
 * also keeps video off for the other twenty-two captures.
 *
 * Files land in `docs/screenshots/` (uploaded as the `screenshots` artifact) and the videos land with the
 * Playwright report, so a reviewer can watch the click instead of trusting a paragraph.
 */
test.use({ video: "on" });

const OUT = path.resolve(import.meta.dirname, "../../docs/screenshots");

test.describe("R6 evidence", () => {
  test("17-overdue-queue.png", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the dashboard grid and the 3-column queue are 1440×900");
    await login(page, MANAGER);
    await page.goto("/dashboard");

    const card = page.getByTestId("kpi-overdue-link");
    await expect(card).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "17a-dashboard-overdue-card.png"), fullPage: false });

    // the click, the address it leaves behind, and the count the server reports
    await card.click();
    await expect(page).toHaveURL(/\/inbox(\/[0-9a-f-]+)?\?filter=overdue$/, { timeout: 20_000 });
    await expect(page.getByTestId("queue-count")).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(700);
    await page.screenshot({ path: path.join(OUT, "17-overdue-queue.png"), fullPage: true });
  });

  test("18-not-connected.png", async ({ page, request }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the settings cards sit under the mobile bar at 390px");
    // a provider with no green test is exactly what an operator sees before they can go live
    const auth = await apiLogin(request, MANAGER);
    const cleared = await request.put("/api/v1/settings/integrations/telephony", {
      headers: auth,
      data: { config: { provider_url: "" } },
    });
    expect(cleared.status(), await cleared.text()).toBe(200);

    await login(page, MANAGER);
    await page.goto("/settings");
    await expect(page.getByTestId("connection-missing-telephony")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("live-switch-telephony")).toHaveCount(0);
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "18-not-connected.png"), fullPage: true });
  });
});
