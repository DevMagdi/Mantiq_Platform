import { QueryErrorResetBoundary } from "@tanstack/react-query";
import { Component, lazy, Suspense, type ErrorInfo, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { StaffShell } from "@/app/StaffShell";
import { TableSkeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth";

// Route-level code splitting keeps the first paint small (spec §10 performance).
const LoginPage = lazy(() => import("@/features/auth/LoginPage"));
const DashboardPage = lazy(() => import("@/features/dashboard/DashboardPage"));
const InboxPage = lazy(() => import("@/features/inbox/InboxPage"));
const KanbanPage = lazy(() => import("@/features/leads/KanbanPage"));
const LeadDetailPage = lazy(() => import("@/features/leads/LeadDetailPage"));
const NewLeadPage = lazy(() => import("@/features/leads/NewLeadPage"));
const SalesTeamPage = lazy(() => import("@/features/leads/SalesTeamPage"));
const MobileLeadsPage = lazy(() => import("@/features/leads/MobileLeadsPage"));
const ClientsPage = lazy(() => import("@/features/clients/ClientsPage"));
const ClientFilePage = lazy(() => import("@/features/clients/ClientFilePage"));
const InsuredPage = lazy(() => import("@/features/clients/InsuredPage"));
const PortalPage = lazy(() => import("@/features/portal/PortalPage"));
const SettingsHub = lazy(() => import("@/features/settings/SettingsHub"));
const SetupPage = lazy(() => import("@/features/settings/SetupPage"));
const OpsPage = lazy(() => import("@/features/settings/OpsPage"));
const PoliciesPage = lazy(() => import("@/features/phase2/PoliciesPage"));
const IssuingPage = lazy(() => import("@/features/phase2/IssuingPage"));
const ReportsPage = lazy(() => import("@/features/phase2/ReportsPage"));
// R10 (ADR-030): the executive screen and the four money screens
const ExecutiveDashboardPage = lazy(() => import("@/features/executive/ExecutiveDashboardPage"));
const LedgerPage = lazy(() => import("@/features/finance/LedgerPage"));
const ArPage = lazy(() => import("@/features/finance/ArPage"));
const CommissionsPage = lazy(() => import("@/features/finance/CommissionsPage"));
const CashboxPage = lazy(() => import("@/features/finance/CashboxPage"));
const ErpPage = lazy(() => import("@/features/finance/ErpPage"));

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // structlog lives on the server; the client keeps a breadcrumb for support
    console.error("ui_crash", error.message, info.componentStack);
  }

  override render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-bg p-8">
          <div className="card max-w-[440px] text-center">
            <h1 className="text-card font-bold">حدث خطأ في الواجهة</h1>
            <p className="mt-2 text-[13px] text-muted">{this.state.error.message}</p>
            <button type="button" className="btn-primary mt-4" onClick={() => window.location.reload()}>
              إعادة تحميل
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function Fallback() {
  return (
    <div className="card">
      <TableSkeleton rows={8} cols={6} />
    </div>
  );
}

function StaffRoute({ children, permit }: { children: ReactNode; permit?: string[] }) {
  const { user, ready, canAny } = useAuth();
  if (!ready) return <Fallback />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role.startsWith("client_")) return <Navigate to="/portal" replace />;
  if (permit && !canAny(...permit)) return <NoAccess />;
  return <StaffShell>{children}</StaffShell>;
}

