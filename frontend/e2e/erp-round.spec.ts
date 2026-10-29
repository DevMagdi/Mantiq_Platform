import { expect, test } from "@playwright/test";

import { login, MANAGER, REPS } from "./fixtures";

/** ERP hub (ADR-031): manager reaches /erp, switches period, opens the ledger; a rep never sees ERP. */
test.describe("ERP accounting hub", () => {
  test("manager opens /erp, switches period and opens the ledger from a card", async ({ page }, testInfo) => {
    await login(page, MANAGER);
    if (testInfo.project.name === "desktop") {
      // the sidebar item is the entry point on desktop; on mobile the bottom bar shows only the
      // first five items (ADR-018), so the deep route is the honest path there
      await page.getByRole("link", { name: /ERP المحاسبي/ }).first().click();
    } else {
      await page.goto("/erp");
    }
    await expect(page).toHaveURL(/\/erp$/);
    await expect(page.getByRole("heading", { name: "ERP المحاسبي" })).toBeVisible();
    await page.getByRole("button", { name: "سنوي", exact: true }).click();
    await expect(page.getByText("صُرف / دخل على إيه", { exact: false })).toBeVisible();
    await page.getByRole("link", { name: /دفتر الحركات/ }).first().click();
    await expect(page).toHaveURL(/\/finance\/ledger$/);
  });

  test("manager exports the period Excel and sees the closes card (Score-8)", async ({ page }) => {
    await login(page, MANAGER);
    await page.goto("/erp");
    await expect(page.getByRole("heading", { name: "ERP المحاسبي" })).toBeVisible();
    await expect(page.getByText("إقفال الفترات", { exact: false })).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: /تصدير Excel/ }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain(".xlsx");
  });

  test("a rep does not see the ERP item and /erp is refused", async ({ page }) => {
    await login(page, REPS.sara);
    await expect(page.getByRole("link", { name: /ERP المحاسبي/ })).toHaveCount(0);
    await page.goto("/erp");
    await expect(page.getByText("403")).toBeVisible();
  });
});
