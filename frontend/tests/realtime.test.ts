/**
 * The change feed's client (ADR-026). Every test here is about a failure that is invisible in a happy-path
 * demo: a frame split across two TCP reads, an expired token, a half-open socket, a server that is down.
 *
 * No timers are real: the reconnect/backoff paths are driven with vitest's fake clock, because a test that
 * sleeps 8 s to see a backoff is a test nobody runs.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RealtimeClient, parseFrames, queryKeysFor, setTokenReader, type ChangeEvent } from "@/lib/realtime";

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      // deliberately left open: a real stream does not end
    },
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  vi.useFakeTimers();
  setTokenReader(() => "token-1");
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("parseFrames", () => {
  it("parses complete frames and keeps the partial tail", () => {
    const { frames, rest } = parseFrames('event: hello\ndata: {"revision":1}\n\nevent: change\ndata: {"rev');
    expect(frames).toHaveLength(1);
    expect(frames[0]).toEqual({ event: "hello", data: '{"revision":1}' });
    expect(rest).toBe('event: change\ndata: {"rev');
  });

  it("ignores heartbeat comments and the retry hint", () => {
    const { frames } = parseFrames("retry: 3000\n\n: ping\n\nevent: change\ndata: {\"revision\":2}\n\n");
    expect(frames).toEqual([{ event: "change", data: '{"revision":2}' }]);
  });

  it("tolerates CRLF framing", () => {
    const { frames } = parseFrames('event: change\r\ndata: {"revision":3}\r\n\r\n');
    expect(frames[0]).toEqual({ event: "change", data: '{"revision":3}' });
  });
});

describe("RealtimeClient", () => {
  it("delivers a change that arrives in the middle of a frame", async () => {
    const events: ChangeEvent[] = [];
    const statuses: string[] = [];
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          streamFrom(['event: hello\ndata: {"revision":4,"topics":["requests"]}\n\nevent: cha', 'nge\ndata: {"revision":5,"topic":"requests","action":"created"}\n\n']),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        ),
      );

    const client = new RealtimeClient({
      fetchImpl,
      onEvent: (event) => events.push(event),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
    });
    client.start();
    await vi.waitFor(() => expect(events).toHaveLength(1));

    expect(events[0]).toMatchObject({ revision: 5, topic: "requests", action: "created" });
    expect(statuses).toContain("live");
    expect(client.lastRevision).toBe(5);
    client.stop();
  });

  it("sends the bearer token and resumes from the last revision", async () => {
    const fetchImpl = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response(streamFrom(['event: hello\ndata: {"revision":11}\n\n']), { status: 200 })));
    const client = new RealtimeClient({ fetchImpl, onEvent: vi.fn(), onResync: vi.fn() });
    client.start();
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/realtime/stream?since=0");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer token-1");
    expect((init.headers as Record<string, string>).accept).toBe("text/event-stream");
    client.stop();
  });

  it("refreshes the token once on 401 and reconnects instead of going quiet", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockImplementation(() => Promise.resolve(new Response(streamFrom(['event: hello\ndata: {"revision":1}\n\n']), { status: 200 })));

    const statuses: string[] = [];
    const client = new RealtimeClient({
      fetchImpl,
      refresh,
      onEvent: vi.fn(),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
    });
    client.start();
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    await vi.runOnlyPendingTimersAsync();
    await vi.waitFor(() => expect(statuses).toContain("live"));
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    client.stop();
  });

  it("stops for good when the refresh is refused", async () => {
    const client = new RealtimeClient({
      fetchImpl: vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
      refresh: vi.fn().mockResolvedValue(false),
      onEvent: vi.fn(),
      onResync: vi.fn(),
    });
    client.start();
    await vi.waitFor(() => expect(client.currentStatus).toBe("offline"));
  });

  it("gives up instead of hammering a server that keeps refusing the stream", async () => {
    /*
     * The 401 path renews and immediately reconnects with zero delay — correct once, fatal in a loop.
     * A server that mints a token it then rejects would spin the tab at full speed forever, so the
     * retry is capped: after two renewals the client stops and leaves the screen honest.
     */
    const refresh = vi.fn().mockResolvedValue(true);
    const fetchImpl = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));
    const client = new RealtimeClient({
      fetchImpl,
      refresh,
      onEvent: vi.fn(),
      onResync: vi.fn(),
    });
    client.start();
    await vi.waitFor(() => expect(client.currentStatus).toBe("offline"), { timeout: 2_000 });
    await vi.runOnlyPendingTimersAsync();
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(fetchImpl).toHaveBeenCalledTimes(3); // first attempt + two renewed retries, then stop
  });

  it("falls back to polling and still reports the change", async () => {
    // 1 stream attempt fails, then the revision endpoint reports a newer `leads`
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockRejectedValueOnce(new Error("network down"))
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue(jsonResponse({ revision: 9, topics: { leads: 9 } }));

    const events: ChangeEvent[] = [];
    const statuses: string[] = [];
    const client = new RealtimeClient({
      fetchImpl,
      onEvent: (event) => events.push(event),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
      pollIntervalMs: 1_000,
    });
    client.start();

    for (let i = 0; i < 6; i += 1) await vi.runOnlyPendingTimersAsync();
    await vi.waitFor(() => expect(statuses).toContain("polling"));
    await vi.waitFor(() => expect(events.some((event) => event.topic === "leads")).toBe(true));
    client.stop();
  });

  it("refetches everything on a resync frame", async () => {
    const onResync = vi.fn();
    const fetchImpl = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response(streamFrom(['event: resync\ndata: {"revision":42,"reason":"gap"}\n\n']), { status: 200 })));
    const client = new RealtimeClient({ fetchImpl, onEvent: vi.fn(), onResync });
    client.start();
    await vi.waitFor(() => expect(onResync).toHaveBeenCalledWith(42));
    expect(client.lastRevision).toBe(42);
    client.stop();
  });

  it("reconnects on its own when the socket goes silent", async () => {
    // A half-open socket: the stream never errors, it just stops producing frames.
    const fetchImpl = vi.fn().mockImplementation(() => Promise.resolve(new Response(streamFrom(['event: hello\ndata: {"revision":1}\n\n']), { status: 200 })));
    const client = new RealtimeClient({ fetchImpl, onEvent: vi.fn(), onResync: vi.fn(), heartbeatTimeoutMs: 5_000 });
    client.start();
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(5_100); // watchdog fires
    await vi.advanceTimersByTimeAsync(600); // backoff, then a second attempt
    expect(fetchImpl.mock.calls.length).toBeGreaterThan(1);
    client.stop();
  });

  it("does not reconnect after stop()", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("down"));
    const client = new RealtimeClient({ fetchImpl, onEvent: vi.fn(), onResync: vi.fn() });
    client.start();
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    client.stop();
    const calls = fetchImpl.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetchImpl.mock.calls.length).toBe(calls);
    expect(client.currentStatus).toBe("offline");
  });

  it("maps a topic to every screen that shows it", () => {
    // a requests change must refetch the queue *and* the KPIs and the portal, or two screens disagree
    expect(queryKeysFor("requests")).toEqual(expect.arrayContaining(["requests", "dashboard", "portal", "nav"]));
    expect(queryKeysFor("policies")).toEqual(expect.arrayContaining(["policies", "portal"]));
    expect(queryKeysFor("nonsense")).toEqual([]);
  });
});

