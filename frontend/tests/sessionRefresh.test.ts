/**
 * Renewing the session: no storm, no burst, no logout on a network blip.
 *
 * The bug this pins (reported from a live console): with a dead refresh cookie, `POST /auth/refresh`
 * answered 401, the client notified `onUnauthorized`, whose handler called `/auth/refresh` again… Each turn
 * is a microtask, so the tab never went idle, the console filled with identical 401s, and the UI looked
 * frozen. Measured before the fix: **one** 401 produced 6+ listener calls, and an unbounded run never
 * returned. The three cases below are the whole contract.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, onUnauthorized, setAccessToken, withoutUnauthorizedRetry } from "@/lib/api/client";
import { refreshAccessToken, resetRefreshState } from "@/lib/session";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchCalls: string[];
let authHeaders: Array<string | undefined>;

function stubFetch(handler: () => Response | Promise<Response>) {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    fetchCalls.push(String(input));
    const header = (init?.headers ?? {}) as Record<string, string>;
    authHeaders.push(header.authorization);
    return handler();
  });
}

beforeEach(() => {
  fetchCalls = [];
  authHeaders = [];
  resetRefreshState();
  setAccessToken(null);
});

afterEach(() => vi.unstubAllGlobals());

describe("the refresh storm", () => {
  it("does not ask the listeners to renew again when the renewal itself is refused", async () => {
    stubFetch(() => json(401, { code: "unauthorized", message: "expired" }));
    let listenerCalls = 0;
    const off = onUnauthorized(() => {
      listenerCalls += 1;
      // precisely what AuthProvider does; before the fix this re-entered itself forever
      void refreshAccessToken();
    });

    await refreshAccessToken();
    await new Promise((resolve) => setTimeout(resolve, 20));
    off();

    expect(listenerCalls).toBe(0);
    expect(fetchCalls.filter((url) => url.includes("/auth/refresh"))).toHaveLength(1);
  });

  it("shares one renewal between callers that hit an expired token at the same time", async () => {
    stubFetch(() => json(200, { access_token: "fresh-token", expires_in: 900 }));

    const [first, second, third] = await Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ]);

    expect([first.ok, second.ok, third.ok]).toEqual([true, true, true]);
    expect(fetchCalls.filter((url) => url.includes("/auth/refresh"))).toHaveLength(1);
  });

  it("distinguishes a refused session from an unreachable server", async () => {
    stubFetch(() => json(401, { code: "unauthorized", message: "expired" }));
    await expect(refreshAccessToken()).resolves.toEqual({ ok: false, refused: true });

    resetRefreshState();
    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(refreshAccessToken()).resolves.toEqual({ ok: false, refused: false });
  });

  it("stores the new access token so the next request is authorised", async () => {
    stubFetch(() => json(200, { access_token: "token-after-renewal", expires_in: 900 }));
    await refreshAccessToken();

    await api.get("/auth/me");

    // the second recorded request is the one that had to carry the renewed token
    expect(authHeaders.at(-1)).toBe("Bearer token-after-renewal");
  });
});

describe("withoutUnauthorizedRetry", () => {
  it("suppresses the listeners for the duration of the wrapped call only", async () => {
    stubFetch(() => json(401, { code: "unauthorized", message: "expired" }));
    const listener = vi.fn();
    const off = onUnauthorized(listener);

    await withoutUnauthorizedRetry(() => api.post("/auth/refresh")).catch(() => undefined);
    expect(listener).not.toHaveBeenCalled();

    await api.post("/leads").catch(() => undefined);
    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });

  it("restores the listeners even when the wrapped call throws", async () => {
    stubFetch(() => json(401, { code: "unauthorized", message: "expired" }));
    const listener = vi.fn();
    const off = onUnauthorized(listener);

    await withoutUnauthorizedRetry(() => api.post("/auth/refresh")).catch(() => undefined);
    // if the counter leaked, this second, ordinary 401 would be ignored
    await withoutUnauthorizedRetry(() => api.post("/auth/logout")).catch(() => undefined);
    await api.get("/auth/me").catch(() => undefined);

    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });
});
