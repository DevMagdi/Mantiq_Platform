/**
 * The settings hub's logic without JSX, so the rules that matter are testable without a browser —
 * the same reason `app/navModel.ts` exists (ADR-014/015/016).
 *
 * Three rules live here because each one is a promise the API also makes:
 *  1. a stored secret is *never* edited in place: the form holds a blank field meaning "leave it",
 *     an explicit removal meaning `null`, and a typed value meaning "replace" (ADR-016 §2);
 *  2. the badge in a provider card is derived from the row, never chosen by eye (ADR-016 §5);
 *  3. «تبديل إلى الحيّ» is explained by what is missing, not refused silently.
 */
import type { components } from "@/lib/api/schema";
import { asText } from "@/lib/text";

export type ProviderState = components["schemas"]["ProviderStateOut"];
export type SecretField = components["schemas"]["SecretFieldOut"];

export type ProviderKey = "whatsapp" | "telephony" | "email";
export type Preflight = components["schemas"]["PreflightOut"];

const PROVIDER_KEYS: readonly string[] = ["whatsapp", "telephony", "email"];
const PRIMARY_SECRET: Record<string, string> = { whatsapp: "access_token", telephony: "webhook_secret", email: "password" };
/* R9 §1.B: WhatsApp live also needs the App Secret (it verifies every inbound delivery). Mirrors
   `LIVE_REQUIRED_SECRETS` in `app/services/integrations_settings.py`; the server's preflight still wins. */
const ALSO_REQUIRED_SECRETS: Record<string, readonly string[]> = { whatsapp: ["app_secret"] };

export const PROVIDERS: readonly { key: ProviderKey; nameKey: string; noteKey: string }[] = [
  { key: "whatsapp", nameKey: "settings.provider.whatsapp", noteKey: "settings.provider.whatsappNote" },
  { key: "telephony", nameKey: "settings.provider.telephony", noteKey: "settings.provider.telephonyNote" },
  { key: "email", nameKey: "settings.provider.email", noteKey: "settings.provider.emailNote" },
] as const;

/** What the API prints instead of a stored secret. Anything equal to it means "untouched". */
export const MASK = "••••••••";

/** One edited secret field: blank = keep, `remove` = delete, text = replace. */
export interface SecretDraft {
  value: string;
  remove: boolean;
}

export function secretPatch(initial: readonly SecretField[], drafts: Record<string, SecretDraft>): Record<string, string | null> {
  const patch: Record<string, string | null> = {};
  for (const field of initial) {
    const draft = drafts[field.key];
    if (!draft) continue;
    if (draft.remove) {
      patch[field.key] = null;
      continue;
    }
    const typed = draft.value.trim();
    if (!typed || typed === MASK) continue; // the form echoes nothing, so nothing changes
    patch[field.key] = typed;
  }
  return patch;
}

/** True when the user actually changed a secret — so a config-only save sends no `secrets` key at all. */
export function hasSecretChanges(initial: readonly SecretField[], drafts: Record<string, SecretDraft>): boolean {
  return Object.keys(secretPatch(initial, drafts)).length > 0;
}

export type StatusKey = "live" | "ready" | "unset" | "error";

/**
 * The four badge tones of the screen, pinned to the token set: `t` live, `b` ready (configured, one switch
 * away), `r` failing, and *no* accent color for "not configured" — neutral means the `--line-2`/`--muted`
 * pair, which is why this returns `null` instead of inventing a sixth tone (ADR-016 §5).
 *
 * R9 §1.D renamed the `sim` badge to `ready`: production has three states — «غير مُعد» / «جاهز» / «Live» —
 * and "simulated" is not one of them, because a production process has no simulated driver at all.
 */
export const STATUS_BADGE: Record<StatusKey, { tone: "t" | "b" | "r" | null; labelKey: string }> = {
  live: { tone: "t", labelKey: "settings.status.live" },
  ready: { tone: "b", labelKey: "settings.status.ready" },
  unset: { tone: null, labelKey: "settings.status.unset" },
  error: { tone: "r", labelKey: "settings.status.error" },
};

export type ConnectionKey = "connected" | "not_connected" | "error";

/**
 * Connection semantics (R12 §A): the only badge an operator acts on. `connected` when the server holds
 * every required credential, `error` when the last connection test failed, otherwise `not_connected`
 * with the missing credentials named by the server (`missing_fields`). There is no "live" switch.
 */
export function connectionBadge(state: ProviderState | null | undefined): { key: ConnectionKey; tone: "t" | "b" | "r" | null; labelKey: string } {
  const status = asText(state?.connection_status);
  if (status === "connected") return { key: "connected", tone: "t", labelKey: "settings.connection.connected" };
  if (status === "error" || state?.last_test_status === "failed") return { key: "error", tone: "r", labelKey: "settings.connection.error" };
  return { key: "not_connected", tone: null, labelKey: "settings.connection.notConnected" };
}

export function statusBadge(status: string | null | undefined): { tone: "t" | "b" | "r" | null; labelKey: string } {
  // `sim` is what an un-upgraded API still answers for a configured provider — read it as «جاهز», never
  // as a badge that says the channel is simulated.
  const key = status === "live" || status === "error" ? status : status === "ready" || status === "sim" ? "ready" : "unset";
  return STATUS_BADGE[key];
}

/**
 * What the operator still has to fix before «التبديل إلى الحيّ» can succeed, in the order the server
 * checks it (ADR-016 §3): a green test, then the fields the provider cannot work without. The explicit
 * confirmation is *not* listed — the dialog always supplies it, so it is never something to fix.
 */
