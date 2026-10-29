import { expect, test, type APIRequestContext } from "@playwright/test";

import { ar } from "../src/lib/i18n/ar";

import { apiLogin, ADMIN, login, MANAGER, OWNER } from "./fixtures";

/**
 * R5 — the five paths that used to end at a screen instead of at a result: issuance (a file), the manager's
 * lead filters, portal accounts (a username that signs in) and the portal's own policy download.
 *
 * Every test arranges through the API and then proves the *browser* did the work:
 * - a policy is created by the API and issued by clicking «إصدار» in the table, and the download is a real
 *   Playwright `download` event with a `.pdf` filename (a broken `<a href>` fires nothing);
 * - the filter test compares the cards on screen with the rows the API returns for the same filter, so a
 *   client-side filter that ignored the query string would fail here;
 * - the portal test signs in with a *username* (not the generated mailbox) in a fresh browser context.
 *
 * Desktop only, same rule as `team-round.spec.ts` (ADR-019): the policies table and the client-file dialogs
 * are 1440×900 surfaces from the design sheet, and a phone run would spend the budget chasing a table
 * column that scrolls out of the viewport instead of asserting a fact. The API half of every flow runs on the
 * server and is covered at both widths by `scripts/live_matrix.py`.
 */

function desktopOnly(testInfo: { project: { name: string } }): boolean {
  return testInfo.project.name !== "desktop";
}

let counter = 0;
function unique(prefix: string): string {
  counter += 1;
  return `${prefix}.${Date.now().toString(36)}${counter}`.toLowerCase();
}

async function phase2On(request: APIRequestContext): Promise<void> {
  const auth = await apiLogin(request, ADMIN);
  const response = await request.patch("/api/v1/feature-flags/phase2.enabled", { headers: auth, data: { enabled: true } });
  expect(response.status(), await response.text()).toBe(200);
}

