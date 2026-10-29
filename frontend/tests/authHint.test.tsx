/**
 * `AuthProvider`'s session hint (ADR-028 §1).
 *
 * The bug this pins: a tab with no session still called `POST /auth/refresh` on mount. The API answers that
 * with `403 forbidden` by design (a pinned contract — see `docs/STATUS.md` and `e2e/auth.spec.ts`), the app
 * handled the refusal correctly, and yet every visit to /login produced a browser console error. The fix is
 * not to "swallow" the error: it is to stop asking a question whose answer is known to be "no".
 *
 * The four cases below are the whole state machine: nothing to restore, something to restore, a cookie that
 * died, and a network blip (which must not sign anybody out).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth";

const post = vi.fn();
const get = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: { post: (...args: unknown[]) => post(...args), get: (...args: unknown[]) => get(...args) },
  setAccessToken: vi.fn(),
  getAccessToken: () => "token",
  onUnauthorized: () => () => undefined,
}));

function Probe() {
  const { user, ready } = useAuth();
  return <span data-testid="state">{`${ready ? "ready" : "waiting"}:${user?.email ?? "nobody"}`}</span>;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0 }, mutations: { retry: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  window.localStorage.clear();
});

describe("the session hint", () => {
  it("does not ask the API for a session that was never there", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("ready:nobody"));
    // the whole point: no request, so no 403 in the console on the login screen
    expect(post).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
  });

  it("restores the session the hint promises, and keeps the hint", async () => {
    window.localStorage.setItem("bh.session", "1");
    post.mockResolvedValue({ access_token: "fresh" });
    get.mockResolvedValue({ email: "manager@brokerhub.test", permissions: [] });
    mount();
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("ready:manager@brokerhub.test"));
    expect(post).toHaveBeenCalledWith("/auth/refresh");
    expect(window.localStorage.getItem("bh.session")).toBe("1");
  });

  it("drops the hint when the cookie is gone, and stays quiet when the network is down", async () => {
    window.localStorage.setItem("bh.session", "1");
    post.mockRejectedValue(Object.assign(new Error("forbidden"), { status: 403 }));
    mount();
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("ready:nobody"));
    expect(window.localStorage.getItem("bh.session")).toBeNull();
  });

  it("keeps the hint through a failed refresh that is not an auth refusal", async () => {
    window.localStorage.setItem("bh.session", "1");
    // a 500 or a dead socket says nothing about whether the cookie is valid — signing the user out here
    // would turn a five-second outage into a forced re-login
    post.mockRejectedValue(Object.assign(new Error("bad gateway"), { status: 502 }));
    mount();
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("ready:nobody"));
    expect(window.localStorage.getItem("bh.session")).toBe("1");
  });
});
