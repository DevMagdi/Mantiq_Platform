/**
 * The real-time client: one `fetch()` stream, parsed by hand, with a polling fallback (ADR-026).
 *
 * Why not `EventSource`: it cannot send an `Authorization` header, and the only workaround — a token in the
 * query string — writes the credential into access logs, proxy traces and `Referer`. `fetch()` + `ReadableStream`
 * is a dozen lines more code and keeps the same bearer-token contract as every other call.
 *
 * The class is deliberately framework-free so the tricky parts (partial frames, reconnect, watchdog,
 * fallback) are unit-testable without a browser. `realtime.tsx` is the thin React wiring on top.
 */

export type RealtimeStatus = "connecting" | "live" | "polling" | "offline";

export interface ChangeEvent {
  revision: number;
  topic: string;
  action: string;
  at?: string;
  entity_id?: string;
  path?: string;
}

export interface RealtimeOptions {
  /** Called for every change frame and for every change the polling fallback notices. */
  onEvent: (event: ChangeEvent) => void;
  /** The client missed changes (gap or overflow): refetch everything on screen. */
  onResync: (revision: number) => void;
  onStatus?: (status: RealtimeStatus) => void;
  /** Re-mints the access token. Resolves `false` when the session is gone for good. */
  refresh?: () => Promise<boolean>;
  /** Injected in tests; defaults to `window.fetch`. */
  fetchImpl?: (input: string, init?: RequestInit) => Promise<Response>;
  baseUrl?: string;
  /** Delay before the first reconnect attempt (doubles up to `maxBackoffMs`). */
  backoffMs?: number;
  maxBackoffMs?: number;
  /** A stream that says nothing for this long is treated as dead (server heartbeats every 15 s). */
  heartbeatTimeoutMs?: number;
  /** Fallback cadence once the stream has failed `pollAfterFailures` times in a row. */
  pollIntervalMs?: number;
  pollAfterFailures?: number;
  /** Timers are injected so the tests do not sleep. */
  now?: () => number;
}

const DEFAULTS = {
  baseUrl: "/api/v1",
  backoffMs: 500,
  maxBackoffMs: 8_000,
  heartbeatTimeoutMs: 40_000,
  pollIntervalMs: 4_000,
  pollAfterFailures: 3,
} as const;

interface SseFrame {
  event: string;
  data: string;
}

/**
 * Split a buffer into complete SSE frames, returning the remainder that has not arrived yet.
 *
 * A chunk boundary can fall anywhere — inside a `data:` line, between `event:` and `data:`, or in the middle
 * of a multi-byte Arabic character — so the parser must be incremental and the decoder must stream.
 */
export function parseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  // CRLF is normalised before splitting: a proxy may rewrite line endings, and `\r\n\r\n` would otherwise
  // never match the frame separator and the client would sit on a buffer that grows forever.
  const parts = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue; // comment / heartbeat
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      // `retry:` and `id:` are understood by EventSource, not by us: our backoff is explicit
    }
    if (data.length) frames.push({ event, data: data.join("\n") });
  }
  return { frames, rest };
}

export class RealtimeClient {
  private readonly options: Required<Pick<RealtimeOptions, "onEvent" | "onResync">> & RealtimeOptions;
  private readonly baseUrl: string;
  private readonly fetchImpl: (input: string, init?: RequestInit) => Promise<Response>;
  private controller: AbortController | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private failures = 0;
  private revision = 0;
  private topicRevisions: Record<string, number> = {};
  private polling = false;
  private status: RealtimeStatus = "offline";

