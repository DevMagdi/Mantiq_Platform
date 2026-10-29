import { expect, test } from "@playwright/test";

import { formatNumber, formatPercent } from "../src/lib/format";

import { apiLogin, login, MANAGER, REPS } from "./fixtures";

/** Screen 1 — the daily cockpit, plus the shell rules (RTL, sidebar, real numbers only). */
test.describe("dashboard (screen 1)", () => {
  test("renders the four KPIs from the API and no placeholder numbers", async ({ page, request }) => {
    const auth = await apiLogin(request, MANAGER);
    const summary = await (await request.get("/api/v1/dashboard/summary", { headers: auth })).json();

    await login(page, MANAGER);
    await expect(page.getByRole("heading", { name: "لوحة التحكم", exact: true })).toBeVisible();
    // the phrase lives twice: the shell's top bar shows it as a breadcrumb, the page header shows it with
    // the date. The screen's own copy is the one inside `main`.
    await expect(page.locator("main").getByText("نظرة حية على كل الطلبات والقنوات")).toBeVisible();

    for (const label of ["طلبات اليوم", "متوسط زمن أول رد", "الالتزام بالـSLA", "طلبات متأخرة"]) {
      // the tile's own label, inside the screen — `main` scoping keeps a shell duplicate out of the frame,
      // and the box is part of the message because a "hidden" verdict on a painted node needs the geometry
      const tile = page.locator("main").getByText(label, { exact: true }).first();
      const box = await tile.boundingBox().catch(() => null);
      await expect(tile, `KPI «${label}» must be painted on the dashboard (boundingBox=${JSON.stringify(box)})`).toBeVisible();
    }

    // the KPI tiles must carry the API's numbers (Egyptian digits), never the mockup's hardcoded ones
    const main = await page.locator("main").innerText();
    expect(main).not.toMatch(/TBD|lorem/i);
    // the mockup's invented target must not survive as data (ADR-014): hints come from the summary
    expect(main).not.toContain("الهدف 90%");
    // The tile paints its label (and a skeleton value) before the summary resolves, and other specs create
    // requests in parallel — so the comparison is made as a poll: the *current* API number must be painted.
    await expect(async () => {
      const fresh = (await (await request.get("/api/v1/dashboard/summary", { headers: auth })).json()) as {
        requests_today: number;
      };
      expect(await page.locator("main").innerText(), "«طلبات اليوم» must show the API's own number").toContain(
        formatNumber(fresh.requests_today),
      );
    }).toPass({ timeout: 20_000 });
    if (summary.sla_compliance_pct !== null && summary.sla_compliance_pct !== undefined) {
      /*
       * The tiles paint their labels before the summary resolves — `value` is undefined until it does, and
       * the Kpi renders a skeleton — so a one-shot read of `innerText` raced the data and lost. Polling the
       * API's own percentage is the same claim with the wait made explicit.
       */
      await expect(async () => {
        const text = await page.locator("main").innerText();
        expect(text, "percentages must be localised").toContain("٪");
        expect(text, "the SLA tile must show the API's percentage").toContain(formatPercent(summary.sla_compliance_pct));
      }).toPass({ timeout: 20_000 });
    }

    // charts and the recent table are real content, not skeletons
    await expect(page.getByText("الطلبات حسب القناة — آخر 7 أيام")).toBeVisible();
    await expect(page.getByText("أحدث الطلبات")).toBeVisible();
    await expect(page.locator('[data-request-row], table tbody tr').first()).toBeVisible({ timeout: 15_000 });
  });

  test("the shell is RTL with navigation on the logical start edge", async ({ page, request }) => {
    const auth = await apiLogin(request, MANAGER);
    const open = (await (await request.get("/api/v1/requests?only_open=true&page_size=1", { headers: auth })).json()).total as number;

    await login(page, MANAGER);
    const root = page.locator("html");
    await expect(root).toHaveAttribute("dir", "rtl");
    await expect(root).toHaveAttribute("lang", "ar");

    const viewport = page.viewportSize();
    const narrow = (viewport?.width ?? 1440) < 1024;
    /*
     * Two shapes, one rule: navigation sits on the logical start edge and carries the role's items.
     * A phone has no room for a 264px sidebar — it took two thirds of a 390px screen and squeezed the
     * dashboard's KPI label to `width: 0` (R3) — so below `lg` the shell renders a bottom bar instead.
     * That is the product's own responsive contract, and this test asserts it in both shapes rather than
     * asking the phone to have a sidebar the design never drew.
     */
    if (narrow) {
      const rail = page.getByRole("navigation", { name: "التنقل السريع", exact: true });
      await expect(rail).toBeVisible();
      const railBox = await rail.boundingBox();
      expect(railBox).not.toBeNull();
      expect(railBox!.y + railBox!.height).toBeGreaterThanOrEqual((viewport?.height ?? 844) - 2); // pinned to the bottom
      expect(railBox!.width).toBeGreaterThan((viewport?.width ?? 390) * 0.9); // spans the screen, not a column
      await expect(page.getByRole("navigation", { name: "التنقل الرئيسي", exact: true })).toHaveCount(0);
      await expect(rail.getByText("صندوق الطلبات", { exact: true }).first()).toBeVisible();
      await expect(rail.getByText("العملاء", { exact: true }).first()).toBeVisible();
      return;
    }

    const nav = page.getByRole("navigation", { name: "التنقل الرئيسي", exact: true });
    await expect(nav).toBeVisible();
    const box = await nav.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThan((viewport?.width ?? 1440) / 2); // RTL ⇒ sidebar sits on the right

    for (const item of ["صندوق الطلبات", "العملاء"]) {
      await expect(nav.getByText(item, { exact: true }).first()).toBeVisible();
    }
    await expect(nav.locator(".rounded-pill")).toHaveCount(1); // exactly one pill: the open-requests count
    await expect(page.locator("body")).toHaveCSS("background-color", "rgb(243, 244, 249)");

    // the pill is that number, or 99+ — never the design sheet's fixed "24"
    const pill = nav.locator(".rounded-pill");
    if (open > 0) {
      await expect(pill).toHaveText(open > 99 ? "99+" : String(open));
    } else {
      await expect(pill).toHaveCount(0);
    }
  });

  test("the header search filters the shared queue", async ({ page }) => {
    await login(page, MANAGER);
    if ((page.viewportSize()?.width ?? 0) < 1024) {
      // below `lg` the shell does not render the global field at all (`hidden lg:block`): the phone screens
      // own their search. Demanding the desktop control at 390px tested a header that is not there.
      await expect(page.getByRole("search")).toHaveCount(0);
      return;
    }
    const box = page.getByRole("search").getByRole("textbox");
    await box.fill("أحمد");
    await box.press("Enter");
    await expect(page).toHaveURL(/\?q=/);
    await expect(page.getByRole("heading", { name: "صندوق الطلبات الموحد", exact: true })).toBeVisible({ timeout: 20_000 });
  });

  test("a rep without requests.view gets no request surfaces in the shell", async ({ page, request }) => {
    await login(page, REPS.sara);
    /*
     * Which navigation exists depends on the width (ADR-018): the phone renders the bottom rail
     * («التنقل السريع») and no sidebar at all. Asking a 390px screen for `التنقل الرئيسي` resolved to zero
     * elements and was read as "the item is filtered out" — the assertion passed for the wrong reason on
     * desktop-shaped queries and failed outright on the phone (R3, mobile). Each shape is asked for its own
     * navigation, and the same three claims hold in both.
     */
    const narrow = (page.viewportSize()?.width ?? 1440) < 1024;
    const nav = page.getByRole("navigation", { name: narrow ? "التنقل السريع" : "التنقل الرئيسي", exact: true });
    await expect(nav, `the ${narrow ? "phone rail" : "sidebar"} is not rendered`).toBeVisible();
    await expect(nav.getByText("صندوق الطلبات", { exact: true })).toHaveCount(0);
    await expect(nav.getByText("لوحة التحكم", { exact: true })).toHaveCount(0);
    await expect(nav.getByText("العملاء المحتملون", { exact: true })).toHaveCount(1);

    // the same boundary the sidebar promises is what the API enforces
    const auth = await apiLogin(request, REPS.sara);
    expect((await request.get("/api/v1/requests?page_size=1", { headers: auth })).status()).toBe(403);
    const mine = await (await request.get("/api/v1/leads?page_size=1", { headers: auth })).json();
    expect(mine.total).toBeGreaterThan(0);
  });
});