/**
 * R15 P0 — the invalidation policy. The failure this pins is not a wrong number on a screen; it is a tab that
 * comes back from the background and fires a request for every list in the app, most of them for screens the
 * operator is not even looking at.
 */
describe("R15 · narrow invalidation", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.useFakeTimers();
  });

  it("a gap invalidates the topic roots, active queries only — never the whole cache", async () => {
    const { QueryClient } = await import("@tanstack/react-query");
    const { invalidateForResync } = await import("@/lib/realtimeProvider");

    const queryClient = new QueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    invalidateForResync(queryClient);

    expect(spy).toHaveBeenCalled();
    for (const call of spy.mock.calls) {
      const filters = call[0] as { queryKey?: unknown[]; refetchType?: string } | undefined;
      // a bare `invalidateQueries()` — the bug — has no filters at all
      expect(filters).toBeDefined();
      expect(Array.isArray(filters?.queryKey)).toBe(true);
      expect(filters?.refetchType).toBe("active");
    }
    // roots, not a wildcard: every key we pass is one a topic can actually touch
    const { ALL_TOPIC_ROOTS } = await import("@/lib/realtime");
    const keys = spy.mock.calls.map((call) => (call[0] as { queryKey: unknown[] }).queryKey[0]);
    expect(new Set(keys)).toEqual(new Set(ALL_TOPIC_ROOTS));
  });

  it("an inactive query is not refetched by a gap; an active one is", async () => {
    const { QueryClient, QueryObserver } = await import("@tanstack/react-query");
    const { invalidateForResync } = await import("@/lib/realtimeProvider");

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
    const activeFn = vi.fn().mockResolvedValue(["row"]);
    const inactiveFn = vi.fn().mockResolvedValue(["row"]);

    // "on screen": an observer with a subscriber is what react-query counts as active
    const observer = new QueryObserver(queryClient, { queryKey: ["requests", "queue"], queryFn: activeFn });
    const unsubscribe = observer.subscribe(() => {});
    // "another screen, not mounted": prefetch populates the cache without an observer
    await queryClient.prefetchQuery({ queryKey: ["leads", "board"], queryFn: inactiveFn });

    await vi.waitFor(() => expect(activeFn).toHaveBeenCalled());
    const activeBefore = activeFn.mock.calls.length;
    const inactiveBefore = inactiveFn.mock.calls.length;

    invalidateForResync(queryClient);
    await vi.waitFor(() => expect(activeFn.mock.calls.length).toBeGreaterThan(activeBefore));
    // give the inactive refetch every chance to happen: it must not
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(inactiveFn.mock.calls.length).toBe(inactiveBefore);
    // …and the key is still *marked* stale, so opening that screen later refetches it (correctness kept)
    expect(queryClient.getQueryState(["leads", "board"])?.isInvalidated).toBe(true);

    unsubscribe();
  });

  it("a returning tab asks the server before invalidating anything", async () => {
    const { RealtimeClient } = await import("@/lib/realtime");
    const onResync = vi.fn();
    // the stream is a live, silent body; `/realtime/revision` answers from `serverRevision`
    let serverRevision = 0;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        // a real `hello` frame: that is what moves the client to "live" (a comment frame is only a heartbeat)
        controller.enqueue(new TextEncoder().encode('event: hello\ndata: {"revision":0}\n\n'));
      },
    });
    const fetchImpl = vi.fn(async (input: string) => {
      if (String(input).includes("/realtime/stream")) return { ok: true, status: 200, body: stream } as unknown as Response;
      return {
        ok: true,
        status: 200,
        json: async () => ({ revision: serverRevision, topics: serverRevision ? { requests: serverRevision } : {} }),
      } as unknown as Response;
    });
    const client = new RealtimeClient({ fetchImpl, onEvent: vi.fn(), onResync });
    client.start();
    await vi.waitFor(() => expect(client.currentStatus).toBe("live"));

    // nothing moved on the server: the tab must not invalidate anything
    const before = onResync.mock.calls.length;
    expect(await client.checkStale()).toBe(false);
    expect(onResync.mock.calls.length).toBe(before);

    // now the server has moved: exactly one resync, carrying the server's revision
    serverRevision = 42;
    expect(await client.checkStale()).toBe(true);
    expect(onResync).toHaveBeenCalledTimes(before + 1);
    expect(onResync).toHaveBeenLastCalledWith(42);
    // …and asking again with nothing new is a no-op (no storm on every focus)
    const afterMove = onResync.mock.calls.length;
    expect(await client.checkStale()).toBe(false);
    expect(onResync.mock.calls.length).toBe(afterMove);
    client.stop();
  });
});
