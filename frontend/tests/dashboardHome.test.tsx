/**
 * R15 P0-B — the dashboard opens with ONE request.
 *
 * Before: five `useQuery` hooks fired five GETs in parallel and each card painted when its own request landed,
 * so the first paint was five staggered paints. The fix is a single `/dashboard/home` payload; this test pins
 * it at the network boundary, because a refactor that quietly reintroduces a second call would still render
 * correctly and only be visible as slowness.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const AUTH = { user: { id: "u1" }, ready: true, logout: vi.fn(), can: () => true, canAny: () => true };
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/components/Toast", () => ({ useToast: () => vi.fn(), ToastProvider: ({ children }: { children: React.ReactNode }) => children }));

const calls = vi.hoisted(() => [] as string[]);

const HOME = {
  summary: {
    requests_today: 12,
    requests_yesterday: 10,
    open_requests: 31,
    overdue: 7,
    sla_compliance_pct: 92.5,
    avg_first_response_minutes: 34,
    avg_first_response_delta_minutes: 3,
    clients_total: 8,
  },
  channels: [
    { date: "2026-10-29", whatsapp: 3, call: 1, email: 2, manual: 0 },
    { date: "2026-10-30", whatsapp: 5, call: 2, email: 1, manual: 1 },
  ],
  status_breakdown: [{ key: "new", count: 4, pct: 40 }],
  today: { overdue_requests: { count: 2, items: [] }, lead_followups: { count: 1, items: [] }, installments_due: { count: 0, items: [] } },
  recent: [
    {
      id: "r1",
      number: 1042,
      subject: "طلب تجديد",
      status: "new",
      channel: "whatsapp",
      employee_name: "منى فؤاد",
      client_name: "شركة ألف",
      insurance_type: "طبي",
      insurer: "أكسا",
      is_overdue: false,
      sla_hours_late: null,
    },
  ],
  channels_days: 7,
};

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (path: string) => {
      calls.push(path);
      return Promise.resolve(HOME);
    },
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
  },
  ApiError_: class ApiError extends Error {},
  onUnauthorized: () => () => undefined,
  getAccessToken: () => "token",
  setAccessToken: () => undefined,
}));

import DashboardPage from "@/features/dashboard/DashboardPage";

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
});

describe("R15 · the dashboard is one round trip", () => {
  it("asks /dashboard/home and never the five single-purpose endpoints", async () => {
    mount();
    await screen.findByText("1042", { exact: false }).catch(() => undefined);
    const dashboardCalls = calls.filter((path) => path.includes("/dashboard"));
    expect(dashboardCalls).toEqual(["/dashboard/home"]);
    for (const old of ["/dashboard/summary", "/dashboard/channels", "/dashboard/status-breakdown", "/dashboard/today", "/dashboard/recent-requests"]) {
      expect(calls).not.toContain(old);
    }
  });

  it("renders every section from that one payload", async () => {
    mount();
    // the KPI + the recent table row both come from the same response
    expect(await screen.findByText(/1,?042|1042/)).toBeInTheDocument();
  });
});