  constructor(options: RealtimeOptions) {
    this.options = options;
    this.baseUrl = options.baseUrl ?? DEFAULTS.baseUrl;
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  get currentStatus(): RealtimeStatus {
    return this.status;
  }

  get lastRevision(): number {
    return this.revision;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.failures = 0;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.controller?.abort();
    this.controller = null;
    this.polling = false;
    this.setStatus("offline");
  }

  // ---- stream ------------------------------------------------------------------------------
  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.setStatus(this.polling ? "polling" : "connecting");
    const controller = new AbortController();
    this.controller = controller;
    this.armWatchdog(controller);

    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/realtime/stream?since=${this.revision}`, {
        method: "GET",
        headers: { authorization: `Bearer ${tokenOrEmpty()}`, accept: "text/event-stream" },
        credentials: "include",
        signal: controller.signal,
      });
    } catch {
      this.scheduleReconnect();
      return;
    }

    if (response.status === 401) {
      // The access token expires every 15 minutes; a stream that dies on expiry and never comes back is a
      // screen that silently stops updating, which is the failure this whole feature exists to remove.
      const renewed = (await this.options.refresh?.()) ?? false;
      if (renewed) this.retrySoon(0);
      else this.stop();
      return;
    }
    if (!response.ok || !response.body) {
      this.scheduleReconnect();
      return;
    }

    let reader: ReadableStreamDefaultReader<Uint8Array>;
    try {
      reader = response.body.getReader();
    } catch {
      // an already-consumed body (a retried Response object) is a programming error we must survive:
      // crashing here would take the whole tab down instead of retrying the stream
      this.scheduleReconnect();
      return;
    }
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = parseFrames(buffer);
        buffer = rest;
        for (const frame of frames) this.handleFrame(frame);
      }
    } catch {
      // aborted by `stop()` or by the watchdog — both paths decide what happens next themselves
    } finally {
      reader.releaseLock?.();
    }
    if (this.stopped) return;
    this.scheduleReconnect();
  }

  private handleFrame(frame: SseFrame): void {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(frame.data) as Record<string, unknown>;
    } catch {
      return; // a malformed frame must never take the connection down
    }
    const revision = Number(payload.revision ?? 0);
    if (frame.event === "hello") {
      this.failures = 0;
      this.polling = false;
      this.revision = Math.max(this.revision, revision);
      this.setStatus("live");
      this.armWatchdog(this.controller);
      return;
    }
    if (frame.event === "resync") {
      this.revision = Math.max(this.revision, revision);
      this.setStatus("live");
      this.armWatchdog(this.controller);
      this.options.onResync(revision);
      return;
    }
    if (frame.event === "change") {
      this.revision = Math.max(this.revision, revision);
      this.topicRevisions[String(payload.topic ?? "")] = revision;
      this.setStatus("live");
      this.armWatchdog(this.controller);
      this.options.onEvent({
        revision,
        topic: String(payload.topic ?? ""),
        action: String(payload.action ?? "changed"),
        at: typeof payload.at === "string" ? payload.at : undefined,
        entity_id: typeof payload.entity_id === "string" ? payload.entity_id : undefined,
        path: typeof payload.path === "string" ? payload.path : undefined,
      });
    }
  }

  // ---- fallback ----------------------------------------------------------------------------
  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.failures += 1;
    if (this.failures >= (this.options.pollAfterFailures ?? DEFAULTS.pollAfterFailures)) this.startPolling();
    const base = this.options.backoffMs ?? DEFAULTS.backoffMs;
    const ceiling = this.options.maxBackoffMs ?? DEFAULTS.maxBackoffMs;
    // a polling client retries the stream slowly: it is already converging, so hammering helps nobody
    const delay = this.polling ? ceiling * 2 : Math.min(ceiling, base * 2 ** Math.min(this.failures - 1, 5));
    this.retrySoon(delay);
  }

  private retrySoon(delay: number): void {
    if (this.stopped) return;
    this.clearReconnect();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }

  private startPolling(): void {
    if (this.stopped || this.polling) return;
    this.polling = true;
    this.setStatus("polling");
    void this.poll();
  }

  private async poll(): Promise<void> {
    if (this.stopped || !this.polling) return;
    try {
      const response = await this.fetchImpl(`${this.baseUrl}/realtime/revision`, {
        headers: { authorization: `Bearer ${tokenOrEmpty()}` },
        credentials: "include",
      });
      if (response.status === 401) {
        const renewed = (await this.options.refresh?.()) ?? false;
        if (!renewed) {
          this.stop();
          return;
        }
      } else if (response.ok) {
        const payload = (await response.json()) as { revision?: number; topics?: Record<string, number> };
        for (const [topic, revision] of Object.entries(payload.topics ?? {})) {
          if (typeof revision !== "number") continue;
          if ((this.topicRevisions[topic] ?? 0) < revision) {
            this.topicRevisions[topic] = revision;
            this.options.onEvent({ revision, topic, action: "changed" });
          }
        }
        this.revision = Math.max(this.revision, Number(payload.revision ?? 0));
      }
    } catch {
      // the network is still down: the next tick tries again, the user is already told it is "polling"
    }
    if (this.stopped || !this.polling) return;
    this.pollTimer = setTimeout(() => void this.poll(), this.options.pollIntervalMs ?? DEFAULTS.pollIntervalMs);
  }

  // ---- timers ------------------------------------------------------------------------------
  private armWatchdog(controller: AbortController | null): void {
    if (!controller || this.stopped) return;
    if (this.watchdogTimer) clearTimeout(this.watchdogTimer);
    // A half-open socket (NAT rebind, laptop sleep) looks exactly like a quiet feed. The server pings every
    // 15 s, so 40 s of silence means the connection is gone even though `read()` has not rejected.
    this.watchdogTimer = setTimeout(() => {
      controller.abort();
      this.scheduleReconnect();
    }, this.options.heartbeatTimeoutMs ?? DEFAULTS.heartbeatTimeoutMs);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private clearTimers(): void {
    this.clearReconnect();
    if (this.watchdogTimer) clearTimeout(this.watchdogTimer);
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.watchdogTimer = null;
    this.pollTimer = null;
  }

  private setStatus(next: RealtimeStatus): void {
    if (this.status === next) return;
    this.status = next;
    this.options.onStatus?.(next);
  }
}

function tokenOrEmpty(): string {
  // imported lazily to keep this module free of a cycle with the API client
  return readToken() ?? "";
}

/** Overridden by `realtime.tsx` with the API client's in-memory token. */
let tokenReader: () => string | null = () => null;

export function setTokenReader(reader: () => string | null): void {
  tokenReader = reader;
}

function readToken(): string | null {
  return tokenReader();
}

/** Topic → the query-key roots a change in it makes stale. Prefix matching: `["requests"]` covers the queue,
 * the detail pane and the thread, because react-query compares key arrays by prefix. */
export const TOPIC_QUERY_KEYS: Record<string, readonly string[]> = {
  // `nav` is the sidebar's open-request badge (`["nav", "open-requests"]`). Without it the pill kept the
  // count it loaded with for as long as the tab stayed open — the queue underneath it was fresh and the
  // number next to it was not, which is the "two screens disagree" failure this table exists to prevent.
  requests: ["requests", "dashboard", "notifications", "portal", "nav"],
  leads: ["leads", "sales", "sales-team", "dashboard", "team", "users"],
  clients: ["clients", "dashboard", "portal", "sales-team"],
  policies: ["policies", "portal", "reports"],
  settings: ["settings", "feature-flags", "roles"],
};

export function queryKeysFor(topic: string): readonly string[] {
  return TOPIC_QUERY_KEYS[topic] ?? [];
}
