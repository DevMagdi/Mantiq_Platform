import { expect, test, type APIRequestContext } from "@playwright/test";

import { ar } from "../src/lib/i18n/ar";

import { apiLogin, ADMIN, login, MANAGER } from "./fixtures";

/**
 * R7 — Phase 2 as a loop a manager can actually drive, in a browser, from the first field to a recorded payment.
 *
 * The R5 spec issued a policy that the *API* had created. That proved issuance and left the real gap in place:
 * the screen had no way to book the policy, so the loop began somewhere the user could not reach. This file
 * starts where a manager starts — «وثيقة جديدة» — and ends where the money is: a paid installment the API
 * agrees with.
 *
 * Each test books its own company first (a unique name, written through the API) and then finds its row by
 * that name. The table is ordered by creation, and other specs create policies in parallel — "the first row"
 * would be a race dressed as an assertion.
 *
 * Desktop only, same rule as the rest of the round (ADR-019): screen 12 is a 1440×900 table with a dialog.
 */

function desktopOnly(testInfo: { project: { name: string } }): boolean {
  return testInfo.project.name !== "desktop";
}

async function phase2On(request: APIRequestContext): Promise<void> {
  const auth = await apiLogin(request, ADMIN);
  const response = await request.patch("/api/v1/feature-flags/phase2.enabled", { headers: auth, data: { enabled: true } });
  expect(response.status(), await response.text()).toBe(200);
}

