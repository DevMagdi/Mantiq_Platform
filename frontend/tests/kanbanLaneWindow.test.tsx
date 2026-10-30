/**
 * R15 P1-B — a lane renders a page and says how much is behind it.
 *
 * The board used to be one `LIMIT 500` across all five stages: a campaign could fill the first lanes and leave
 * the last one empty (a lie), and the browser mounted up to 500 cards before it could scroll. The API now caps
 * each lane and reports `total`; this pins the client half — the header shows the stage's real size, and the
 * lane admits it is showing a window instead of pretending the stage has 60 cards.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const AUTH = { user: { id: "u1" }, ready: true, logout: vi.fn(), can: () => true, canAny: () => true };
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/components/Toast", () => ({ useToast: () => vi.fn(), ToastProvider: ({ children }: { children: React.ReactNode }) => children }));

const lead = (index: number) => ({
  id: `l-${index}`,
  name: `ليد ${index}`,
  type: "company",
  status: "quote_sent",
  mobile: `+2010000${String(index).padStart(4, "0")}`,
  contact_name: null,
  contact_title: null,
  headcount: null,
  next_followup_at: null,
  followup_overdue: false,
  insurance_type: null,
  insurer: null,
  assignee_name: "منى",
  assignee_id: "u2",
  last_activity_at: "2026-10-29T10:00:00Z",
  converted_client_id: null,
});

const BOARD = {
  columns: [
    { status: "new", count: 0, total: 0, items: [] },
    { status: "contacted", count: 0, total: 0, items: [] },
    { status: "meeting_scheduled", count: 0, total: 0, items: [] },
    { status: "quote_sent", count: 3, total: 214, items: [lead(1), lead(2), lead(3)] },
    { status: "won", count: 0, total: 0, items: [] },
  ],
};

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (path: string) => Promise.resolve(path.startsWith("/leads/board") ? BOARD : {}),
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
  },
  ApiError_: class ApiError extends Error {},
  onUnauthorized: () => () => undefined,
  getAccessToken: () => "token",
  setAccessToken: () => undefined,
}));

import KanbanPage from "@/features/leads/KanbanPage";

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <KanbanPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("R15 · a kanban lane is a window", () => {
  it("shows the stage's real total, not the number of cards it received", async () => {
    mount();
    const lane = await screen.findByTestId("lane-window-quote_sent");
    expect(lane.textContent).toMatch(/214/);
  });

  it("renders only the cards it was given", async () => {
    mount();
    await screen.findByText("ليد 3");
    expect(screen.queryByText("ليد 4")).not.toBeInTheDocument();
    expect(screen.getAllByText(/^ليد \d+$/).length).toBe(3);
  });
});
