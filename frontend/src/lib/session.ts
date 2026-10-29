/**
 * Renewing the session — one place, one call in flight.
 *
 * Two failures this file exists to prevent:
 *
 * 1. **The storm.** `POST /auth/refresh` answering 401 used to notify the unauthorized listeners, whose
 *    handler calls `/auth/refresh`… a microtask loop that never ends, floods the console and freezes the
 *    tab. `withoutUnauthorizedRetry` is what stops the recursion, so every renewal goes through here.
 * 2. **The burst.** A screen that fires eight requests at once with an expired token produces eight 401s;
 *    eight refreshes would rotate the refresh family eight times, and the last one wins while the others
 *    are already invalid. The promise is therefore shared while it is in flight.
 *
 * The outcome distinguishes *refused* (the API answered 401/403 — the session is really gone) from
 * *unreachable* (network/5xx). Those must not be treated alike: signing a user out because their train
 * went into a tunnel is a bug, and it is the kind that is never reported as one.
 */
import { api, setAccessToken, withoutUnauthorizedRetry } from "@/lib/api/client";

export type RefreshOutcome =
  | { ok: true; token: string }
  | { ok: false; refused: boolean };

let inFlight: Promise<RefreshOutcome> | null = null;

async function renew(): Promise<RefreshOutcome> {
  try {
    const refreshed = await withoutUnauthorizedRetry(() =>
      api.post<{ access_token: string }>("/auth/refresh"),
    );
    setAccessToken(refreshed.access_token);
    return { ok: true, token: refreshed.access_token };
  } catch (error) {
    const status = (error as { status?: number }).status;
    return { ok: false, refused: status === 401 || status === 403 };
  }
}

/** One renewal shared by every caller that needs a token right now. */
export function refreshAccessToken(): Promise<RefreshOutcome> {
  if (inFlight) return inFlight;
  inFlight = renew().finally(() => {
    inFlight = null;
  });
  return inFlight;
}

/** Test hook: forget any in-flight renewal between cases. */
export function resetRefreshState(): void {
  inFlight = null;
}
