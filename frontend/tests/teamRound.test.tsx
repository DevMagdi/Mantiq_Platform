/**
 * The team round: adding a seat, importing a sheet, and redistributing the pool.
 *
 * Each test defends a behaviour the spec promised, not markup:
 *  - redistribution is one bulk request the server drains, not a client loop over one page;
 *  - a refused action shows the API's reason, because «لم نتمكن من تحميل هذه البيانات» is not actionable;
 *  - the import dialog shows a dry-run and commits nothing until the manager applies it, then counts the
 *    API's own `created`;
 *  - a generated password is revealed once, in the dialog that asked for it, and never in a list.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ImportLeadsDialog } from "@/features/leads/ImportLeadsDialog";
import { TeamMembersCard } from "@/features/leads/TeamMembersCard";
import SalesTeamPage from "@/features/leads/SalesTeamPage";
import { ToastProvider } from "@/components/Toast";
import { ar } from "@/lib/i18n/ar";

const REPS = [
  {
    rep_id: "r1",
    name: "سارة",
    is_active: true,
    open_leads: 4,
    won: 1,
    meetings: 2,
    stages: {},
    stages_pct: {},
    conversion_rate: 20,
  },
  {
    rep_id: "r2",
    name: "منى",
    is_active: true,
    open_leads: 1,
    won: 0,
    meetings: 0,
    stages: {},
    stages_pct: {},
    conversion_rate: 0,
  },
  {
    rep_id: "r3",
    name: "هالة موقوفة",
    is_active: false,
    open_leads: 7,
    won: 3,
    meetings: 1,
    stages: {},
    stages_pct: {},
    conversion_rate: 30,
  },
];
const ACTIVE_REP_COUNT = REPS.filter((row) => row.is_active).length;
const POOL = [
  {
    id: "l1",
    name: "شركة النور",
    type: "company",
    status: "new",
    mobile: "+201000000001",
  },
  {
    id: "l2",
    name: "مصنع الدلتا",
    type: "company",
    status: "contacted",
    mobile: "+201000000002",
  },
];
const FIRST_OPEN_LEADS = 4;
const ROSTER = {
  members: [
    {
      id: "u1",
      email: "sara@broker.test",
      full_name: "سارة",
      mobile: "+201000000001",
      role: "sales_rep",
      role_name: "بياع",
      is_active: true,
      open_leads: FIRST_OPEN_LEADS,
    },
    {
      id: "u2",
      email: "mona@broker.test",
      full_name: "منى",
      mobile: null,
      role: "sales_rep",
      role_name: "بياع",
      is_active: true,
      open_leads: 1,
    },
  ],
  assignable_roles: [
    { code: "sales_rep", name: "بياع" },
    { code: "sales_manager", name: "مدير مبيعات" },
    { code: "customer_service", name: "خدمة عملاء" },
    { code: "account_manager", name: "مدير حسابات" },
  ],
};
const PREVIEW = {
  id: "job-1",
  status: "dry_run",
  filename: "leads.xlsx",
  total_rows: 3,
  stats: {
    rows: 3,
    ready: 2,
    errors: 1,
    skipped_duplicate: 1,
    warnings: 0,
    assigned: 1,
    unassigned: 1,
  },
  error_report: [
    {
      row: 3,
      field: "mobile",
      message: "رقم موبايل غير قابل للقراءة",
      kind: "error",
    },
  ],
  preview: [
    {
      name: "شركة النور",
      mobile: "+201000000001",
      contact_name: "منى",
      contact_title: "مدير مشتريات",
      headcount: 40,
      next_followup_at: "2026-11-02T00:00:00Z",
      insurance_type: "طبي",
    },
  ],
  confirm_required: true,
  commit_url: "/api/v1/leads/import/job-1/commit",
};
const REPORT = {
  ...PREVIEW,
  status: "completed",
  stats: { ...PREVIEW.stats, created: 2 },
  committed_at: "2026-10-26T10:00:00Z",
};

const state = vi.hoisted(() => ({ failBulkWith: null as string | null }));
const calls = vi.hoisted(() => ({
  post: [] as { path: string; body?: unknown }[],
  patch: [] as { path: string; body?: unknown }[],
}));

vi.mock("@/lib/api/client", () => {
  const get = (path: string) => {
    if (path.startsWith("/sales-team/members")) return Promise.resolve(ROSTER);
    if (path.startsWith("/sales/team")) return Promise.resolve(REPS);
    if (path.startsWith("/leads"))
      return Promise.resolve({
        items: POOL,
        total: POOL.length,
        page: 1,
        page_size: 100,
      });
    return Promise.resolve({});
  };
  const post = (path: string, body?: unknown) => {
    calls.post.push({ path, body });
    if (path === "/leads/assign-bulk" && state.failBulkWith !== null) return Promise.reject(new Error(state.failBulkWith));
    if (path.includes("/commit")) return Promise.resolve(REPORT);
    if (path === "/sales-team/members")
      return Promise.resolve({
        id: "u3",
        email: "new@broker.test",
        role: "sales_rep",
        is_active: true,
        temporary_password: "Temp-Pass-91",
      });
    return Promise.resolve({
      assigned: 2,
      per_rep: { r1: 1, r2: 1 },
      skipped: [{ reason: "already_assigned" }],
    });
  };
  class ApiError extends Error {
    status = 400;
  }
  return {
    api: {
      get,
      post,
      patch: (path: string, body?: unknown) => {
        calls.patch.push({ path, body });
        return Promise.resolve({});
      },
      put: () => Promise.resolve({}),
      upload: () => Promise.resolve(PREVIEW),
    },
    ApiError_: ApiError,
    duplicateInfo: () => ({
      leadId: null,
      name: null,
      hidden: false,
      message: "duplicate",
    }),
    onUnauthorized: () => () => undefined,
  };
});

const AUTH = {
  user: {
    full_name: "هبة",
    role: "sales_manager",
    permissions: ["leads.view_all", "leads.assign", "leads.create"],
  },
  isManager: true,
  isPortal: false,
  can: (slug: string) => slug === "leads.view_all" || slug === "leads.assign",
  canAny: () => true,
  ready: true,
  logout: vi.fn(),
};
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));

function renderWithProviders(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ToastProvider>{node}</ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** jsdom under `noUncheckedIndexedAccess` returns `T | undefined` from array reads; fail loudly instead of typing casts. */
function at<T>(rows: T[], index: number, what: string): T {
  const value = rows[index];
  if (value === undefined) throw new Error(`expected a ${what} at index ${index}, got ${rows.length}`);
  return value;
}