export function liveBlockers(state: ProviderState): ("test" | "fields")[] {
  const blockers: ("test" | "fields")[] = [];
  if (state.last_test_status !== "ok") blockers.push("test");
  if (missingLiveFields(state).length > 0) blockers.push("fields");
  return blockers;
}

/** The fields a provider needs before it may talk to customers, as the row reports them. */
export function missingLiveFields(state: ProviderState | undefined | null): string[] {
  /* Every read here is guarded: this used to run `state.provider` through a lookup and then call string
     methods on whatever came back, which is how a partially-loaded provider row became
     `Cannot read properties of undefined (reading 'toLowerCase')` instead of a card with a hint on it. */
  const provider = asText(state?.provider);
  if (!PROVIDER_KEYS.includes(provider)) return [];
  const config = (state?.config ?? {}) as Record<string, unknown>;
  const secrets = Array.isArray(state?.secrets) ? state.secrets : [];
  const missing: string[] = [];
  if (provider === "whatsapp" && !asText(config.phone_number_id).trim()) missing.push("phone_number_id");
  if (provider === "email" && !asText(config.host).trim()) missing.push("host");
  if (provider === "telephony" && !asText(config.provider_url).trim()) missing.push("provider_url");
  const required = [PRIMARY_SECRET[provider] ?? "", ...(ALSO_REQUIRED_SECRETS[provider] ?? [])].filter(Boolean);
  for (const key of required) {
    if (!secrets.some((item) => asText(item?.key) === key && item?.set === true)) missing.push(key);
  }
  return missing;
}

/**
 * The blockers, preferring the server's own answer (it knows all three providers and the environment
 * fallbacks). While the preflight is loading the local rules stand in, so the button is never enabled by
 * accident and never disabled without a reason.
 */
export function liveBlockersOf(state: ProviderState, preflight?: Preflight | null): { code: string; message: string; fields: string[] }[] {
  if (preflight && Array.isArray(preflight.blockers)) {
    return preflight.blockers.map((blocker) => ({
      code: asText(blocker?.code),
      message: asText(blocker?.message),
      fields: Array.isArray(blocker?.fields) ? blocker.fields.map(asText) : [],
    }));
  }
  const blockers: { code: string; message: string; fields: string[] }[] = [];
  if (state.last_test_status !== "ok") blockers.push({ code: "test", message: "", fields: [] });
  const missing = missingLiveFields(state);
  if (missing.length) blockers.push({ code: "fields", message: "", fields: missing });
  return blockers;
}

/** What to say when the switch comes back 409 anyway (a hidden env change, a second tab, a stale screen). */
export function liveSwitchError(error: unknown): { message: string; fields: string[] } {
  const details = (error as { details?: unknown } | undefined)?.details as
    | { blockers?: { message?: unknown }[]; labels?: unknown; missing?: unknown; hint?: unknown }
    | undefined;
  const fields = Array.isArray(details?.labels) ? details.labels.map(asText).filter(Boolean) : [];
  const first = Array.isArray(details?.blockers) ? asText(details?.blockers[0]?.message) : "";
  // a thrown `ApiError_` is an Error, a rejected fetch mock or a plain envelope object is not — both shapes
  // carry the server's Arabic message, and neither is allowed to become the string "undefined"
  const message = first || asText((error as { message?: unknown } | undefined)?.message);
  const hint = asText(details?.hint);
  return { message: hint && !message.includes(hint) ? `${message} — ${hint}` : message, fields };
}

/** `409 {details: {missing: [...]}}` → the labels to put next to the offending inputs. */
export function missingFromDetails(details: unknown): string[] {
  if (!details || typeof details !== "object") return [];
  const missing = (details as { missing?: unknown }).missing;
  return Array.isArray(missing) ? missing.filter((item): item is string => typeof item === "string") : [];
}

/** `429 {details: {retry_after_seconds}}` → a countdown the operator can act on. */
export function retryAfterSeconds(details: unknown): number | null {
  if (!details || typeof details !== "object") return null;
  const raw = (details as { retry_after_seconds?: unknown }).retry_after_seconds;
  return typeof raw === "number" && raw > 0 ? Math.ceil(raw) : null;
}

export interface SettingsTab {
  key: "integrations" | "policies" | "users";
  labelKey: string;
  /** The tab is mounted only when the caller holds all of these (`users` also needs `users.manage`). */
  permit: string[];
}

export const SETTINGS_TABS: readonly SettingsTab[] = [
  { key: "integrations", labelKey: "settings.tab.integrations", permit: ["settings.manage"] },
  { key: "policies", labelKey: "settings.tab.policies", permit: ["settings.manage"] },
  { key: "users", labelKey: "settings.tab.users", permit: ["settings.manage", "users.manage"] },
] as const;

export function visibleTabs(permissions: readonly string[]): SettingsTab[] {
  return SETTINGS_TABS.filter((tab) => tab.permit.every((perm) => permissions.includes(perm)));
}

export const PAYMENT_PLANS = ["monthly", "quarterly", "semiannual", "cash"] as const;

/**
 * Mirrors `INSTALLMENTS_BY_PLAN` in `app/services/policies.py`; `tests/settingsView.test.tsx` reads the
 * backend source and pins the copy, because a schedule the UI promises and the service does not build is
 * the same class of defect as a hardcoded badge count (ADR-015).
 */
export const INSTALLMENTS_BY_PLAN: Record<(typeof PAYMENT_PLANS)[number], number> = {
  monthly: 12,
  quarterly: 4,
  semiannual: 2,
  cash: 1,
};

/** The inbound-event rows the hub lists under the provider cards (`webhook_events`, ADR-016 §4). */
export type EventRow = components["schemas"]["WebhookEventOut"];
export type WebhookUrls = components["schemas"]["WebhookUrlsOut"];
