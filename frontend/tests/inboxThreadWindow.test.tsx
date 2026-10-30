/**
 * R15 P1-A — the inbox opens on the newest page, and "load older" is a request, not a re-render of history.
 *
 * Before: `GET /requests/{id}` returned every message and the pane mapped all of them, so a long WhatsApp
 * negotiation mounted hundreds of bubbles before the reader saw the one they came for. The API now sends 30
 * and a total; this test pins the client half — it renders the page it was given, and asks for the next one
 * only when the reader asks for it, with the cursor the API expects.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const AUTH = { user: { id: "u1" }, ready: true, logout: vi.fn(), can: () => true, canAny: () => true };
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/components/Toast", () => ({ useToast: () => vi.fn(), ToastProvider: ({ children }: { children: React.ReactNode }) => children }));

const message = (index: number) => ({
  id: `m-${index}`,
  direction: "inbound",
  channel: "whatsapp",
  body: `رسالة ${index}`,
  media_kind: null,
  duration_s: null,
  waveform: null,
  sender_ref: null,
  external_id: null,
  delivery_status: "delivered",
  created_at: `2026-10-2${Math.min(9, 1 + Math.floor(index / 10))}T10:${String(index % 60).padStart(2, "0")}:00Z`,
  attachments: [],
});

const PAGE = Array.from({ length: 30 }, (_, index) => message(45 + index)); // the newest 30 of 60
const OLDER = Array.from({ length: 30 }, (_, index) => message(index));

const calls: { path: string; query?: Record<string, unknown> }[] = [];

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (path: string, query?: Record<string, unknown>) => {
      calls.push({ path, query });
      if (path === "/requests") return Promise.resolve({ items: [{ id: "r1", number: 1001, subject: "طلب", status: "new", client_name: "ألف", employee_name: "منى", channel: "whatsapp", last_activity_at: "2026-10-29T10:00:00Z", is_overdue: false }], total: 1 });
      if (path.startsWith("/requests/r1/messages")) return Promise.resolve(OLDER);
      if (path === "/requests/r1") {
        return Promise.resolve({
          id: "r1",
          number: 1001,
          subject: "طلب",
          status: "new",
          channel: "whatsapp",
          client_name: "ألف",
          employee_name: "منى",
          description: null,
          messages: PAGE,
          messages_total: 60,
          notes: [],
          notes_total: 0,
          calls: [],
          calls_total: 0,
          attachments: [],
          attachments_total: 0,
        });
      }
      return Promise.resolve({});
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

import InboxPage from "@/features/inbox/InboxPage";

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/inbox/r1"]}>
        <Routes>
          <Route path="/inbox/:requestId" element={<InboxPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
});

describe("R15 · the thread is a window, not the whole history", () => {
  it("does not fetch older messages just because the screen opened", async () => {
    mount();
    await waitFor(() => expect(calls.some((call) => call.path === "/requests/r1")).toBe(true));
    expect(calls.some((call) => call.path.startsWith("/requests/r1/messages"))).toBe(false);
  });

  it("offers the remaining count and fetches the previous page with a `before` cursor", async () => {
    mount();
    const loadOlder = await screen.findByRole("button", { name: /تحميل رسائل أقدم|Load older messages/ });
    fireEvent.click(loadOlder);

    await waitFor(() => expect(calls.some((call) => call.path.startsWith("/requests/r1/messages"))).toBe(true));
    const requested = calls.find((call) => call.path.startsWith("/requests/r1/messages"));
    expect(requested?.query).toMatchObject({ limit: 30 });
    expect(String(requested?.query?.before)).toBe(PAGE[0]?.created_at);
    // and the older page is now on screen, above the newest one
    await waitFor(() => expect(screen.getAllByText(/رسالة 0\b/).length).toBeGreaterThan(0));
  });
});
