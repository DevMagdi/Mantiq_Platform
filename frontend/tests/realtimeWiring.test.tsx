/**
 * R15 P0 — the wiring, not the helper.
 *
 * `realtime.test.ts` pins `invalidateForResync` in isolation; that is not enough, because the bug was in what
 * the provider *called*: `onResync: () => queryClient.invalidateQueries()` and the same bare call on
 * `visibilitychange`. A test of the helper stays green while the provider regresses.
 *
 * So this file mounts the real `RealtimeProvider`, swaps only the network client for a controllable fake, and
 * drives the two events that used to blast the cache: a gap (`resync`) and the tab coming back from the
 * background. The assertions are about the `QueryClient`, which is the thing that fires the requests.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const AUTH = { user: { id: "u1" }, ready: true, logout: vi.fn(), can: () => true, canAny: () => true };
vi.mock("@/lib/auth", () => ({ useAuth: () => AUTH }));
vi.mock("@/lib/api/client", () => ({ getAccessToken: () => "token" }));

//: the fake client hands its options to the test, so the test can fire the exact callbacks the transport would
const captured = vi.hoisted(() => ({ options: null as null | Record<string, (...args: never[]) => unknown>, started: 0, stopped: 0, probes: 0 }));

vi.mock("@/lib/realtime", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  class FakeClient {
    constructor(options: Record<string, (...args: never[]) => unknown>) {
      captured.options = options;
    }
    start() {
      captured.started += 1;
    }
    stop() {
      captured.stopped += 1;
    }
    async checkStale() {
      captured.probes += 1;
      return false;
    }
  }
  return { ...actual, RealtimeClient: FakeClient };
});

import { RealtimeProvider } from "@/lib/realtimeProvider";

function mount() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 0, gcTime: 0 } } });
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <RealtimeProvider>
        <div>screen</div>
      </RealtimeProvider>
    </QueryClientProvider>,
  );
  return { queryClient, spy };
}

/** Every invalidation must name its roots and must only refetch what is on screen. */
function expectNarrow(spy: ReturnType<typeof vi.spyOn>) {
  expect(spy).toHaveBeenCalled();
  for (const call of spy.mock.calls) {
    const filters = call[0] as { queryKey?: unknown[]; refetchType?: string } | undefined;
    expect(filters, "a bare invalidateQueries() marks every query in the app stale").toBeDefined();
    expect(Array.isArray(filters?.queryKey)).toBe(true);
    expect(filters?.refetchType).toBe("active");
  }
}

beforeEach(() => {
  captured.options = null;
  captured.started = 0;
  captured.stopped = 0;
  captured.probes = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("R15 · a gap refetches what is on screen, not the cache", () => {
  it("onResync invalidates topic roots with refetchType active", () => {
    const { spy } = mount();
    expect(captured.options?.onResync).toBeTypeOf("function");
    act(() => {
      (captured.options!.onResync as (revision: number) => void)(9);
    });
    expectNarrow(spy);
  });
});

describe("R15 · returning to the tab asks the server", () => {
  it("visibilitychange reconnects and probes the revision instead of invalidating everything", () => {
    const { spy } = mount();
    act(() => {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(captured.probes, "the returning tab must ask /realtime/revision").toBe(1);
    expect(captured.started).toBe(2); // mount + reconnect
    expect(spy, "no invalidation on focus — the probe decides, and it found nothing").not.toHaveBeenCalled();
  });

  it("going *away* does nothing at all", () => {
    const { spy } = mount();
    act(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(captured.probes).toBe(0);
    expect(spy).not.toHaveBeenCalled();
  });
});
