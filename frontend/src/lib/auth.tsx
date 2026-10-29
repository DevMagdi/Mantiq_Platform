/** Auth state: short-lived access token in memory + refresh cookie rotation (spec §6.2). */
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, onUnauthorized, setAccessToken } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type Me = components["schemas"]["MeOut"];
export type LoginIn = components["schemas"]["LoginIn"];

interface AuthValue {
  user: Me | null;
  ready: boolean;
  can: (permission: string) => boolean;
  canAny: (...permissions: string[]) => boolean;
  isManager: boolean;
  isPortal: boolean;
  login: (payload: LoginIn) => Promise<Me>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/**
 * «Is there probably a session in this browser?»
 *
 * The real credential is an `httpOnly` cookie the page cannot read, so this is only a *hint* — and it exists
 * for one reason: without it, every visit to the login screen fired `POST /auth/refresh` while signed out,
 * which the API answers 403 by design (a pinned contract, see `docs/STATUS.md`). The screen handled the
 * refusal fine, but the browser logs every refused request, so a correctly-behaving login page produced a
 * console error on every load. A hint that is wrong costs one redundant call (cookie present, hint missing)
 * or one failed refresh the app already handles (cookie gone, hint stale) — never a wrong screen.
 */
const SESSION_HINT = "bh.session";
function hintPresent(): boolean {
  try {
    return window.localStorage.getItem(SESSION_HINT) === "1";
  } catch {
    return true; // storage blocked (private mode): behave exactly as before this hint existed
  }
}
function setHint(present: boolean): void {
  try {
    if (present) window.localStorage.setItem(SESSION_HINT, "1");
    else window.localStorage.removeItem(SESSION_HINT);
  } catch {
    /* storage blocked: nothing to record, and nothing to break */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const queryClient = useQueryClient();

  const loadMe = useCallback(async () => {
    if (!hintPresent()) {
      // nothing to restore: no session existed in this browser, so asking would only produce a refusal
      setUser(null);
      setAccessToken(null);
      setReady(true);
      return;
    }
    try {
      const refreshed = await api.post<components["schemas"]["RefreshOut"]>("/auth/refresh");
      setAccessToken(refreshed.access_token);
      setHint(true);
      const me = await api.get<Me>("/auth/me");
      setUser(me);
    } catch (error) {
      // a refused refresh means the cookie is gone; a network blip does not, and must not sign anyone out
      const status = (error as { status?: number }).status;
      if (status === 401 || status === 403) setHint(false);
      setUser(null);
      setAccessToken(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  useEffect(
    () =>
      onUnauthorized(() => {
        // A dead access token is normal mid-session: refresh once, otherwise sign out.
        void api
          .post<{ access_token: string }>("/auth/refresh")
          .then((refreshed) => setAccessToken(refreshed.access_token))
          .catch(() => {
            setHint(false);
            setUser(null);
            setAccessToken(null);
            queryClient.clear();
          });
      }),
    [queryClient],
  );

  const login = useCallback(
    async (payload: LoginIn) => {
      const result = await api.post<components["schemas"]["LoginOut"]>("/auth/login", payload);
      setAccessToken(result.access_token);
      setHint(true);
      setUser(result.user);
      setReady(true);
      return result.user;
    },
    [setUser],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setHint(false);
      setAccessToken(null);
      setUser(null);
      queryClient.clear();
    }
  }, [queryClient]);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      ready,
      can: (permission: string) => Boolean(user?.permissions?.includes(permission)),
      canAny: (...permissions: string[]) => permissions.some((permission) => Boolean(user?.permissions?.includes(permission))),
      get isManager() {
        return Boolean(user?.permissions?.includes("leads.view_all"));
      },
      get isPortal() {
        return user?.role === "client_owner" || user?.role === "client_contact";
      },
      login,
      logout,
    }),
    [user, ready, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
