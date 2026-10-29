/**
 * Screen 5 — the client file — and the two ways R3 broke it.
 *
 * 1. The page is driven by one query (`/clients/{id}/overview`). When the API refused that payload the
 *    component fell to its "not found" state and the *whole* screen disappeared — no tabs, no contract card.
 *    The e2e grid read that as "the page did not open", so the shape asserted here is the one that has to
 *    exist for every client the API answers for: the tab group named «ملف العميل» plus the contract card.
 * 2. A draft policy has no number yet (`policy_no` is nullable in the model and in `PolicyCreate`). The card
 *    must render a placeholder, not `null` — React prints nothing for `null`, which turns "وثيقة نشطة · "
 *    into a sentence that ends in a dangling separator.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import ClientFilePage from "@/features/clients/ClientFilePage";
import { ar } from "@/lib/i18n/ar";

const OVERVIEW = {
  client: {
    id: "c1",
    name: "دار الطباعة",
    type: "company",
    status: "active",
    since_date: "2026-01-01",
    converted_at: null,
    is_converted_from_lead: true,
    source_lead_id: "l1",
    account_owner_id: "u1",
    account_owner_name: "نور فهمي",
    insurance_type: "تأمين طبي",
    insurer: "أكسا",
    insured_count: 0,
    open_requests: 0,
    end_date: "2027-01-01",
  },
  contacts: [{ id: "k1", name: "منى مسؤول", title: "HR", mobile: "+201012030361", email: null, is_primary: true }],
  kpis: { insured: 12, open_requests: 0, avg_response_minutes: null, renewal_in_days: 42, calls: 0 },
  policy: {
    id: "p1",
    policy_no: null, // a draft: booked, not issued yet
    status: "draft",
    payment_plan: "monthly",
    start_date: "2026-01-01",
    end_date: "2027-01-01",
    premium_amount: 120000,
    commission_rate: 5,
  },
};

vi.mock("@/lib/api/client", () => ({
  api: {
    get: vi.fn(async (path: string) => (path.endsWith("/overview") ? OVERVIEW : { items: [], total: 0, page: 1, page_size: 6 })),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
  },
}));

function harness(element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/clients/c1"]}>
        <Routes>
          <Route path="/clients/:clientId" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("client file (screen 5)", () => {
  it("renders the tab group and the contract card for a client that has a draft policy", async () => {
    harness(<ClientFilePage />);
    await waitFor(() => expect(screen.getByRole("tablist", { name: ar.clients.file.title })).toBeInTheDocument());
    expect(screen.getByText(ar.clients.file.contract)).toBeInTheDocument();
    // the client's own name is the header, and the KPI row is painted from the API's numbers
    expect(screen.getByText("دار الطباعة")).toBeInTheDocument();
    expect(screen.getByText(ar.clients.file.kpiStaff)).toBeInTheDocument();
  });

  it("does not print a bare separator where the policy number is missing", async () => {
    harness(<ClientFilePage />);
    await waitFor(() => expect(screen.getByText(ar.clients.file.contract)).toBeInTheDocument());
    const banner = screen.getByText(new RegExp(ar.clients.activeContract));
    expect(banner.textContent, "a draft policy must show a placeholder, not an empty tail").toContain("—");
    expect(banner.textContent?.trim().endsWith("·")).toBe(false);
  });
});