/** A company nobody else in this run owns, so this spec's row is unambiguous. */
async function freshClient(request: APIRequestContext, auth: Record<string, string>): Promise<{ id: string; name: string }> {
  const name = `شركة R7 ${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
  const created = await request.post("/api/v1/clients", { headers: auth, data: { name, type: "company" } });
  expect(created.status(), await created.text()).toBe(201);
  return { id: (await created.json()).id as string, name };
}

test.describe("R7 · the Phase-2 loop", () => {
  test("a draft is created from the screen, issued, and its PDF downloaded", async ({ page, request }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 table flow (ADR-019)");
    await phase2On(request);
    const auth = await apiLogin(request, MANAGER);
    const client = await freshClient(request, auth);
    const premium = String(240_000 + Math.floor(Math.random() * 1000));

    await login(page, MANAGER);
    await page.goto("/policies");
    await expect(page.getByRole("heading", { name: new RegExp(ar.phase2.policies) })).toBeVisible({ timeout: 20_000 });

    // 1) the draft, typed in by a person
    await page.getByRole("button", { name: ar.phase2.newPolicy }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const picker = dialog.getByLabel(ar.phase2.form.client);
    // the option list is filled by a query — choosing before it lands silently does nothing
    await expect.poll(async () => (await picker.locator("option").count()) > 1, { timeout: 20_000 }).toBe(true);
    await picker.selectOption({ label: client.name });
    await dialog.getByLabel(ar.phase2.form.premium).fill(premium);
    await dialog.getByLabel(ar.phase2.form.plan).selectOption("quarterly");
    await dialog.getByRole("button", { name: ar.phase2.saveDraft }).click();
    await expect(dialog).toHaveCount(0, { timeout: 20_000 });

    // the row the API really wrote: a draft, this company, and a schedule behind it
    const listing = await (await request.get("/api/v1/policies?page_size=50", { headers: auth })).json();
    const created = (listing.items as { id: string; status: string; client?: { name: string } }[]).find(
      (row) => row.client?.name === client.name && row.status === "draft",
    );
    expect(created, "the draft the dialog submitted must exist as a draft row").toBeTruthy();
    const policyId = (created as { id: string }).id;

    // 2) the badge says «مسودة» in Arabic — not the toast sentence and not the raw enum
    const row = page.locator("table tbody tr").filter({ hasText: client.name }).first();
    await expect(row).toContainText(ar.phase2.policyStatus.draft, { timeout: 20_000 });

    // 3) issue from the table, then take the file
    await row.getByRole("button", { name: ar.phase2.issueAction, exact: true }).click();
    await expect(row).toContainText(ar.phase2.policyStatus.issued, { timeout: 20_000 });
    const download = row.getByRole("button", { name: ar.common.download, exact: true });
    await expect(download).toBeVisible({ timeout: 20_000 });
    const [file] = await Promise.all([page.waitForEvent("download"), download.click()]);
    expect(file.suggestedFilename().toLowerCase()).toMatch(/\.pdf$/);

    const saved = await request.get(`/api/v1/policies/${policyId}/document`, { headers: auth });
    expect(saved.status()).toBe(200);
    expect((await saved.body()).subarray(0, 5).toString()).toBe("%PDF-");

    // 4) the plan the issue left behind: four rows for a quarterly policy, and the money adds up
    const plan = await (await request.get(`/api/v1/policies/${policyId}/installments`, { headers: auth })).json();
    expect(plan.items).toHaveLength(4);
    expect(plan.totals.outstanding_amount).toBeCloseTo(Number(premium), 1);
  });

  test("a manager records a payment from the plan dialog, and the ledger agrees", async ({ page, request }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 table flow (ADR-019)");
    await phase2On(request);
    const auth = await apiLogin(request, MANAGER);
    const client = await freshClient(request, auth);

    // arranged through the API: this test is about the *payment* half of the loop
    const draft = await request.post("/api/v1/policies", {
      headers: auth,
      data: {
        client_id: client.id,
        start_date: "2026-11-01",
        end_date: "2027-10-31",
        premium_amount: 120000,
        payment_plan: "quarterly",
        commission_rate: 5,
      },
    });
    expect(draft.status(), await draft.text()).toBe(201);
    const policyId = (await draft.json()).id as string;
    const issued = await request.post(`/api/v1/policies/${policyId}/issue`, { headers: auth });
    expect(issued.status(), await issued.text()).toBe(200);
    const policyNo = (await issued.json()).policy_no as string;

    await login(page, MANAGER);
    await page.goto("/policies");
    await expect(page.getByRole("heading", { name: new RegExp(ar.phase2.policies) })).toBeVisible({ timeout: 20_000 });

    // the row for *this* policy opens the plan dialog
    await page.locator("table tbody tr").filter({ hasText: policyNo }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(ar.phase2.commission);
    await expect(dialog.getByText(ar.phase2.commissionNotComputed)).toHaveCount(0); // 5% is a computed number

    // pay the first unpaid installment
    const pay = dialog.getByRole("button", { name: ar.phase2.markPaid });
    await expect(pay).toHaveCount(4, { timeout: 20_000 });
    await pay.first().click();
    await expect(dialog.getByRole("button", { name: ar.phase2.markPaid })).toHaveCount(3, { timeout: 20_000 });
    await expect(dialog.getByText(ar.phase2.paid).first()).toBeVisible();

    // the API says the same thing, and the totals moved by exactly one installment
    const plan = await (await request.get(`/api/v1/policies/${policyId}/installments`, { headers: auth })).json();
    const paid = (plan.items as { status: string; amount: number }[]).filter((item) => item.status === "paid");
    expect(paid).toHaveLength(1);
    expect(plan.totals.paid_count).toBe(1);
    expect(plan.totals.paid_amount).toBeCloseTo(paid[0]?.amount ?? -1, 2);
    expect(plan.commission.state).toBe("computed");
    expect(plan.commission.amount).toBeCloseTo(6000, 2); // 5% of 120 000

    // and reopening the plan shows three, not four: a paid row offers no second receipt
    await dialog.getByRole("button", { name: ar.common.close, exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.locator("table tbody tr").filter({ hasText: policyNo }).first().click();
    await expect(page.getByRole("dialog").getByRole("button", { name: ar.phase2.markPaid })).toHaveCount(3, { timeout: 20_000 });
  });
});
