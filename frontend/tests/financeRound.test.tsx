/**
 * R10 — the money screens must render the API's numbers and nothing else.
 *
 * The three promises tested here are the ones a browser can get wrong even when the API is right:
 *
 *  - the executive screen prints `collections_mtd` as money and says «لا يوجد هدف» when
 *    `collections_target` is `null` — it must never divide by a target it invented;
 *  - the commissions table offers **approve** on an accrued row, **pay** on an approved one, and no action
 *    at all on a paid one (the second pay is the API's 409, and the button simply is not there);
 *  - the ledger's void control appears only for a caller holding `finance.void`, and the reason is required
 *    before it can be sent.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExecutiveDashboardPage from "@/features/executive/ExecutiveDashboardPage";
import CommissionsPage from "@/features/finance/CommissionsPage";
import CashboxPage from "@/features/finance/CashboxPage";
import LedgerPage from "@/features/finance/LedgerPage";
import { ar } from "@/lib/i18n/ar";

const calls: { method: string; path: string; body?: unknown }[] = [];
/** The two rarer powers are off by default; a test that needs them flips this instead of the mock. */
let moneyPowers = false;
const denied = (permission: string) => !moneyPowers && ["finance.void", "finance.post"].includes(permission);
const AUTH = {
  user: { full_name: "مدير", role: "sales_manager" },
  // by default this caller reads the ledger but may neither post to it nor take a row back
  can: (permission: string) => !denied(permission),
  canAny: (...permissions: string[]) => permissions.some((permission) => !denied(permission)),
  ready: true,
  isManager: true,
  isPortal: false,
  logout: vi.fn(),
};

const DASHBOARD = {
  period: { from_date: "2026-10-01", to_date: "2026-10-31", label: "2026-10" },
  kpis: {
    collections_mtd: 128500,
    collections_target: null,
    outstanding_ar: 64000,
    overdue_ar: 21000,
    commissions_accrued: 7300,
    commissions_paid_mtd: 4100,
    open_requests: 12,
    overdue_requests: 3,
    new_leads_mtd: 9,
    converted_leads_mtd: 2,
    policies_issued_mtd: 4,
    cashbox_balance: 51500,
  },
  sales_performance: [{ user_id: "u-1", name: "سارة السيد", policies_issued_mtd: 2, premium_mtd: 90000, collected_mtd: 70000 }],
  ar_aging: [
    { bucket: "current", amount: 43000, count: 4 },
    { bucket: "d0_30", amount: 12000, count: 2 },
    { bucket: "d31_60", amount: 5000, count: 1 },
    { bucket: "d61_90", amount: 2000, count: 1 },
    { bucket: "d90_plus", amount: 2000, count: 1 },
  ],
  channel_mix: [
    { channel: "whatsapp", count: 6 },
    { channel: "call", count: 3 },
    { channel: "email", count: 1 },
    { channel: "manual", count: 2 },
  ],
  renewals_next_30d: [
    { policy_id: "p-1", policy_no: "GRP-2026-1", client_name: "شركة النيل", insurer_name: "أكسا", end_date: "2026-11-10", days_left: 13, premium_amount: 240000 },
  ],
  alerts: [{ code: "overdue_ar", severity: "high", message: "مستحقات متأخرة", count: 5 }],
};

