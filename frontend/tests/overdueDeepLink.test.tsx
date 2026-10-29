/**
 * The overdue deep link (spec §R6 item 1): the KPI card is a real link, and the screen it opens asks the API
 * for exactly the rows the card counted.
 *
 * The two assertions that matter:
 *  - the card is an `<a href>` to `/inbox?filter=overdue` with an accessible name — keyboard users and the
 *    browser's own "open in new tab" both work, and a pasted URL opens the same view;
 *  - the inbox reads that URL and sends `only_overdue=true` to the API instead of filtering a fetched page
 *    in the browser (which would show "7" over a list of 40).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/features/dashboard/DashboardPage";
import InboxPage from "@/features/inbox/InboxPage";
import { ar } from "@/lib/i18n/ar";
import { formatNumber } from "@/lib/format";

const calls: { path: string; query?: Record<string, unknown> }[] = [];

const SUMMARY = {
  requests_today: 12,
  requests_yesterday: 10,
  open_requests: 31,
  overdue: 7,
  sla_compliance_pct: 92.5,
  avg_first_response_minutes: 34,
  avg_first_response_delta_minutes: 3,
  clients_total: 8,
};

const EMPTY_PAGE = { items: [], total: 0, page: 1, page_size: 40 };
/** Flipped by the last test: a queue with one overdue row, so the screen's auto-select actually runs. */
let queuePage: typeof EMPTY_PAGE = EMPTY_PAGE;

vi.mock("@/lib/api/client", () => {
  class ApiError extends Error {
    status = 400;
  }
  return {
    api: {
      get: (path: string, query?: Record<string, unknown>) => {
        calls.push({ path, query });
        if (path.startsWith("/dashboard/summary")) return Promise.resolve(SUMMARY);
        if (path.startsWith("/dashboard/channels")) return Promise.resolve([]);
        if (path.startsWith("/dashboard/status-breakdown")) return Promise.resolve([]);
        if (path.startsWith("/dashboard/recent-requests")) return Promise.resolve([]);
        if (path.startsWith("/requests")) return Promise.resolve(queuePage);
        return Promise.resolve({});
      },
      post: () => Promise.resolve({}),
      patch: () => Promise.resolve({}),
      put: () => Promise.resolve({}),
      upload: () => Promise.resolve({}),
    },
    ApiError_: ApiError,
    onUnauthorized: () => () => undefined,
    getAccessToken: () => "token",
    setAccessToken: () => undefined,
  };
});

const AUTH = {
  user: { full_name: "مدير", role: "sales_manager", permissions: ["requests.view", "requests.manage", "leads.view_all"] },
  isManager: true,
  isPortal: false,
  can: () => true,
  canAny: () => true,
  ready: true,
  logout: vi.fn(),
};
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/components/Toast", () => ({
  useToast: () => vi.fn(),
  ToastProvider: ({ children }: { children: ReactNode }) => children,
}));

/** Where the router thinks it is — the whole point of a deep link is the address it leaves behind. */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{`${location.pathname}${location.search}`}</span>;
}

function harness(element: ReactNode, initialEntries: string[] = ["/dashboard"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        {element}
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  queuePage = EMPTY_PAGE;
});

describe("dashboard → overdue queue", () => {
  it("makes the overdue KPI a keyboard-reachable link to the filtered queue", async () => {
    harness(<DashboardPage />);
    const link = await screen.findByTestId("kpi-overdue-link");

    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/inbox?filter=overdue");
    // the number is on the card and in the accessible name, so a screen reader user hears what it counts
    await waitFor(() => expect(link).toHaveTextContent(formatNumber(7)));
    expect(link.getAttribute("aria-label")).toContain(ar.dashboard.overdueAria.replace("{n}", formatNumber(7)));
    // an anchor is focusable by default — no manual tabIndex, no click handler standing in for a link
    link.focus();
    expect(document.activeElement).toBe(link);
  });

  it("does not turn the other cards into links", async () => {
    harness(<DashboardPage />);
    await screen.findByTestId("kpi-overdue-link");
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
});

describe("inbox · ?filter=overdue", () => {
  it("asks the API for overdue rows only, from the URL", async () => {
    harness(<InboxPage />, ["/inbox?filter=overdue"]);
    await waitFor(() => expect(calls.some((call) => call.path.startsWith("/requests"))).toBe(true));
    const request = calls.find((call) => call.path.startsWith("/requests"));
    expect(request?.query).toMatchObject({ only_overdue: true });
    expect(request?.query?.only_open).toBeUndefined();
  });

  it("shows the overdue-specific empty state and the way back to the queue", async () => {
    harness(<InboxPage />, ["/inbox?filter=overdue"]);
    expect(await screen.findByText(ar.inbox.emptyOverdue)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ar.inbox.showAll })).toBeInTheDocument();
  });

  it("defaults to the open queue when the URL says nothing", async () => {
    harness(<InboxPage />, ["/inbox"]);
    await waitFor(() => expect(calls.some((call) => call.path.startsWith("/requests"))).toBe(true));
    const request = calls.find((call) => call.path.startsWith("/requests"));
    expect(request?.query).toMatchObject({ only_open: true });
    expect(screen.queryByText(ar.inbox.emptyOverdue)).not.toBeInTheDocument();
  });

  it("keeps ?filter=overdue when it opens the first row — the deep link must not erase itself", async () => {
    queuePage = {
      items: [
        {
          id: "11111111-1111-1111-1111-111111111111",
          number: 42,
          subject: "طلب متأخر",
          status: "new",
          is_overdue: true,
          employee_name: "سارة",
          contact_name: "عميل",
          channel: "whatsapp",
          last_activity_at: "2026-10-20T09:00:00Z",
          sla_due_at: "2026-10-21T09:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 40,
    } as typeof EMPTY_PAGE;

    harness(<InboxPage />, ["/inbox?filter=overdue"]);
    // the queue selects the first overdue row on its own, and that selection is what used to wipe the filter
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("/inbox/11111111-1111-1111-1111-111111111111"));
    expect(screen.getByTestId("location").textContent).toBe("/inbox/11111111-1111-1111-1111-111111111111?filter=overdue");
    // and every list read that followed still asked for overdue rows only
    const listCalls = calls.filter((call) => call.path === "/requests");
    expect(listCalls.length).toBeGreaterThan(0);
    for (const call of listCalls) expect(call.query).toMatchObject({ only_overdue: true });
  });

  it("names the count for assistive tech", async () => {
    harness(<InboxPage />, ["/inbox?filter=overdue"]);
    await waitFor(() => expect(screen.getByTestId("queue-count")).toBeInTheDocument());
    expect(screen.getByTestId("queue-count").getAttribute("aria-label")).toContain(formatNumber(0));
  });
});
