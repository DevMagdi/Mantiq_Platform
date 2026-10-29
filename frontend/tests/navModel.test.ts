/**
 * The two shell rules that CI could not enforce before: an item must be loadable by this role, and a
 * pill must be a count. Both are pure now, so they are tested without a browser (ADR-014).
 */
import { describe, expect, it } from "vitest";

import { landingPath } from "@/app/landing";
import { navItemsFor, openBadge } from "@/app/navModel";

type Item = { to: string; perms?: string[]; permsAll?: string[] };

const ITEMS: Item[] = [
  { to: "/dashboard", perms: ["requests.view"] },
  { to: "/inbox", perms: ["requests.view"] },
  { to: "/leads", perms: ["leads.view_own", "leads.view_all"] },
  { to: "/sales-team", permsAll: ["leads.view_all", "leads.assign"] },
  { to: "/users", perms: ["users.manage"] },
];

const SALES_REP = ["leads.view_own", "leads.convert", "clients.view_own", "clients.manage", "insured.manage"];

const to = (items: { to: string }[]) => items.map((item) => item.to);

describe("sidebar by permission (spec §6)", () => {
  it("hides every request surface from a sales rep, because their API answers 403", () => {
    expect(to(navItemsFor(ITEMS, SALES_REP))).toEqual(["/leads"]);
  });

  it("shows the request cockpit and queue to customer service", () => {
    expect(to(navItemsFor(ITEMS, ["requests.view", "requests.manage", "calls.listen"]))).toEqual(["/dashboard", "/inbox"]);
  });

  it("requires both manager permissions for the team board, not one", () => {
    expect(to(navItemsFor(ITEMS, ["leads.view_all"]))).not.toContain("/sales-team");
    expect(to(navItemsFor(ITEMS, ["leads.view_all", "leads.assign"]))).toContain("/sales-team");
  });

  it("treats `perms` as any-of, because roles carry either the own or the all variant", () => {
    expect(to(navItemsFor(ITEMS, ["leads.view_own"]))).toContain("/leads");
    expect(to(navItemsFor(ITEMS, ["leads.view_all"]))).toContain("/leads");
    expect(navItemsFor(ITEMS, [])).toEqual([]);
  });
});

describe("the open-requests pill", () => {
  it("is rendered only from a real count", () => {
    expect(openBadge(undefined)).toBeNull();
    expect(openBadge(0)).toBeNull();
    expect(openBadge(18)).toBe("18");
    expect(openBadge(240)).toBe("99+");
  });
});

describe("landing route", () => {
  it("sends the portal to the client shell and staff by permission", () => {
    expect(landingPath("client_owner", [])).toBe("/portal");
    expect(landingPath("client_contact", ["requests.view"])).toBe("/portal");
    expect(landingPath("customer_service", ["requests.view"])).toBe("/dashboard");
    expect(landingPath("sales_rep", SALES_REP)).toBe("/leads");
    expect(landingPath(undefined, undefined)).toBe("/leads");
  });
});