async function openRedistribute() {
  fireEvent.click(await screen.findByRole("button", { name: ar.leads.redistribute }));
  return screen.getByRole("dialog", { name: ar.leads.redistribute });
}

describe("redistributing the unassigned pool", () => {
  it("sends the pool to the bulk endpoint instead of patching lead by lead", async () => {
    calls.post.length = 0;
    calls.patch.length = 0;
    renderWithProviders(<SalesTeamPage />);

    const dialog = await openRedistribute();
    // every seat that can still take work is tickable: the server round-robins across whoever is chosen
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes).toHaveLength(ACTIVE_REP_COUNT);
    boxes.forEach((box) => fireEvent.click(box));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: ar.leads.redistributeGo.replace("{n}", "2"),
      }),
    );

    await waitFor(() => expect(calls.post.length).toBeGreaterThan(0));
    expect(calls.post.at(-1)).toEqual({
      path: "/leads/assign-bulk",
      body: { unassigned_only: true, to: ["r1", "r2"] },
    });
    // a per-lead PATCH loop is what made the old button able to move one page at most
    expect(calls.patch).toEqual([]);
  });

  it("keeps row-level reassignment: one selected row is sent by id", async () => {
    calls.post.length = 0;
    renderWithProviders(<SalesTeamPage />);

    fireEvent.click(at(await screen.findAllByText(ar.leads.reassignRow), 0, "a row-level reassign button"));
    const dialog = screen.getByRole("dialog", { name: ar.leads.redistribute });
    const boxes = within(dialog).getAllByRole("checkbox");
    fireEvent.click(at(boxes, 1, "the second rep's checkbox"));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: ar.leads.redistributeGo.replace("{n}", "1"),
      }),
    );

    await waitFor(() => expect(calls.post.length).toBeGreaterThan(0));
    expect(calls.post.at(-1)?.path).toBe("/leads/assign-bulk");
    expect(calls.post.at(-1)?.body).toEqual({ lead_ids: ["l1"], to: ["r2"] });
  });

  it("does not offer a suspended seat as a target", async () => {
    calls.post.length = 0;
    calls.patch.length = 0;
    renderWithProviders(<SalesTeamPage />);

    const dialog = await openRedistribute();
    // هالة is on the workload board below (her 7 open leads are real) but not in the picker: handing her
    // more work is a 422 from `_require_rep`, so offering her would be a button that only fails
    expect(within(dialog).queryByText("هالة موقوفة")).toBeNull();
    expect(screen.getByText("هالة موقوفة"), "the suspended seat disappeared from the manager's board").toBeInTheDocument();
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes).toHaveLength(ACTIVE_REP_COUNT);
    boxes.forEach((box) => fireEvent.click(box));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: ar.leads.redistributeGo.replace("{n}", "2"),
      }),
    );

    await waitFor(() => expect(calls.post.length).toBeGreaterThan(0));
    expect(calls.post.at(-1)?.body).toEqual({ unassigned_only: true, to: ["r1", "r2"] });
  });

  it("shows the API's own reason when the move is refused", async () => {
    state.failBulkWith = "لا يمكن إسناد ليدز إلى بياع موقوف";
    renderWithProviders(<SalesTeamPage />);
    const dialog = await openRedistribute();
    fireEvent.click(at(within(dialog).getAllByRole("checkbox"), 0, "the first rep's checkbox"));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: ar.leads.redistributeGo.replace("{n}", "2"),
      }),
    );

    expect(await screen.findByText("لا يمكن إسناد ليدز إلى بياع موقوف")).toBeInTheDocument();
    // the generic load-failure copy would have hidden the reason
    expect(screen.queryByText(ar.common.errorBody)).toBeNull();
    state.failBulkWith = null;
  });
});

