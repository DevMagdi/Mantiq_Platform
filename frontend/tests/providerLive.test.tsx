/**
 * Connection semantics (R12 §A, ADR-032): the settings card has no «تحويل إلى الوضع الحيّ» switch.
 *
 * The promises checked here are the ones the brief makes: a provider is Connected / Not connected /
 * Error; missing credentials are named on the card; saving credentials is what connects (the server
 * derives `connection_status`); and a partially-loaded row still renders instead of crashing.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProviderCard } from "@/features/settings/ProviderCard";
import { connectionBadge, statusBadge, type ProviderState } from "@/features/settings/model";
import { ar } from "@/lib/i18n/ar";

const MASK = "••••••••";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api }));
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/Toast", () => ({ useToast: () => toast, ToastProvider: ({ children }: { children: React.ReactNode }) => children }));

function state(overrides: Partial<ProviderState> = {}): ProviderState {
  return {
    provider: "whatsapp",
    config: { phone_number_id: "12345", business_account_id: "", receive_media: true },
    secrets: [
      { key: "access_token", set: true, display: MASK },
      { key: "app_secret", set: true, display: MASK },
      { key: "verify_token", set: false, display: "not_set" },
    ],
    is_live: false,
    status: "ready",
    connection_status: "connected",
    missing_fields: [],
    last_webhook_at: null,
    last_error: null,
    last_tested_at: "2026-10-27T09:00:00Z",
    last_test_status: "ok",
    last_test_detail: "green",
    updated_at: null,
    ...overrides,
  } as ProviderState;
}

function mount(provider: ProviderState) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 }, mutations: { retry: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProviderCard state={provider} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.put.mockReset();
  toast.mockReset();
});

describe("connection semantics replace the live switch", () => {
  it("a credentialed provider reads Connected, with no switch anywhere on the card", () => {
    mount(state());
    expect(screen.getByText(ar.settings.connection.connected)).toBeInTheDocument();
    expect(screen.queryByTestId("live-switch-whatsapp")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("التبديل إلى الحيّ");
  });

  it("a provider with missing credentials is Not connected and names them", () => {
    mount(state({ connection_status: "not_connected", missing_fields: ["access_token", "app_secret"] }));
    expect(screen.getByText(ar.settings.connection.notConnected)).toBeInTheDocument();
    const panel = screen.getByTestId("connection-missing-whatsapp");
    expect(panel.textContent).toContain(ar.settings.secret.access_token);
    expect(panel.textContent).toContain(ar.settings.secret.app_secret);
  });

  it("a failed connection test is an Error state with the provider's words kept", () => {
    mount(state({ connection_status: "error", last_test_status: "failed", last_test_detail: "(100) invalid credentials", last_error: "bad token" }));
    expect(screen.getByText(ar.settings.connection.error)).toBeInTheDocument();
    expect(screen.getByTestId("test-result-whatsapp")).toHaveTextContent("(100) invalid credentials");
  });

  it("the badge helper maps the three states and nothing else", () => {
    expect(connectionBadge(state()).key).toBe("connected");
    expect(connectionBadge(state({ connection_status: "not_connected" })).key).toBe("not_connected");
    expect(connectionBadge(state({ connection_status: "error" })).key).toBe("error");
    expect(connectionBadge(undefined).key).toBe("not_connected");
  });
});

describe("partial payloads never crash the card", () => {
  it("renders a provider row with no provider, no config and no secrets", () => {
    const broken = { status: undefined, config: undefined, secrets: undefined } as unknown as ProviderState;
    expect(() => mount(broken)).not.toThrow();
  });

  it("guards the badge helpers against undefined", () => {
    expect(statusBadge(undefined).labelKey).toBe("settings.status.unset");
    expect(connectionBadge(undefined).labelKey).toBe("settings.connection.notConnected");
  });
});
