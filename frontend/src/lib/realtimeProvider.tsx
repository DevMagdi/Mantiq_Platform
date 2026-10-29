/**
 * The React wiring for the change feed (ADR-026): one connection per tab, one invalidation table.
 *
 * The invalidation table is the contract. A `requests` change refetches the queue, the open thread, the
 * dashboard KPIs and the portal — because all four are views of the same rows, and a card that shows a stale
 * number next to a list that is fresh is how people stop trusting the screen.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, getAccessToken, setAccessToken } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { RealtimeClient, queryKeysFor, setTokenReader, type ChangeEvent, type RealtimeStatus } from "@/lib/realtime";

interface RealtimeValue {
  status: RealtimeStatus;
  /** The revision the tab has applied — surfaced in tests and in the status tooltip. */
  connected: boolean;
  /** The last change revision this tab applied. `0` means "nothing seen yet". */
  revision: number;
}

const RealtimeContext = createContext<RealtimeValue>({ status: "offline", connected: false, revision: 0 });

/** Everything a topic change makes stale, in one place, so two screens cannot disagree about it. */
export function invalidateForChange(queryClient: QueryClient, event: ChangeEvent): void {
  for (const root of queryKeysFor(event.topic)) {
    void queryClient.invalidateQueries({ queryKey: [root] });
  }
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { user, ready } = useAuth();
  const [status, setStatus] = useState<RealtimeStatus>("offline");
  /*
   * The revision is on screen (`data-revision`) for the same reason the status pill exists: "how fresh is
   * this tab" must be answerable without a debugger. It is also the only way to tell two very different
   * failures apart in an e2e report — a change *that never reached the tab* (revision behind the server's)
   * from a change *the tab applied but did not refetch* (revision caught up, screen stale).
   */
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    setTokenReader(() => getAccessToken());
  }, []);

  useEffect(() => {
    // nothing to sync until we know who the caller is; a logged-out tab holds no connection at all
    if (!ready || !user) return;
    const client = new RealtimeClient({
      onEvent: (event) => {
        setRevision((seen) => Math.max(seen, event.revision));
        invalidateForChange(queryClient, event);
      },
      onResync: (applyTo) => {
        setRevision((seen) => Math.max(seen, applyTo));
        // a gap means "anything could have moved": refetch what is on screen rather than guessing
        void queryClient.invalidateQueries();
      },
      onStatus: setStatus,
      refresh: async () => {
        try {
          const refreshed = await api.post<{ access_token: string }>("/auth/refresh");
          setAccessToken(refreshed.access_token);
          return true;
        } catch {
          return false;
        }
      },
    });
    client.start();
    // A tab that comes back from the background had its socket killed by the browser; reconnect at once and
    // catch up, instead of waiting for the next backoff tick.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      client.stop();
      client.start();
      void queryClient.invalidateQueries();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      client.stop();
    };
  }, [ready, user, queryClient]);

  const value = useMemo<RealtimeValue>(
    () => ({ status, connected: status === "live", revision }),
    [status, revision],
  );
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeValue {
  return useContext(RealtimeContext);
}
