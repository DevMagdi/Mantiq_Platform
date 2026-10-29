/**
 * The one place a possibly-missing string becomes a string.
 *
 * `Cannot read properties of undefined (reading 'toLowerCase')` is not a mystery: every screen that renders
 * server data eventually calls a string method on a field the server did not send — an older row, a partial
 * response, a provider the API has not configured yet. `asText` makes the coercion explicit and, just as
 * importantly, makes it impossible to *forget* it: the helpers below are the only sanctioned way to lowercase,
 * uppercase or compare user/API text.
 */

/** `null`/`undefined`/`NaN` → `""`; anything else → its string form. Never throws. */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return String(value);
  return "";
}

/** Lower-cased text, safe for `undefined` inputs. Use for search boxes and case-insensitive comparisons. */
export function lower(value: unknown): string {
  return asText(value).toLowerCase();
}

/** Upper-cased text, safe for `undefined` inputs (currency codes, initials). */
export function upper(value: unknown): string {
  return asText(value).toUpperCase();
}

/** Does `haystack` contain `needle`, ignoring case — the search predicate every list uses. */
export function includesText(haystack: unknown, needle: unknown): boolean {
  const term = lower(needle).trim();
  if (!term) return true;
  return lower(haystack).includes(term);
}
