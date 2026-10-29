/**
 * Screen-level behaviour the spec calls non-negotiable: a rep must see that the board is
 * scope-limited (and must not see manager affordances), and the client portal must render none
 * of the staff-only vocabulary. Copy is read from the Arabic dictionary so the assertions test
 * behaviour, not wording.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import KanbanPage from "@/features/leads/KanbanPage";
import PortalPage from "@/features/portal/PortalPage";
import { ar } from "@/lib/i18n/ar";

const LEAD = {
  id: "lead-1",
  name: "شركة منطق للتوزيع",
  type: "company",
  status: "new",
  mobile: "+201001234567",
  contact_name: "منى فؤاد",
  headcount: 42,
  next_followup_at: "2026-10-20T09:00:00Z",
  followup_overdue: true,
  insurance_type: "تأمين طبي",
  insurer: "أكسا",
  assignee_name: "سارة",
};

const ROUTES: Record<string, unknown> = {
  "/leads/board": {
    columns: [
      { status: "new", count: 1, items: [LEAD] },
      { status: "contacted", count: 0, items: [] },
      { status: "meeting_scheduled", count: 0, items: [] },
      { status: "quote_sent", count: 0, items: [] },
      { status: "won", count: 0, items: [] },
    ],
  },
  "/leads/stats": {
    my_leads: 12,
    new_week: 3,
    meetings_week: 2,
    meetings_today: 1,
    followups_overdue: 4,
    converted_month: 1,
    conversion_rate_pct: 8.3,
  },
  "/leads": { items: [LEAD], total: 1, page: 1, page_size: 50 },
  "/lost-reasons": [{ id: "r1", name: "السعر" }],
  "/portal/overview": {
    client: { id: "c1", name: "شركة منطق للتوزيع", type: "company", since_date: "2024-02-01" },
    policy_summary: {
      policy_no: "GRP-2026-43240",
      insurer: "أكسا",
      insurance_type: "تأمين طبي",
      end_date: "2027-03-31",
      payment_plan: "quarterly",
    },
    kpis: {
      requests_this_month: 6,
      avg_response_minutes: 42,
      premiums: { total: 1000, paid: 750, overdue: 0, next_due: { due_date: "2026-11-15", amount: 250 } },
    },
    covered_count: 128,
    renewal_in_days: 156,
  },
  "/portal/requests": {
    items: [
      {
        id: "q1",
        number: 2841,
        subject: "إضافة مولود جديد",
        status: "in_progress",
        channel: "whatsapp",
        created_at: "2026-10-26T09:00:00Z",
        messages: [{ id: "m1", direction: "out", channel: "whatsapp", body: "تم الرفع للشركة", created_at: "2026-10-26T09:30:00Z" }],
      },
    ],
    total: 1,
    page: 1,
    page_size: 10,
  },
  "/portal/policies": [
    {
      id: "p1",
      policy_no: "GRP-2026-43240",
      status: "issued",
      insurance_type: "تأمين طبي",
      insurer: "أكسا",
      start_date: "2026-04-01",
      end_date: "2027-03-31",
      premium_amount: 480000,
      currency: "EGP",
      document: {
        filename: "GRP-2026-43240.pdf",
        size: 40960,
        created_at: "2026-04-02T09:00:00Z",
        download_url: "/api/v1/portal/policies/p1/document",
      },
    },
  ],
  "/portal/documents": [
    {
      id: "d1",
      filename: "بطاقة التأمين.pdf",
      mime: "application/pdf",
      size: 20480,
      created_at: "2026-10-20T09:00:00Z",
      request_number: 2841,
      download_url: "/api/v1/portal/attachments/d1",
    },
  ],
};

vi.mock("@/lib/api/client", () => {
  const call = (path: string) => {
    const key = Object.keys(ROUTES).find((route) => path.startsWith(route));
    return Promise.resolve(key ? ROUTES[key] : {});
  };
  class ApiError extends Error {
    status = 400;
  }
  return {
    api: {
      get: (path: string) => call(path),
      post: () => Promise.resolve({}),
      patch: () => Promise.resolve({}),
      put: () => Promise.resolve({}),
      upload: () => Promise.resolve({}),
    },
    ApiError_: ApiError,
    duplicateInfo: () => ({ leadId: null, name: null, hidden: false, message: "duplicate" }),
    onUnauthorized: () => () => undefined,
  };
});

const AUTH = {
  user: { full_name: "سارة", role: "sales_rep", permissions: ["leads.view_own", "leads.convert"] },
  isManager: false,
  isPortal: false,
  can: () => false,
  canAny: () => true,
  ready: true,
  logout: vi.fn(),
};
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/components/Toast", () => ({
  useToast: () => vi.fn(),
  ToastProvider: ({ children }: { children: ReactNode }) => children,
}));

function harness(element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("screen 3 · rep Kanban", () => {
  it("states that the board is scope-limited and hides the manager view", async () => {
    const { container } = harness(<KanbanPage />);
    expect(await screen.findByText(ar.leads.isolationBanner)).toBeInTheDocument();
    expect(screen.queryByText(ar.leads.managerView)).not.toBeInTheDocument();
    const card = await screen.findByText("شركة منطق للتوزيع");
    expect(card).toBeInTheDocument();
    // overdue follow-up is surfaced on the card, not only in the KPI counter
    expect(card.closest("article")?.textContent).toContain(ar.leads.followupLate);
    expect(container.querySelectorAll("article")).toHaveLength(1);
  });

  it("renders the KPI hint lines that the design sheet specifies", async () => {
    harness(<KanbanPage />);
    await waitFor(() => expect(screen.getByText(`+3 ${ar.leads.newWeek.replace("+{n}", "").trim()}`)).toBeInTheDocument());
    expect(screen.getByText(ar.leads.conversionRate.replace("{n}", "8.3"))).toBeInTheDocument();
  });
});

describe("screen 7 · client portal", () => {
  it("shows the owner their own data and none of the staff vocabulary", async () => {
    const { container } = harness(<PortalPage />);
    expect(await screen.findByText("شركة منطق للتوزيع")).toBeInTheDocument();
    const text = container.textContent ?? "";
    // staff-only vocabulary must be absent, not merely hidden
    expect(text).not.toContain(ar.inbox.internalNote);
    expect(text.toLowerCase()).not.toContain("عمولة");
    for (const label of [ar.inbox.assignee, ar.inbox.employee, ar.inbox.voiceNote]) {
      expect(screen.queryByText(label, { exact: true })).not.toBeInTheDocument();
    }
    // the issued policy and its file are visible to the owner — and the download is a real button, not a
    // bare <a href> that would arrive at the API without a bearer token
    expect(text).toContain("GRP-2026-43240");
    expect(screen.getByLabelText(ar.common.download)).toBeInTheDocument();
    // the request thread and the covered head count are the owner's own business
    expect(text).toContain("إضافة مولود جديد");
    expect(text).toContain("128");
    expect(text).toContain("أكسا");
  });
});
