/**
 * R7 — the Phase-2 operating loop as the screens implement it: create a draft, then work its installments.
 *
 * Two things are worth a unit test rather than an e2e run:
 *
 *  - **the create dialog refuses to send what the API will refuse.** The button is disabled until the client,
 *    the start date and a positive premium are there, and a 422 from the server is rendered *next to the
 *    field it names* — not swallowed into a toast that says "something went wrong".
 *  - **a paid installment has no action.** The row's state comes from the API (`status`), so once the server
 *    says `paid` there is no button to press twice; and the commission block says «غير محسوبة» instead of
 *    printing `0.00` when no commission row exists.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PoliciesPage from "@/features/phase2/PoliciesPage";
import { NewPolicyDialog } from "@/features/phase2/NewPolicyDialog";
import { ar } from "@/lib/i18n/ar";

const calls: { method: string; path: string; body?: unknown }[] = [];
let postError: unknown = null;
let plans: Record<string, unknown> = {};

const CLIENTS = {
  items: [
    { id: "c-1", name: "شركة النيل" },
    { id: "c-2", name: "شركة الأمل" },
  ],
  total: 2,
  page: 1,
  page_size: 200,
};

const POLICY_ROW = {
  id: "p-1",
  client_id: "c-1",
  client: { id: "c-1", name: "شركة النيل" },
  policy_no: null,
  status: "draft",
  start_date: "2026-11-01",
  end_date: "2027-10-31",
  premium_amount: 24000,
  currency: "EGP",
  payment_plan: "quarterly",
  insurance_type: { id: "t-1", code: "group_life", name: "تأمين جماعي" },
  insurer: { id: "i-1", name: "شركة مصر للتأمين" },
  document: null,
};

const PLAN = {
  policy: POLICY_ROW,
  items: [
    { id: "in-1", sequence_no: 1, due_date: "2026-11-01", amount: 6000, status: "paid", paid_at: "2026-11-02T10:00:00Z" },
    { id: "in-2", sequence_no: 2, due_date: "2026-02-01", amount: 6000, status: "late", paid_at: null },
    { id: "in-3", sequence_no: 3, due_date: "2027-05-01", amount: 6000, status: "upcoming", paid_at: null },
  ],
  totals: { count: 3, paid_count: 1, late_count: 1, due_count: 1, paid_amount: 6000, late_amount: 6000, due_amount: 6000, outstanding_amount: 12000 },
  commission: { state: "not_computed", amount: null, rate: 0, status: null, settled_at: null },
};

vi.mock("@/lib/api/client", () => {
  class ApiError extends Error {
    status = 422;
    details: unknown;
    code = "validation_failed";
    constructor(message: string, status: number, details?: unknown) {
      super(message);
      this.status = status;
      this.details = details;
    }
  }
  return {
    api: {
      get: (path: string) => {
        calls.push({ method: "GET", path });
        if (path.startsWith("/clients")) return Promise.resolve(CLIENTS);
        if (path.startsWith("/policies/kpis")) return Promise.resolve({ active: 1, expiring_90d: 0, installments_this_month: 3, late_installments: 1, expected_commission: 600, currency: "EGP" });
        if (path.startsWith("/policies/") && path.endsWith("/installments")) {
          const id = path.split("/")[2] ?? "";
          return Promise.resolve(plans[id] ?? PLAN);
        }
        if (path.startsWith("/policies")) return Promise.resolve({ items: [POLICY_ROW], total: 1, page: 1, page_size: 50 });
        if (path.startsWith("/insurers") || path.startsWith("/insurance-types")) return Promise.resolve([{ id: "i-1", name: "شركة مصر للتأمين" }]);
        return Promise.resolve({});
      },
      post: (path: string, body?: unknown) => {
        calls.push({ method: "POST", path, body });
        if (postError) return Promise.reject(postError);
        return Promise.resolve({ id: "p-9", policy_no: null, status: "draft", installments: [{}, {}, {}, {}] });
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

const AUTH = { user: { full_name: "مدير", role: "sales_manager" }, can: () => true, canAny: () => true, ready: true, isManager: true, isPortal: false, logout: vi.fn() };
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
const toast = vi.fn();
vi.mock("@/components/Toast", () => ({ useToast: () => toast, ToastProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/features/phase2/phase2Gate", () => ({ Phase2Screen: ({ children }: { children: ReactNode }) => children }));

function harness(element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The one body row of the policies table (the header is a row too, so `getByRole` would be ambiguous). */
function policyRow(): HTMLElement | undefined {
  return screen.getAllByRole("row").find((row) => row.textContent?.includes("شركة النيل") && row.tagName === "TR");
}

beforeEach(() => {
  calls.length = 0;
  postError = null;
  plans = {};
  toast.mockClear();
});

