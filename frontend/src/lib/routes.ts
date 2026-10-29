/**
 * The staff path list, in sidebar order. `tests/settingsView.test.ts` asserts it against the `<Route>`
 * elements in `App.tsx`, in both directions — a nav entry that points at an unregistered path renders an
 * empty shell, and a registered path nobody links to is a screen that no role can reach.
 */
export const STAFF_ROUTES = [
  "/dashboard",
  "/inbox",
  "/leads",
  "/leads/new",
  "/sales-team",
  "/clients",
  "/settings",
  "/policies",
  "/reports",
  "/issuing",
  "/executive",
  "/finance/ledger",
  "/finance/ar",
  "/finance/commissions",
  "/finance/cashbox",
] as const;
