/**
 * The settings hub's rules, tested without a browser (ADR-014/015/016).
 *
 * Three kinds of assertion live here on purpose:
 *  - the pure logic (what a secret form may send, which badge a status gets, who may see a tab);
 *  - the drift gates: the frontend's copy of a backend table must equal the backend, and the staff route
 *    list must equal the router — mismatches of exactly this kind are what turned the last CI run red;
 *  - one render, because "a secret is never displayed" is a UI claim and must be checked in the DOM.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProviderCard } from "@/features/settings/ProviderCard";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  MASK,
  liveBlockers,
  missingFromDetails,
  missingLiveFields,
  retryAfterSeconds,
  secretPatch,
  statusBadge,
  visibleTabs,
  type ProviderState,
} from "@/features/settings/model";
import { INSTALLMENTS_BY_PLAN } from "@/features/settings/model";
import { ar } from "@/lib/i18n/ar";
import { en } from "@/lib/i18n/en";
import { STAFF_ROUTES } from "@/lib/routes";

const providerState = vi.hoisted(() => (overrides: Partial<ProviderState> = {}): ProviderState => ({
  provider: "whatsapp",
  config: { phone_number_id: "", business_account_id: "", receive_media: true },
  secrets: [
    { key: "access_token", set: true, display: MASK },
    { key: "app_secret", set: true, display: MASK },
    { key: "verify_token", set: false, display: "not_set" },
  ],
  is_live: false,
  status: "ready",
  connection_status: "not_connected",
  missing_fields: [],
  last_webhook_at: null,
  last_error: null,
  last_tested_at: null,
  last_test_status: null,
  last_test_detail: null,
  updated_at: null,
  ...overrides,
}));

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api }));
vi.mock("@/components/Toast", () => ({ useToast: () => vi.fn(), ToastProvider: ({ children }: { children: React.ReactNode }) => children }));

const ROOT = path.resolve(import.meta.dirname, "..");

describe("secret form → payload (ADR-016 §2)", () => {
  const fields = providerState().secrets;

  it("treats a blank field as ‘keep what is stored’", () => {
    expect(secretPatch(fields, { access_token: { value: "", remove: false } })).toEqual({});
  });

  it("treats the mask itself as ‘untouched’, so an echoed placeholder can never overwrite a token", () => {
    expect(secretPatch(fields, { access_token: { value: `  ${MASK}  `, remove: false } })).toEqual({});
  });

  it("sends an explicit null for a removal — the only way to delete", () => {
    expect(secretPatch(fields, { access_token: { value: "", remove: true } })).toEqual({ access_token: null });
  });

  it("sends a typed value, trimmed, and ignores fields the user never touched", () => {
    expect(secretPatch(fields, { verify_token: { value: "  new-token  ", remove: false } })).toEqual({ verify_token: "new-token" });
  });
});

describe("badges and tabs", () => {
  it("pins the four badge states to the approved tones — and to no sixth tone (ADR-016 §5)", () => {
    expect(statusBadge("live").tone).toBe("t");
    expect(statusBadge("ready").tone).toBe("b");
    // an API that still answers the pre-R9 value is read as «جاهز», never as a simulation badge
    expect(statusBadge("sim").labelKey).toBe("settings.status.ready");
    expect(statusBadge("error").tone).toBe("r");
    expect(statusBadge("unset").tone).toBeNull(); // neutral: --kanban-col/--muted, never a new accent
    expect(statusBadge(null).tone).toBeNull();
    const ui = readFileSync(path.join(ROOT, "src/components/ui.tsx"), "utf8");
    const tones = ui.match(/export type Tone = ([^;]+);/)?.[1] ?? "";
    expect(tones.replace(/\s/g, "")).toBe('"g"|"a"|"r"|"b"|"t"');
  });

  it("shows the users tab only to someone who can also manage users", () => {
    expect(visibleTabs(["settings.manage"]).map((tab) => tab.key)).toEqual(["integrations", "policies"]);
    expect(visibleTabs(["settings.manage", "users.manage"]).map((tab) => tab.key)).toEqual(["integrations", "policies", "users"]);
    expect(visibleTabs(["users.manage"])).toEqual([]);
  });
});

describe("the live switch explains itself", () => {
  it("lists the test before the fields, in the order the API checks them — and nothing once both hold", () => {
    expect(liveBlockers(providerState())).toEqual(["test", "fields"]);
    expect(liveBlockers(providerState({ last_test_status: "ok" }))).toEqual(["fields"]);
    expect(liveBlockers(providerState({ last_test_status: "ok", config: { phone_number_id: "1", business_account_id: "b", receive_media: true } }))).toEqual([]);
  });

  it("names the missing primary credential, not a vague error", () => {
    expect(missingLiveFields(providerState())).toEqual(["phone_number_id"]); // access_token is set in the fixture
    const email = providerState({
      provider: "email",
      config: { host: "", port: 1025, username: "", encryption: "none", from_name: "", from_email: "", reply_to: "" },
      secrets: [{ key: "password", set: false, display: "not_set" }],
    });
    expect(missingLiveFields(email)).toEqual(["host", "password"]);
  });

  it("reads the two detail shapes the API can answer with", () => {
    expect(missingFromDetails({ missing: ["host", "password"] })).toEqual(["host", "password"]);
    expect(missingFromDetails(undefined)).toEqual([]);
    expect(retryAfterSeconds({ retry_after_seconds: 12 })).toBe(12);
    expect(retryAfterSeconds({ retry_after_seconds: 0 })).toBeNull();
  });
});

describe("drift gates against the backend", () => {
  it("the installment counts the UI promises are the ones the service computes", () => {
    const source = readFileSync(path.join(ROOT, "../backend/app/services/policies.py"), "utf8");
    const block = source.slice(source.indexOf("INSTALLMENTS_BY_PLAN"), source.indexOf("MONTHS_PER_PLAN"));
    for (const [plan, count] of Object.entries(INSTALLMENTS_BY_PLAN)) {
      expect(block, plan).toContain(`${plan.toUpperCase()}.value: ${count}`);
    }
  });

  it("every secret field the backend masks has a label in the dictionary", () => {
    const source = readFileSync(path.join(ROOT, "../backend/app/services/integrations_settings.py"), "utf8");
    const block = source.slice(source.indexOf("PROVIDER_SECRETS"), source.indexOf("ENV_FALLBACK"));
    const quoted = [...block.matchAll(/"([a-z_]+)"/g)].map((match) => match[1] as string);
    const keys = [...new Set(quoted.filter((name) => !["whatsapp", "telephony", "email"].includes(name)))];
    expect(keys.slice().sort()).toEqual(["access_token", "api_key", "app_secret", "inbound_secret", "password", "verify_token", "webhook_secret"]);
    const labels = ar.settings.secret as unknown as Record<string, string>;
    for (const key of keys) expect(labels[key], key).toBeTruthy();
  });

  it("every config field the backend offers has a label in both dictionaries", () => {
    const source = readFileSync(path.join(ROOT, "../backend/app/services/integrations_settings.py"), "utf8");
    const block = source.slice(source.indexOf("PROVIDER_FIELDS"), source.indexOf("PROVIDER_SECRETS"));
    const found = block.matchAll(/FieldSpec\("([a-z_]+)"/g);
    const keys = [...new Set([...found].map((match) => match[1] as string))];
    // the list is short and closed on purpose: a field nobody reads is deleted, not labelled (ADR-016 §8)
    expect(keys.slice().sort()).toEqual([
      "app_id",
      "business_account_id",
      "encryption",
      "from_email",
      "from_name",
      "host",
      "number",
      "phone_number_id",
      "port",
      "provider",
      "provider_url",
      "receive_media",
      "recording_retention_days",
      "reply_to",
      "trunk",
      "username",
    ]);
    const labels = { ar: ar.settings.field as unknown as Record<string, string>, en: en.settings.field as unknown as Record<string, string> };
    for (const key of keys) {
      for (const [language, bag] of Object.entries(labels)) expect(bag[key], `${language}: ${key}`).toBeTruthy();
    }
    // and nothing is labelled that the backend no longer offers — a dead label is a dead setting's ghost
    for (const bag of Object.values(labels)) {
      for (const key of Object.keys(bag)) expect(keys, key).toContain(key);
    }
  });

  it("the staff route list equals the router", () => {
    const app = readFileSync(path.join(ROOT, "src/App.tsx"), "utf8");
    const declared = [...app.matchAll(/path="(\/[^"]*)"/g)].map((match) => match[1] as string);
    for (const route of STAFF_ROUTES) {
      expect(declared, route).toContain(route);
    }
    // the hub replaced the standalone users screen, which is now a redirect into it
    expect(declared).toContain("/settings");
    expect(app).toContain('to="/settings?tab=users"');
  });
});

describe("the card renders what the API said — and nothing it did not", () => {
  it("never paints a stored secret", async () => {
    api.get.mockResolvedValue(providerState({ secrets: [...providerState().secrets] }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: 0 }, mutations: { retry: 0 } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ProviderCard state={providerState()} webhookUrl={{ url: "http://localhost:5173/api/v1/webhooks/whatsapp", note: "Meta → WhatsApp → Configuration" }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(container.textContent).toContain(ar.settings.provider.whatsapp);
    const passwords = [...container.querySelectorAll('input[type="password"]')] as HTMLInputElement[];
    expect(passwords).toHaveLength(3);
    // a stored secret exists in the DOM only as the mask, in a placeholder — never as a value
    expect(passwords.map((input) => input.placeholder)).toEqual([MASK, MASK, ar.settings.secretNotSet]);
    expect(passwords.every((input) => input.value === "")).toBe(true);
    expect(container.innerHTML).not.toContain("wamid");
    expect(screen.getByText(ar.settings.testConnection)).toBeInTheDocument();
  });

  it("shows connection semantics only — connected / not connected (named) / error, never a live switch (R12 §A)", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: 0 }, mutations: { retry: 0 } } });
    const mount = (state: ProviderState) =>
      render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <ProviderCard state={state} webhookUrl={{ url: "http://localhost:5173/api/v1/webhooks/whatsapp", note: "" }} />
          </MemoryRouter>
        </QueryClientProvider>,
      );

    const connected = mount(providerState({ connection_status: "connected", missing_fields: [], last_test_status: "ok" }));
    expect(connected.container.textContent).toContain(ar.settings.connection.connected);
    expect(screen.queryByTestId("live-switch-whatsapp")).not.toBeInTheDocument();
    connected.unmount();

    const failed = mount(
      providerState({ connection_status: "error", last_test_status: "error", last_test_detail: "(100) invalid credentials" }),
    );
    expect(failed.container.textContent).toContain(ar.settings.connection.error);
    expect(screen.getByTestId("test-result-whatsapp")).toHaveTextContent("(100) invalid credentials");
    failed.unmount();

    // not connected: the missing credentials are named on the card — no switch to be refused by a 409
    const missing = mount(providerState({ connection_status: "not_connected", missing_fields: ["access_token", "app_secret"], last_test_status: null }));
    expect(missing.container.textContent).toContain(ar.settings.connection.notConnected);
    expect(screen.getByTestId("connection-missing-whatsapp")).toHaveTextContent("Access Token");
    expect(screen.queryByTestId("live-switch-whatsapp")).not.toBeInTheDocument();
    missing.unmount();
  });
});
