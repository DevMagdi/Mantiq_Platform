/** ERP hub (ADR-031): one sidebar money item, period switching updates KPIs, cashbox disclaimer. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaffShell } from "@/app/StaffShell";
import ErpPage from "@/features/finance/ErpPage";
import CashboxPage from "@/features/finance/CashboxPage";
import { ar } from "@/lib/i18n/ar";
import { formatMoney } from "@/lib/format";

const calls: string[] = [];
const SPEND: Record<string, unknown> = {
  month: { currency: "EGP", period: "month", label: "2026-10", start: "2026-10-01", end: "2026-10-31", total_in: 10000, total_out: 3000, net: 7000,
    by_type: [{ entry_type: "premium_receivable", in_total: 10000, out_total: 0, net: 10000 }, { entry_type: "expense", in_total: 0, out_total: 3000, net: -3000 }] },
  year: { currency: "EGP", period: "year", label: "2026", start: "2026-01-01", end: "2026-12-31", total_in: 120000, total_out: 40000, net: 80000,
    by_type: [{ entry_type: "premium_receivable", in_total: 120000, out_total: 0, net: 120000 }] },
};
const CASHBOX = { currency: "EGP", as_of: "2026-10-28T00:00:00Z", is_net_movements_only: true, balance_note: "net_movements_only",
  balance: 7000, cash_in_total: 10000, cash_out_total: 3000, today_in: 0, today_out: 0, month_in: 10000, month_out: 3000, by_method: [], movements: [] };

vi.mock("@/lib/api/client", () => {
  class ApiError extends Error { status = 0; }
  return {
    ApiError_: ApiError, onUnauthorized: () => () => undefined, getAccessToken: () => "t", setAccessToken: () => undefined,
    api: {
      get: (path: string) => {
        calls.push(path);
        if (path.startsWith("/finance/spend-by-period")) {
          const period = new URLSearchParams(path.split("?")[1]).get("period") ?? "month";
          return Promise.resolve(SPEND[period] ?? SPEND.month);
        }
        if (path.startsWith("/finance/cashbox")) return Promise.resolve(CASHBOX);
        if (path.startsWith("/requests")) return Promise.resolve({ total: 0 });
        return Promise.resolve({});
      },
      post: () => Promise.resolve({}), patch: () => Promise.resolve({}), put: () => Promise.resolve({}),
    },
  };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { full_name: "مدير", role: "sales_manager", permissions: ["finance.view", "reports.executive", "requests.view"], scope_mode: "all" },
    logout: vi.fn(), can: () => true, canAny: () => true, ready: true }),
}));
vi.mock("@/lib/realtimeProvider", () => ({ useRealtime: () => ({ status: "live", revision: 1 }) }));
vi.mock("@/components/Toast", () => ({ useToast: () => vi.fn(), ToastProvider: ({ children }: { children: ReactNode }) => children }));

function harness(el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{el}</MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { calls.length = 0; });

describe("ERP hub", () => {
  it("shows KPIs and breakdown, and switching period refetches and updates them", async () => {
    harness(<ErpPage />);
    await waitFor(() => expect(screen.getByText(ar.erp.title)).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText(formatMoney(10000)).length).toBeGreaterThan(0));
    expect(screen.getByText(ar.finance.entryType.expense)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: ar.erp.periods.year }));
    await waitFor(() => expect(screen.getAllByText(formatMoney(120000)).length).toBeGreaterThan(0));
    expect(calls.some((p) => p.includes("period=year"))).toBe(true);
  });

  it("module cards deep-link to the five existing screens", async () => {
    harness(<ErpPage />);
    await waitFor(() => expect(screen.getByText(ar.erp.title)).toBeInTheDocument());
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    for (const to of ["/finance/ledger", "/finance/ar", "/finance/commissions", "/finance/cashbox", "/executive"])
      expect(hrefs).toContain(to);
  });

  it("cashbox shows the net-movements-only disclaimer", async () => {
    harness(<CashboxPage />);
    await waitFor(() => expect(screen.getByText(new RegExp("صافي الحركات"))).toBeInTheDocument());
  });

  it("sidebar has exactly one money item: ERP المحاسبي", async () => {
    harness(<StaffShell><div>child</div></StaffShell>);
    await waitFor(() => expect(screen.getAllByText(ar.nav.erp).length).toBeGreaterThan(0));
    expect(screen.queryByText(ar.nav.ledger)).toBeNull();
    expect(screen.queryByText(ar.nav.cashbox)).toBeNull();
    expect(screen.queryByText(ar.nav.ar)).toBeNull();
    expect(screen.queryByText(ar.nav.commissions)).toBeNull();
  });
});