const LEDGER = {
  items: [
    {
      id: "le-1",
      entry_no: "LE-000042",
      entry_type: "premium_receivable",
      direction: "in",
      amount: 6000,
      currency: "EGP",
      status: "posted",
      client_name: "شركة النيل",
      method: "cash",
      posted_at: "2026-10-20T09:00:00Z",
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
};

const COMMISSIONS = {
  items: [
    { id: "c-1", commission_id: "c-1", status: "accrued", amount: 5000, policy_no: "GRP-1", client_name: "شركة النيل", payee_name: "سارة" },
    { id: "c-2", commission_id: "c-2", status: "approved", amount: 3000, policy_no: "GRP-2", client_name: "شركة الأمل", payee_name: "مصطفى" },
    { id: "c-3", commission_id: "c-3", status: "paid", amount: 1000, policy_no: "GRP-3", client_name: "شركة باء", payee_name: "نور", ledger_entry_no: "LE-000007" },
  ],
  total: 3,
  page: 1,
  page_size: 50,
};

const CASHBOX = {
  currency: "EGP",
  as_of: "2026-10-28T12:00:00Z",
  since: "2026-06-01T09:00:00Z",
  balance: 51500,
  cash_in_total: 240000,
  cash_out_total: 188500,
  today_in: 6000,
  today_out: 0,
  month_in: 42000,
  month_out: 12500,
  by_method: [
    { method: "cash", in_total: 240000, out_total: 188500, balance: 51500 },
    { method: "bank", in_total: 90000, out_total: 30000, balance: 60000 },
  ],
  movements: [],
};

vi.mock("@/lib/api/client", () => {
  class ApiError extends Error {
    status = 422;
  }
  return {
    api: {
      get: (path: string, params?: Record<string, unknown>) => {
        calls.push({ method: "GET", path, body: params });
        if (path.startsWith("/executive/dashboard")) return Promise.resolve(DASHBOARD);
        if (path.startsWith("/finance/ledger")) return Promise.resolve(LEDGER);
        if (path.startsWith("/finance/commissions")) return Promise.resolve(COMMISSIONS);
        if (path.startsWith("/clients")) return Promise.resolve({ items: [{ id: "cl-1", name: "شركة النيل" }], total: 1 });
        if (path.startsWith("/finance/cashbox")) return Promise.resolve(CASHBOX);
        return Promise.resolve({});
      },
      post: (path: string, body?: unknown) => {
        calls.push({ method: "POST", path, body });
        return Promise.resolve({
          entry: { ...LEDGER.items[0], status: "void" },
          cashbox_balance: 45500,
          // R10.1: a void of an installment receipt reopens the installment in the same transaction
          reopened: path.endsWith("/void") ? { kind: "installment", id: "in-1", status: "upcoming" } : null,
        });
      },
      patch: () => Promise.resolve({}),
      put: () => Promise.resolve({}),
    },
    ApiError_: ApiError,
    onUnauthorized: () => () => undefined,
    getAccessToken: () => "token",
    setAccessToken: () => undefined,
  };
});
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
const toast = vi.fn();
vi.mock("@/components/Toast", () => ({ useToast: () => toast, ToastProvider: ({ children }: { children: ReactNode }) => children }));

function harness(element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 }, mutations: { retry: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  toast.mockClear();
});

describe("executive dashboard", () => {
  it("prints the API's collections and refuses to invent a target", async () => {
    harness(<ExecutiveDashboardPage />);
    await waitFor(() => expect(screen.getByText(ar.executive.title)).toBeInTheDocument());

    // 128 500 EGP, straight from `kpis.collections_mtd`
    expect(screen.getAllByText(/128,?500/).length).toBeGreaterThan(0);
    expect(screen.getByText(ar.executive.noTarget)).toBeInTheDocument();
    // the target is null, so no percentage is printed anywhere on the screen
    expect(screen.queryByText(/%/)).toBeNull();
    expect(calls.some((call) => call.path === "/executive/dashboard")).toBe(true);
  });

  it("renders the aging row it was given, not a chart of its own", async () => {
    harness(<ExecutiveDashboardPage />);
    await waitFor(() => expect(screen.getByText(ar.executive.arAging)).toBeInTheDocument());
    expect(screen.getByText(ar.finance.bucket.d90_plus)).toBeInTheDocument();
    expect(screen.getByText("سارة السيد")).toBeInTheDocument();
    expect(screen.getByText("GRP-2026-1")).toBeInTheDocument();
  });
});

describe("commissions", () => {
  it("offers approve on accrued, pay on approved, and nothing on paid", async () => {
    harness(<CommissionsPage />);
    await waitFor(() => expect(screen.getByText("GRP-1")).toBeInTheDocument());

    expect(screen.getAllByRole("button", { name: ar.finance.approve })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: ar.finance.pay })).toHaveLength(1);

    // the paid row keeps its ledger entry visible instead of a dead button
    expect(screen.getByText("LE-000007")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: ar.finance.pay }));
    await waitFor(() => expect(calls.some((call) => call.path === "/finance/commissions/c-2/pay")).toBe(true));
  });
});

describe("ledger", () => {
  it("does not offer a void to a caller without finance.void", async () => {
    harness(<LedgerPage />);
    await waitFor(() => expect(screen.getByText("LE-000042")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: ar.finance.void })).toBeNull();
  });

  it("hides the post action without finance.post", async () => {
    harness(<LedgerPage />);
    await waitFor(() => expect(screen.getByText("LE-000042")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: ar.finance.newEntry })).toBeNull();
  });

  it("says what a void put back, so the plan is not left silently stale (R10.1)", async () => {
    moneyPowers = true;
    try {
      harness(<LedgerPage />);
      await waitFor(() => expect(screen.getByText("LE-000042")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: ar.finance.void }));
      fireEvent.change(screen.getByLabelText(ar.finance.voidReason), { target: { value: "شيك مرتجع" } });
      fireEvent.click(screen.getByRole("button", { name: ar.finance.voidConfirm }));

      await waitFor(() => expect(calls.some((call) => call.path === "/finance/ledger/le-1/void")).toBe(true));
      // the operator is told the installment went back to unpaid — not just that the money moved
      await waitFor(() => expect(toast).toHaveBeenCalledWith(ar.finance.voidedReopenedInstallment));
    } finally {
      moneyPowers = false;
    }
  });
});

describe("cashbox (R10.1 point 6)", () => {
  it("states that the balance is a net movement, with no opening balance and no daily close", async () => {
    harness(<CashboxPage />);
    await waitFor(() => expect(screen.getByText(ar.finance.cashbox)).toBeInTheDocument());

    // the sentence is rendered with the first posted cash row, so "since when" is answered on screen
    expect(screen.getByText(/لا يوجد رصيد افتتاحي/)).toBeInTheDocument();
    // …and "since when" is answered with the first posted cash row the API reported
    expect(screen.getByText(/من 01 يونيو 2026/)).toBeInTheDocument();
    // and the KPI is labelled as a movement, not as a drawer balance carried forward
    expect(screen.getByText(ar.finance.cashNetMovement)).toBeInTheDocument();
  });
});
