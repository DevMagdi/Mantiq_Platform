import {
  Activity,
  BarChart3,
  ClipboardList,
  FolderOpen,
  Inbox,
  KanbanSquare,
  Landmark,
  LayoutDashboard,
  LogOut,
  Rocket,
  Settings,
  Users,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { navItemsFor, openBadge, type NavItemModel } from "@/app/navModel";
import { Avatar, SearchInput } from "@/components/ui";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { useRealtime } from "@/lib/realtimeProvider";
import { changeLanguage } from "@/lib/i18n";

type NavItem = NavItemModel & { labelKey: string; icon: ReactNode };

/**
 * `perms` is the permission the screen's own API requires; an item without it is open to every staff
 * role. The inbox badge is data (`only_open`), never a number copied from the mockup.
 */
const NAV: NavItem[] = [
  { to: "/dashboard", labelKey: "nav.dashboard", icon: <LayoutDashboard size={20} strokeWidth={1.8} />, perms: ["requests.view"] },
  { to: "/inbox", labelKey: "nav.inbox", icon: <Inbox size={20} strokeWidth={1.8} />, perms: ["requests.view"], showOpenBadge: true },
  { to: "/leads", labelKey: "nav.leads", icon: <KanbanSquare size={20} strokeWidth={1.8} />, perms: ["leads.view_own", "leads.view_all"] },
  { to: "/clients", labelKey: "nav.clients", icon: <FolderOpen size={20} strokeWidth={1.8} />, perms: ["clients.view_own", "clients.view_all"] },
  { to: "/sales-team", labelKey: "leads.team", icon: <Users size={20} strokeWidth={1.8} />, permsAll: ["leads.view_all", "leads.assign"] },
  { to: "/policies", labelKey: "nav.policies", icon: <ClipboardList size={20} strokeWidth={1.8} />, perms: ["policies.view"] },
  { to: "/reports", labelKey: "nav.reports", icon: <BarChart3 size={20} strokeWidth={1.8} />, perms: ["reports.view"] },
  // ERP hub (ADR-031): one money item. Deep finance routes stay reachable from /erp cards/bookmarks; a role without finance.view never sees this, and the
  // API answers 403 for the same paths — the hidden item and the refusal are the same rule (ADR-030 §5).
  { to: "/erp", labelKey: "nav.erp", icon: <Landmark size={20} strokeWidth={1.8} />, perms: ["finance.view", "reports.executive"] },
  { to: "/settings", labelKey: "nav.settings", icon: <Settings size={20} strokeWidth={1.8} />, perms: ["settings.manage"] },
  { to: "/setup", labelKey: "nav.setup", icon: <Rocket size={20} strokeWidth={1.8} />, perms: ["settings.manage"] },
  { to: "/ops", labelKey: "nav.ops", icon: <Activity size={20} strokeWidth={1.8} />, perms: ["settings.manage"] },
];

export function StaffShell({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [globalTerm, setGlobalTerm] = useState("");
  const [signingOut, setSigningOut] = useState(false);
  const realtime = useRealtime();

  const permissions = user?.permissions ?? [];
  const items = navItemsFor(NAV, permissions);
  // one cheap count for the pill; a role that cannot open the inbox must not even see a number for it
  const openRequests = useQuery({
    queryKey: ["nav", "open-requests"],
    queryFn: () => api.get<{ total: number }>("/requests?only_open=true&limit=1"),
    enabled: permissions.includes("requests.view"),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  return (
    <div className="flex min-h-screen bg-bg">
      {/*
        * Below `lg` the sidebar is not rendered at all. It used to be: a fixed `w-[264px]` panel took 264 of
        * 390 CSS pixels on a phone and left 126px for the screen — the dashboard's KPI label measured
        * `width: 0` and every RTL card was squeezed to a sliver (found by the mobile e2e project, R3). The
        * phone gets its own navigation (bottom bar, below) and the full width for content.
        */}
      <aside className="hidden h-screen w-[264px] shrink-0 sticky top-0 bg-sidebar px-[12px] py-[18px] lg:block">
        <div className="mb-[20px] flex items-center gap-2.5 px-2">
          <span className="grid h-[34px] w-[34px] place-items-center rounded-[10px] text-[15px] font-bold text-white" style={{ background: "var(--primary)" }}>
            و
          </span>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-bold text-white">{t("common.appName")}</div>
          </div>
        </div>

        <nav className="flex flex-col gap-1" aria-label={t("nav.ariaLabel")}>
          {items.map((item) => {
            const badge = item.showOpenBadge ? openBadge(openRequests.data?.total) : null;
            return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-btn px-3 py-[11px] text-[14px] font-semibold transition ${
                  isActive ? "bg-[var(--sidebar-active)] text-white" : "text-[var(--sidebar-text)] hover:bg-white/5 hover:text-white"
                }`
              }
            >
              {item.icon}
              <span className="flex-1 truncate">{t(item.labelKey)}</span>
              {item.showOpenBadge && badge ? (
                <span className="rounded-pill bg-primary px-2 py-[2px] text-[11px] font-bold text-white">{badge}</span>
              ) : null}
            </NavLink>
            );
          })}
        </nav>

        <div className="mt-[18px] border-t border-white/10 px-2 pt-3">
          <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-white/40">{t("users.scope")}</div>
          <div className="text-[12px] text-[var(--sidebar-text)]">
            {user?.scope_mode === "all" ? t("users.allClients") : t("users.scoped")}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-[54px] items-center justify-between gap-4 border-b border-line bg-card px-[24px]">
          <div className="min-w-0 text-[13px] text-muted">{t("dashboard.subtitle")}</div>
          <div className="flex flex-1 items-center justify-end gap-3">
            {/* The sheet's header field: typing here filters the shared inbox queue */}
            <form
              role="search"
              className="hidden lg:block"
              onSubmit={(event) => {
                event.preventDefault();
                const term = globalTerm.trim();
                if (term) navigate(`/inbox?q=${encodeURIComponent(term)}`);
              }}
            >
              <SearchInput value={globalTerm} onChange={setGlobalTerm} placeholder={t("common.search")} />
            </form>
            <button
              type="button"
              className="rounded-btn px-2.5 py-1.5 text-[13px] font-semibold text-muted transition hover:bg-[#F1F2F8] hover:text-ink"
              onClick={() => void changeLanguage(i18n.language === "ar" ? "en" : "ar")}
              aria-label="language"
            >
              {i18n.language === "ar" ? "EN" : "ع"}
            </button>
            {/* The feed's own state, visible to an operator: a screen that has quietly stopped updating is
                the failure mode this feature exists to remove, so "how fresh is this" must be answerable
                without opening devtools. */}
            <span
              role="status"
              aria-live="polite"
              data-testid="realtime-status"
              data-status={realtime.status}
              data-revision={realtime.revision}
              title={t(`realtime.${realtime.status}`)}
              className="flex items-center gap-1.5 rounded-pill px-2 py-[3px] text-[11.5px] font-semibold"
              style={
                realtime.status === "live"
                  ? { background: "var(--g-bg)", color: "var(--g-ink)" }
                  : realtime.status === "polling"
                    ? { background: "var(--a-bg)", color: "var(--a-ink)" }
                    : { background: "var(--kanban-col)", color: "var(--muted)" }
              }
            >
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />
              <span className="hidden md:inline">{t(`realtime.${realtime.status}`)}</span>
            </span>
            <Avatar name={user?.full_name ?? "?"} size={30} />
            <span className="hidden text-[13px] font-semibold text-ink sm:block">{user?.full_name}</span>
            <button
              type="button"
              className="flex items-center gap-1.5 text-[13px] font-semibold text-muted transition hover:text-[var(--r-ink)]"
              disabled={signingOut}
              onClick={async () => {
                setSigningOut(true);
                try {
                  await api.post("/portal/sign-out").catch(() => undefined);
                  await logout();
                  navigate("/login", { replace: true });
                } finally {
                  setSigningOut(false);
                }
              }}
            >
              <LogOut size={16} strokeWidth={1.8} />
              <span className="hidden md:inline">{t("portal.signOut")}</span>
            </button>
          </div>
        </header>

        {/* the bottom bar is fixed on a phone, so the last card must not hide behind it */}
        <main className="content-pad min-w-0 flex-1 pb-[96px] lg:pb-0">{children}</main>
      </div>

      {/* Phone navigation: the same permission-filtered items, one row, labelled separately from the
          desktop sidebar so a test (or a screen reader) can tell which navigation it is talking about. */}
      <nav
        aria-label={t("nav.mobileAriaLabel")}
        className="fixed inset-x-0 bottom-0 z-30 grid auto-cols-fr grid-flow-col border-t border-line bg-card lg:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {items.slice(0, 5).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-col items-center gap-1 px-1 py-2.5 text-[10.5px] font-semibold transition ${
                isActive ? "text-primary" : "text-muted"
              }`
            }
          >
            {item.icon}
            <span className="max-w-full truncate">{t(item.labelKey)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