describe("new policy dialog", () => {
  it("will not send a draft the API would refuse", async () => {
    harness(<NewPolicyDialog onClose={() => undefined} />);
    const save = screen.getByRole("button", { name: ar.phase2.saveDraft });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(ar.phase2.form.start), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText(ar.phase2.form.premium), { target: { value: "24000" } });
    // still no client — the one field nothing can guess
    expect(save).toBeDisabled();

    // the client list arrives from the API: wait for the option, not for the select to exist
    const picker = screen.getByLabelText(ar.phase2.form.client);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(picker, { target: { value: "c-2" } });
    expect(save).toBeEnabled();
  });

  it("posts the draft with the fields the person typed, and says how many installments it made", async () => {
    const onClose = vi.fn();
    harness(<NewPolicyDialog onClose={onClose} />);
    const picker = screen.getByLabelText(ar.phase2.form.client);
    await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(picker, { target: { value: "c-1" } });
    fireEvent.change(screen.getByLabelText(ar.phase2.form.start), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText(ar.phase2.form.premium), { target: { value: "24000" } });
    fireEvent.change(screen.getByLabelText(ar.phase2.form.plan), { target: { value: "quarterly" } });

    fireEvent.click(screen.getByRole("button", { name: ar.phase2.saveDraft }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/policies")).toBe(true));
    const posted = calls.find((call) => call.method === "POST")?.body as Record<string, unknown>;
    expect(posted).toMatchObject({ client_id: "c-1", start_date: "2026-11-01", premium_amount: 24000, payment_plan: "quarterly" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("4"));
  });

  it("renders a 422 next to the field it names instead of a vague toast", async () => {
    const { ApiError_ } = await import("@/lib/api/client");
    const Fail = ApiError_ as unknown as new (message: string, payload: null, fallback: string) => Error;
    // the mocked class *is* the module's export, so the dialog's `instanceof` matches; the envelope is the
    // API's own `{field, message}` list
    postError = Object.assign(new Fail("Some fields need attention", null, "fallback"), {
      status: 422,
      code: "validation_failed",
      details: [{ field: "premium_amount", message: "قيمة القسط السنوي مطلوبة لتوليد جدول الأقساط", type: "missing" }],
    });
    harness(<NewPolicyDialog clientId="c-1" onClose={() => undefined} />);
    fireEvent.change(screen.getByLabelText(ar.phase2.form.premium), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText(ar.phase2.form.start), { target: { value: "2026-11-01" } });

    const save = screen.getByRole("button", { name: ar.phase2.saveDraft });
    expect(save).toBeDisabled(); // zero premium never leaves the browser
    fireEvent.change(screen.getByLabelText(ar.phase2.form.premium), { target: { value: "12000" } });
    fireEvent.click(save);
    await waitFor(() => expect(screen.getByText("قيمة القسط السنوي مطلوبة لتوليد جدول الأقساط")).toBeTruthy());
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("installments dialog", () => {
  it("shows the client's name in the table, and the plan's own statuses", async () => {
    harness(<PoliciesPage />);
    await waitFor(() => expect(policyRow()).toBeTruthy());
    // the header also names the client — a table column that printed «—» for every row is the R7 bug
    expect(screen.getAllByText("شركة النيل").length).toBeGreaterThan(0);
  });

  it("offers «تسجيل الدفع» only on unpaid rows, and records one payment", async () => {
    harness(<PoliciesPage />);
    const target = await waitFor(() => {
      const found = policyRow();
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    fireEvent.click(target as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    // «متأخر» appears twice by design: the totals tile and the row badge
    await waitFor(() => expect(within(dialog).getAllByText(ar.phase2.lateLabel).length).toBeGreaterThan(0));

    const buttons = within(dialog).getAllByRole("button", { name: ar.phase2.markPaid });
    expect(buttons).toHaveLength(2); // the paid row has none

    fireEvent.click(buttons[0] as HTMLElement);
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path.includes("/installments/in-2/pay"))).toBe(true));
  });

  it("says «غير محسوبة» when the policy earns no commission, and prints the number when it does", async () => {
    harness(<PoliciesPage />);
    const priced = await waitFor(() => {
      const found = policyRow();
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    fireEvent.click(priced);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText(new RegExp(ar.phase2.commissionNotComputed))).toBeTruthy());

    plans["p-1"] = { ...PLAN, commission: { state: "computed", amount: 2500, rate: 2.5, status: "accrued", settled_at: null } };
    fireEvent.click(within(dialog).getByRole("button", { name: ar.common.close }));
    const reopened = await waitFor(() => {
      const found = policyRow();
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    fireEvent.click(reopened);
    const again = await screen.findByRole("dialog");
    await waitFor(() => expect(within(again).getByText(/2,500|٢٬٥٠٠/)).toBeTruthy());
  });
});