function NoAccess() {
  return (
    <div className="grid min-h-screen place-items-center bg-bg p-8">
      <div className="card max-w-[420px] text-center">
        <div className="text-[34px] font-bold text-[var(--a-ink)]">403</div>
        <h1 className="mt-1 text-card font-bold">لا تملك صلاحية لهذه الشاشة</h1>
        <p className="mt-2 text-[13px] text-muted">اطلب من مدير النظام توسيع صلاحيات دورك.</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryErrorResetBoundary>
        {({ reset }) => (
          <Suspense fallback={<Fallback />}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/portal" element={<PortalRoute />} />
              <Route path="/mobile/leads" element={<MobileRoute />} />
              <Route
                path="/dashboard"
                element={
                  <StaffRoute>
                    <DashboardPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/inbox"
                element={
                  <StaffRoute permit={["requests.view"]}>
                    <InboxPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/inbox/:requestId"
                element={
                  <StaffRoute permit={["requests.view"]}>
                    <InboxPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/leads"
                element={
                  <StaffRoute permit={["leads.view_own", "leads.view_all"]}>
                    <KanbanPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/leads/:leadId"
                element={
                  <StaffRoute permit={["leads.view_own", "leads.view_all"]}>
                    <LeadDetailPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/leads/new"
                element={
                  <StaffRoute permit={["leads.view_own", "leads.view_all"]}>
                    <NewLeadPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/sales-team"
                element={
                  <StaffRoute permit={["leads.view_all"]}>
                    <SalesTeamPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/clients"
                element={
                  <StaffRoute permit={["clients.view_own", "clients.view_all", "clients.manage"]}>
                    <ClientsPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/clients/:clientId"
                element={
                  <StaffRoute permit={["clients.view_own", "clients.view_all"]}>
                    <ClientFilePage />
                  </StaffRoute>
                }
              />
              <Route
                path="/clients/:clientId/insured"
                element={
                  <StaffRoute permit={["clients.view_own", "clients.view_all"]}>
                    <InsuredPage />
                  </StaffRoute>
                }
              />
              {/*
               * Screen 15 lives inside the hub now. The old path is kept as a redirect because the design
               * sheet, the runbook and the e2e specs all link `/users` — a dead link there would be a
               * regression dressed up as a refactor. The tab itself stays gated on `users.manage`.
               */}
              <Route path="/users" element={<Navigate to="/settings?tab=users" replace />} />
              <Route
                path="/settings"
                element={
                  <StaffRoute permit={["settings.manage"]}>
                    <SettingsHub />
                  </StaffRoute>
                }
              />
              <Route
                path="/setup"
                element={
                  <StaffRoute permit={["settings.manage"]}>
                    <SetupPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/ops"
                element={
                  <StaffRoute permit={["settings.manage"]}>
                    <OpsPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/policies"
                element={
                  <StaffRoute permit={["policies.view"]}>
                    <PoliciesPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/issuing"
                element={
                  <StaffRoute permit={["policies.manage", "policies.view"]}>
                    <IssuingPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/reports"
                element={
                  <StaffRoute permit={["reports.view"]}>
                    <ReportsPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/executive"
                element={
                  <StaffRoute permit={["reports.executive"]}>
                    <ExecutiveDashboardPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/erp"
                element={
                  <StaffRoute permit={["finance.view", "reports.executive"]}>
                    <ErpPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/finance/ledger"
                element={
                  <StaffRoute permit={["finance.view"]}>
                    <LedgerPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/finance/ar"
                element={
                  <StaffRoute permit={["finance.view"]}>
                    <ArPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/finance/commissions"
                element={
                  <StaffRoute permit={["finance.view"]}>
                    <CommissionsPage />
                  </StaffRoute>
                }
              />
              <Route
                path="/finance/cashbox"
                element={
                  <StaffRoute permit={["finance.view"]}>
                    <CashboxPage />
                  </StaffRoute>
                }
              />
              <Route path="*" element={<CatchAll reset={reset} />} />
            </Routes>
          </Suspense>
        )}
      </QueryErrorResetBoundary>
    </ErrorBoundary>
  );
}

function PortalRoute() {
  const { user, ready } = useAuth();
  if (!ready) return <Fallback />;
  if (!user) return <Navigate to="/login" replace />;
  return <PortalPage />;
}

function MobileRoute() {
  const { user, ready } = useAuth();
  if (!ready) return <Fallback />;
  if (!user) return <Navigate to="/login" replace />;
  return <MobileLeadsPage />;
}

function CatchAll({ reset }: { reset: () => void }) {
  const { user, ready } = useAuth();
  if (!ready) return <Fallback />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role.startsWith("client_")) return <Navigate to="/portal" replace />;
  // a stale query error must not trap the user on the error screen
  reset();
  return <Navigate to="/dashboard" replace />;
}