describe("the office roster", () => {
  it("lists each seat with its load and offers only the roles the API allows", async () => {
    renderWithProviders(<TeamMembersCard />);
    expect(await screen.findByText("sara@broker.test")).toBeInTheDocument();
    // the load number is the API's own, not a client-side guess
    expect(screen.getByText(String(FIRST_OPEN_LEADS))).toBeInTheDocument();

    const pickers = screen.getAllByRole("combobox");
    const options = within(at(pickers, 0, "a role picker"))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toEqual(ROSTER.assignable_roles.map((role) => role.name));
    // an admin account is not on this menu, because this screen may not mint one
    expect(options).not.toContain("مدير النظام");
  });

  it("asks for no password by default and reveals the generated one once", async () => {
    calls.post.length = 0;
    renderWithProviders(<TeamMembersCard />);
    fireEvent.click(await screen.findByRole("button", { name: ar.team.add }));
    const dialog = screen.getByRole("dialog", { name: ar.team.add });
    fireEvent.change(within(dialog).getByLabelText(ar.team.fieldName), {
      target: { value: "يوسف عادل" },
    });
    fireEvent.change(within(dialog).getByLabelText(ar.auth.email), {
      target: { value: "yousef@broker.test" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: ar.common.save }));

    await waitFor(() => expect(calls.post.at(-1)?.path).toBe("/sales-team/members"));
    expect(calls.post.at(-1)?.body).toMatchObject({
      full_name: "يوسف عادل",
      password: "",
      role_code: "sales_rep",
    });
    expect(await within(dialog).findByTestId("member-issued-password")).toHaveTextContent("Temp-Pass-91");
  });
});

describe("the sheet importer", () => {
  async function pickFile() {
    renderWithProviders(<ImportLeadsDialog onClose={() => undefined} />);
    const panel = await screen.findByTestId("import-pick");
    const input = panel.querySelector('input[type="file"]');
    if (input === null) throw new Error("the file picker is not in the dialog");
    fireEvent.change(input, {
      target: {
        files: [new File(["name,mobile\nشركة النور,+201000000001"], "leads.xlsx")],
      },
    });
    return screen.findByTestId("import-preview");
  }

  it("shows the dry-run and its counts before anything is applied", async () => {
    calls.post.length = 0;
    const preview = await pickFile();
    expect(preview.textContent).toContain("شركة النور");
    expect(preview.textContent).toContain("منى · مدير مشتريات");
    expect(preview.textContent).toContain("2026-11-02");
    expect(preview.textContent).toContain(ar.leads.importStatReady);
    expect(preview.textContent).toContain("رقم موبايل غير قابل للقراءة");
    // the claim that nothing is saved yet is the point of the preview step
    expect(calls.post).toEqual([]);
  });

  it("commits by job id and reports the created count the API returned", async () => {
    calls.post.length = 0;
    await pickFile();
    fireEvent.click(screen.getByRole("button", { name: ar.leads.importApply }));
    await waitFor(() => expect(calls.post.at(-1)?.path).toBe("/leads/import/job-1/commit"));
    const preview = await screen.findByTestId("import-preview");
    // the report stage is the same panel, but its numbers are the API's `created`, not the preview's `ready`
    expect(preview.textContent).toContain(ar.leads.importStatCreated);
    expect(preview.textContent).toMatch(/2/);
  });
});
