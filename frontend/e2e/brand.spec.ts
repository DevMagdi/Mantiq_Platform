import { expect, test } from "@playwright/test";

import { login, MANAGER } from "./fixtures";

/**
 * R13 / ADR-034 — the brand is a contract, not a screenshot.
 *
 * These assertions are deliberately about *computed* values and DOM facts: the sidebar's navy, the gold
 * active label, the white page, the navy label on a gold primary button, the tab title and the icon set.
 * A palette can look right in a review image while a stray indigo survives in one screen; this spec is
 * the part that cannot be fooled that way.
 */
const DEEP_NAVY = "rgb(14, 23, 40)"; // --color-brand-deep #0E1728
const MANTIQ_GOLD = "rgb(189, 167, 126)"; // --color-gold #BDA77E
const PAGE_WHITE = "rgb(250, 251, 252)"; // --color-bg #FAFBFC

test.describe("Mantiq brand", () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) < 1024, "the sidebar only exists at lg and above");

  test("login: white card, navy panel, gold primary button with a navy label", async ({ page }) => {
    await page.goto("/login");
    const submit = page.getByRole("button", { name: "تسجيل الدخول", exact: true });
    await expect(submit).toHaveCSS("background-color", MANTIQ_GOLD);
    // gold is mid-luminance: the label is Deep Navy, never white (ADR-034 contrast rule)
    await expect(submit).toHaveCSS("color", DEEP_NAVY);

    const panel = page.locator("div").filter({ hasText: "منطق" }).first();
    await expect(panel).toBeVisible();
    // the old product name is gone from the chrome
    await expect(page.locator("body")).not.toContainText("Broker Hub");
    // the mark travels with the wordmark
    await expect(page.locator("[data-brand-mark='mantiq']").first()).toBeVisible();
  });

  test("shell: navy rail, gold active item, white canvas, Mantiq wordmark", async ({ page }) => {
    await login(page, MANAGER);
    const nav = page.getByRole("navigation", { name: "التنقل الرئيسي", exact: true });
    await expect(nav).toBeVisible();

    // the rail itself is Deep Navy
    await expect(page.locator("aside")).toHaveCSS("background-color", DEEP_NAVY);
    // and the canvas behind the cards is near-white, not beige and not the old #f3f4f9
    await expect(page.locator("body")).toHaveCSS("background-color", PAGE_WHITE);

    const lockup = page.locator("[data-brand-wordmark]").first();
    await expect(lockup).toHaveText("منطق");
    await expect(page.locator("[data-brand-mark='mantiq']").first()).toBeVisible();

    // exactly one active item, gold, with an inline-start indicator
    const active = nav.locator("a[aria-current='page']");
    await expect(active).toHaveCount(1);
    await expect(active).toHaveCSS("color", MANTIQ_GOLD);
    await expect(active.locator("[data-nav-indicator='active']")).toHaveCount(1);
    // the indicator is on the physical right in RTL
    const indicator = await active.locator("[data-nav-indicator='active']").boundingBox();
    const item = await active.boundingBox();
    expect(indicator).not.toBeNull();
    expect(item).not.toBeNull();
    expect(indicator!.x).toBeGreaterThan(item!.x + item!.width / 2);

    await expect(page.locator("body")).not.toContainText("Broker Hub");
  });

  test("the tab is Mantiq's: title, theme colour and the icon set", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveTitle(/منطق/);
    await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#0E1728");

    for (const href of ["/favicon.ico", "/favicon-32.png", "/apple-touch-icon.png", "/manifest.webmanifest"]) {
      await expect(page.locator(`link[href="${href}"]`)).toHaveCount(1);
      const response = await page.request.get(href);
      expect(response.status(), href).toBe(200);
    }
    // switching the language switches the tab title with it (the toggle lives in the staff shell header,
    // not on the login card — that is where the locale actually changes)
    await login(page, MANAGER);
    await expect(page).toHaveTitle(/منطق/);
    await page.getByRole("button", { name: "language" }).click();
    await expect(page).toHaveTitle(/^Mantiq/);
    await expect(page.locator("[data-brand-wordmark]").first()).toHaveText("Mantiq");
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  });

  test("both gold-bearing controls stay legible: no white text on a gold fill", async ({ page }) => {
    await login(page, MANAGER);
    const goldFills = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>("button, a")).filter((element) => {
        const style = getComputedStyle(element);
        return style.backgroundColor === "rgb(189, 167, 126)" && element.innerText.trim().length > 0;
      }).map((element) => getComputedStyle(element).color),
    );
    for (const color of goldFills) expect(color, "label on a Mantiq Gold fill").toBe(DEEP_NAVY);
  });
});
