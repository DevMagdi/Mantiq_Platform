/**
 * The nav model without JSX, so the rule can be tested without a browser (ADR-014).
 *
 * Two defects shared one root cause: the sidebar rendered a design-sheet value instead of the
 * session's reality. A hardcoded `24` promised open requests to roles whose API answers 403 for the
 * inbox, and every role was offered screens it cannot load. The shell may show only what the caller's
 * permissions and data support — so both rules live here as pure functions.
 */

export interface NavItemModel {
  to: string;
  /** Any one of these permissions opens the item (a rep holds `leads.view_own`, a manager `leads.view_all`). */
  perms?: string[];
  /**
   * Every one of these is required. `GET /sales/team` checks two permissions, and a nav item that
   * appeared on the strength of one of them would still answer 403 — the point of this model is that
   * an item is shown only when its own route would succeed.
   */
  permsAll?: string[];
  /** The only badge the shell may render, and only for someone who can read that list. */
  showOpenBadge?: boolean;
}

export function navItemsFor<T extends { perms?: string[]; permsAll?: string[] }>(items: readonly T[], permissions: readonly string[]): T[] {
  return items.filter(
    (item) =>
      (!item.perms || item.perms.some((perm) => permissions.includes(perm))) &&
      (!item.permsAll || item.permsAll.every((perm) => permissions.includes(perm))),
  );
}

/** A real count or nothing at all. Above 99 it collapses, because a wide pill is its own bug. */
export function openBadge(total: number | null | undefined): string | null {
  if (!total || total <= 0) return null;
  return total > 99 ? "99+" : String(total);
}
