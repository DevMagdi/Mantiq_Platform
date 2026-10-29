/**
 * Where a session lands after login (ADR-014).
 *
 * `/dashboard` is a request cockpit: its summary requires `requests.view`, so sending a sales rep there
 * produced four empty tiles and a 403 in the network log. Landing is therefore chosen by permission,
 * not by role name — a rep starts on the board they can actually work.
 */
export function landingPath(role: string | undefined, permissions: readonly string[] | undefined): string {
  if ((role ?? "").startsWith("client_")) return "/portal";
  if ((permissions ?? []).includes("requests.view")) return "/dashboard";
  return "/leads";
}