test.describe("R5 · issuance, filters and portal accounts", () => {
  test("a manager issues a draft from the table and downloads the PDF", async ({ page, request }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 table flow (ADR-019)");
    await phase2On(request);
    const auth = await apiLogin(request, MANAGER);

    // the portal owner's company: the same client the portal test below downloads from
    const ownerAuth = await apiLogin(request, OWNER);
    const ownerClient = (await (await request.get("/api/v1/portal/overview", { headers: ownerAuth })).json()).client.id as string;

    const draft = await request.post("/api/v1/policies", {
      headers: auth,
      data: {
        client_id: ownerClient,
        start_date: "2026-11-01",
        end_date: "2027-10-31",
        premium_amount: 275000,
        headcount: 33,
        notes: `R5 issuance ${unique("qa")}`,
      },
    });
    expect(draft.status(), await draft.text()).toBe(201);
    const policyId = (await draft.json()).id as string;

    await login(page, MANAGER);
    await page.goto("/policies");
    await expect(page.getByRole("heading", { name: /الوثائق والأقساط/ })).toBeVisible({ timeout: 20_000 });

    // the list is newest-first, so the draft this test just created is the first row
    const row = page.locator("table tbody tr").first();
    await expect(row).toBeVisible({ timeout: 20_000 });
    const issue = row.getByRole("button", { name: ar.phase2.issueAction, exact: true });
    await expect(issue).toBeVisible();
    const issued = await request.get(`/api/v1/policies?page_size=5`, { headers: auth });
    expect(issued.ok()).toBeTruthy();

    await issue.click();
    const download = row.getByRole("button", { name: ar.common.download, exact: true });
    await expect(download).toBeVisible({ timeout: 20_000 });

    // the file: a download event with a name that ends in .pdf, and the API agrees a file exists
    const [file] = await Promise.all([page.waitForEvent("download"), download.click()]);
    expect(file.suggestedFilename().toLowerCase()).toMatch(/\.pdf$/);
    const saved = await request.get(`/api/v1/policies/${policyId}/document`, { headers: auth });
    expect(saved.status()).toBe(200);
    expect(saved.headers()["content-type"]).toContain("application/pdf");
    expect((await saved.body()).subarray(0, 5).toString()).toBe("%PDF-");
  });

  test("the Kanban filters by rep, by stage and by the unassigned pool", async ({ page, request }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 board flow (ADR-019)");
    const auth = await apiLogin(request, MANAGER);

    const team = (await (await request.get("/api/v1/sales/team", { headers: auth })).json()) as { rep_id: string; name: string; open_leads: number }[];
    const rep = team.find((member) => member.open_leads > 0);
    expect(rep, "the seed has a rep with open leads").toBeTruthy();
    const expected = (await (await request.get(`/api/v1/leads?assigned_to=${rep!.rep_id}&page_size=100`, { headers: auth })).json()).items as {
      name: string;
    }[];
    expect(expected.length).toBeGreaterThan(0);

    await login(page, MANAGER);
    await page.goto("/leads");
    await expect(page.getByRole("heading", { name: /ليدز/ })).toBeVisible({ timeout: 20_000 });

    // filter by the rep: the value is the id, because the option label carries a formatted count
    await page.getByLabel(ar.leads.filterRep).selectOption(rep!.rep_id);
    await expect.poll(async () => page.locator("[data-lead-card]").count(), { timeout: 20_000 }).toBeGreaterThan(0);
    const cards = await page.locator("[data-lead-card]").allTextContents();
    const names = expected.map((lead) => lead.name);
    for (const card of cards) {
      expect(names.some((name) => card.includes(name)), `a card outside ${rep!.name}'s book: ${card.slice(0, 60)}`).toBeTruthy();
    }

    // stage: one lane, and it is the lane that was asked for
    await page.getByLabel(ar.leads.filterStage).selectOption("new");
    await expect(page.locator("[data-column]")).toHaveCount(1);
    await expect(page.locator("[data-column]").first()).toHaveAttribute("data-column", "new");
    await page.getByLabel(ar.leads.filterStage).selectOption("");

    // the pool: rep filter + unassigned is empty on purpose (an unassigned lead has no rep)
    await page.getByLabel(ar.leads.filterUnassigned).check();
    await expect(page.locator("[data-lead-card]")).toHaveCount(0, { timeout: 20_000 });
    await page.getByLabel(ar.leads.filterUnassigned).uncheck();
    await expect.poll(async () => page.locator("[data-lead-card]").count(), { timeout: 20_000 }).toBeGreaterThan(0);
  });

  test("a portal account is created from the client file and its password is shown once", async ({ page, request }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 client-file dialog (ADR-019)");
    const auth = await apiLogin(request, MANAGER);
    const ownerAuth = await apiLogin(request, OWNER);
    const ownerClient = (await (await request.get("/api/v1/portal/overview", { headers: ownerAuth })).json()).client.id as string;

    await login(page, MANAGER);
    await page.goto(`/clients/${ownerClient}`);
    await expect(page.getByRole("tablist", { name: ar.clients.file.title })).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: ar.clients.portal.create }).click();
    await page.getByLabel(ar.clients.portal.username).fill(unique("qa.portal"));
    await page.getByRole("button", { name: ar.common.confirm }).click();

    // the temporary password is shown once, in a monospace `dd`, and never returned by any list route
    const password = page.locator("dd.font-mono");
    await expect(password, "the one-time password must be on screen after creating the account").toBeVisible({ timeout: 20_000 });
    expect(((await password.textContent()) ?? "").trim().length).toBeGreaterThan(7);

    // the handoff: one block a staff member can copy and send, with the username and that same password
    const handoff = page.getByTestId("portal-handoff");
    await expect(handoff).toBeVisible();
    await expect(handoff).toContainText(((await password.textContent()) ?? "").trim());

    // and it really exists in the database, against this client only
    const accounts = await (await request.get(`/api/v1/clients/${ownerClient}/portal-accounts`, { headers: auth })).json();
    const rows = (accounts.items ?? accounts) as { username: string | null; temporary_password?: string }[];
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => row.temporary_password === undefined), "a list must never replay a password").toBeTruthy();
  });

  test("a username portal login sees its own policy file and downloads it", async ({ request, browser }, testInfo) => {
    test.skip(desktopOnly(testInfo), "1440×900 portal screen (ADR-019)");
    test.setTimeout(120_000);
    await phase2On(request);
    const auth = await apiLogin(request, MANAGER);

    // Everything is arranged through the API: this test is about the *session* a client owner gets with a
    // username, and about the file they can take away — not about the dialog (the test above covers it).
    const ownerAuth = await apiLogin(request, OWNER);
    const ownerClient = (await (await request.get("/api/v1/portal/overview", { headers: ownerAuth })).json()).client.id as string;
    const draft = await request.post("/api/v1/policies", {
      headers: auth,
      data: { client_id: ownerClient, start_date: "2026-11-01", end_date: "2027-10-31", premium_amount: 180000 },
    });
    expect(draft.status(), await draft.text()).toBe(201);
    const issued = await request.post(`/api/v1/policies/${(await draft.json()).id}/issue`, { headers: auth });
    expect(issued.status(), await issued.text()).toBe(200);
    const policyNo = (await issued.json()).policy_no as string;

    // a fresh username login, with a password only the owner and the manager ever see
    const username = unique("qa.owner");
    const created = await request.post(`/api/v1/clients/${ownerClient}/portal-accounts`, {
      headers: auth,
      data: { username, full_name: "مالك البوابة", portal_role: "owner" },
    });
    expect(created.status(), await created.text()).toBe(201);
    const temp = (await created.json()).temporary_password as string;
    expect(temp.length).toBeGreaterThan(7);

    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await login(page, username, temp);
      await expect(page, "a username alone must land on the portal").toHaveURL(/\/portal/, { timeout: 20_000 });
      await expect(page.getByText(policyNo).first(), "the owner sees the policy number").toBeVisible({ timeout: 20_000 });

      // the download path the screen is wired to, straight from the API the browser calls
      const policies = (await (await request.get("/api/v1/portal/policies", { headers: ownerAuth })).json()) as {
        policy_no: string;
        document: { download_url: string | null } | null;
      }[];
      const row = policies.find((entry) => entry.policy_no === policyNo);
      expect(row, "the portal policy list must carry the issued policy").toBeTruthy();
      const url = row?.document?.download_url ?? "";
      expect(url, "the row must point at a document route").toContain("/document");

      const button = page.getByLabel(ar.common.download).first();
      await expect(button, "the portal offers a download control for the issued file").toBeVisible({ timeout: 20_000 });
      const [file] = await Promise.all([
        page.waitForEvent("download", { timeout: 30_000 }).catch((error: unknown) => {
          throw new Error(`«${ar.common.download}» produced no browser download (download_url=${url}): ${String(error)}`);
        }),
        button.click(),
      ]);
      expect(file.suggestedFilename().toLowerCase(), "the saved file is the policy PDF").toMatch(/\.pdf$/);

      // and none of the staff vocabulary reaches the portal
      const text = (await page.locator("body").textContent()) ?? "";
      expect(text).not.toContain(ar.leads.filterRep);
      expect(text).not.toContain("عمولة");
    } finally {
      await context.close();
    }
  });
});
