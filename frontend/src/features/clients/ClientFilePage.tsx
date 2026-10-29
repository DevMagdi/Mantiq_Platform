/**
 * Screen 5 — the client file: 64px avatar + 22px name header, four KPIs, and segmented tabs
 * (overview / requests / staff / documents / calls). One card per section, 18×20 padding.
 */
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Clock3, Inbox, PhoneCall, Plus, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { Avatar, Badge, Button, Card, DataTable, PageHeader, SectionTitle, Segmented, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatDuration, formatMoney, formatNumber, relativeTime } from "@/lib/format";
import type { ClientOverview, InsuredList, RequestPage } from "@/lib/api/views";
import type { components } from "@/lib/api/schema";
import { NewPolicyDialog } from "@/features/phase2/NewPolicyDialog";
import { usePhase2 } from "@/features/phase2/usePhase2";
import { PortalAccountsCard } from "./PortalAccountsCard";

type Tab = "overview" | "requests" | "staff" | "documents" | "calls";
type ClientCall = components["schemas"]["CallOut"];
type ClientDocument = components["schemas"]["AttachmentOut"];

export default function ClientFilePage() {
  const { clientId = "" } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");

  const file = useQuery({
    queryKey: ["clients", clientId, "overview"],
    queryFn: () => api.get<ClientOverview>(`/clients/${clientId}/overview`),
  });

  if (file.isPending) return <StateBlock kind="loading" />;
  if (file.isError) {
    return (
      <StateBlock
        kind="notfound"
        title={t("common.notFoundTitle")}
        body={t("common.notFoundBody")}
        action={<Button onClick={() => navigate("/clients")}>{t("common.back")}</Button>}
      />
    );
  }

  const client = file.data.client;
  const kpis = file.data.kpis ?? {};
  const policy = file.data.policy;
  const primary = (file.data.contacts ?? [])[0];

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <Avatar name={client.name} size={64} />
            <span className="min-w-0">
              <span className="block truncate text-[22px] font-bold text-ink">{client.name}</span>
              <span className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                <Badge tone="b">{t("clients.file.groupPolicy")}</Badge>
                {client.insurance_type ? <span>{client.insurance_type}</span> : null}
                {client.insurer ? <span>· {client.insurer}</span> : null}
                {client.is_converted_from_lead ? <Badge tone="g">{t("leads.movedToClients")}</Badge> : null}
              </span>
            </span>
          </span>
        }
        actions={
          <>
            <Button icon={<PhoneCall size={15} strokeWidth={1.8} />} onClick={() => primary?.mobile && window.open(`tel:${primary.mobile}`, "_self")}>
              {t("common.call")}
            </Button>
            <Button variant="primary" onClick={() => navigate(`/clients/${clientId}/insured`)}>
              {t("clients.file.tabs.staff")}
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t("clients.file.kpiStaff")} value={formatNumber(kpis.insured ?? 0)} icon={<Users size={18} strokeWidth={1.8} />} tone="b" />
        <Kpi
          label={t("clients.file.kpiOpen")}
          value={formatNumber(kpis.open_requests ?? 0)}
          icon={<Inbox size={18} strokeWidth={1.8} />}
          tone={Number(kpis.open_requests ?? 0) > 0 ? "a" : "g"}
        />
        <Kpi
          label={t("clients.file.kpiResponse")}
          value={kpis.avg_response_minutes == null ? "—" : `${formatNumber(Math.round(kpis.avg_response_minutes))} ${t("common.minutes")}`}
          icon={<Clock3 size={18} strokeWidth={1.8} />}
          tone="t"
        />
        <Kpi
          label={t("clients.file.kpiRenewal")}
          value={kpis.renewal_in_days == null ? "—" : `${formatNumber(kpis.renewal_in_days)} ${t("clients.file.days")}`}
          hint={kpis.calls != null ? `${formatNumber(kpis.calls)} ${t("channel.call")}` : undefined}
          icon={<CalendarClock size={18} strokeWidth={1.8} />}
          tone={Number(kpis.renewal_in_days ?? 999) <= 60 ? "r" : "g"}
        />
      </div>

      <div className="mt-5 mb-4">
        <Segmented
          ariaLabel={t("clients.file.title")}
          value={tab}
          onChange={(next) => setTab(next)}
          options={[
            { value: "overview", label: t("clients.file.tabs.overview") },
            { value: "requests", label: t("clients.file.tabs.requests") },
            { value: "staff", label: t("clients.file.tabs.staff") },
            { value: "documents", label: t("clients.file.tabs.documents") },
            { value: "calls", label: t("clients.file.tabs.calls") },
          ]}
        />
      </div>

      {tab === "overview" ? <OverviewTab clientId={clientId} policy={policy} contractStart={client.since_date} contractEnd={client.end_date} /> : null}
      {tab === "requests" ? <RequestsTab clientId={clientId} /> : null}
      {tab === "staff" ? <StaffTab clientId={clientId} onOpenAll={() => navigate(`/clients/${clientId}/insured`)} /> : null}
      {tab === "documents" ? <DocumentsTab clientId={clientId} /> : null}
      {tab === "calls" ? <CallsTab clientId={clientId} /> : null}
    </>
  );
}

function Kpi({
  label,
  value,
  hint,
  icon,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: ReactNode;
  tone: "b" | "g" | "a" | "r" | "t";
}) {
  return (
    <Card className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-muted">{label}</div>
        <div className="mt-1.5 text-kpi font-bold tabular-nums text-ink">{value}</div>
        {hint ? <div className="mt-1 text-[12px] text-muted">{hint}</div> : null}
      </div>
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px]" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
        {icon}
      </span>
    </Card>
  );
}

function OverviewTab({
  clientId,
  policy,
  contractStart,
  contractEnd,
}: {
  clientId: string;
  policy: ClientOverview["policy"];
  contractStart?: string | null;
  contractEnd?: string | null;
}) {
  const { t } = useTranslation();
  const requests = useQuery({
    queryKey: ["clients", clientId, "requests"],
    queryFn: () => api.get<RequestPage>(`/clients/${clientId}/requests`, { page_size: 6 }),
  });
  const navigate = useNavigate();

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Card padded={false}>
        <div className="px-[20px] pt-[18px]">
          <SectionTitle>{t("clients.file.recentRequests")}</SectionTitle>
        </div>
        <div className="px-[20px] pb-[18px]">
          <DataTable
            loading={requests.isPending}
            rows={requests.data?.items ?? []}
            keyOf={(row) => String(row.id)}
            onRowClick={(row) => navigate(`/inbox/${row.id}`)}
            empty={<StateBlock kind="empty" title={t("clients.insured.kpiRequests")} body={t("portal.empty")} />}
            columns={[
              { id: "number", head: t("inbox.request"), width: 80, render: (row) => <span className="font-bold text-primary">#{row.number}</span> },
              { id: "subject", head: t("inbox.details"), render: (row) => <span className="text-[13px] text-ink">{row.subject}</span> },
              { id: "channel", head: t("dashboard.colChannel"), width: 100, render: (row) => t(`channel.${row.channel}`) },
              {
                id: "status",
                head: t("dashboard.colStatus"),
                width: 130,
                render: (row) => <Badge tone={row.status === "resolved" ? "g" : row.is_overdue ? "r" : "a"}>{t(`status.${row.status}`)}</Badge>,
              },
              { id: "when", head: t("leads.lastActivity"), width: 90, align: "end", render: (row) => relativeTime(row.last_activity_at) },
            ]}
          />
        </div>
      </Card>

      <div className="space-y-4">
        <Card>
          <SectionTitle>{t("clients.file.contract")}</SectionTitle>
          <dl className="space-y-3 text-[13px]">
            <Row label={t("clients.file.contractStart")} value={policy?.start_date ?? contractStart ?? "—"} />
            <Row label={t("clients.file.contractEnd")} value={policy?.end_date ?? contractEnd ?? "—"} />
            <Row label={t("phase2.colPremium")} value={policy?.premium_amount ? formatMoney(policy.premium_amount) : "—"} />
            <Row label={t("clients.file.paymentPlan")} value={policy?.payment_plan ?? "—"} />
            <Row label={t("clients.colStaff")} value={`${formatNumber(policy?.headcount ?? 0)} ${t("leads.insured_count")}`} />
          </dl>
          {policy ? (
            <p className="mt-4 rounded-btn px-3 py-2 text-[12px] font-semibold" style={{ background: "var(--b-bg)", color: "var(--b-ink)" }}>
              {t("clients.activeContract")} · {policy.policy_no ?? "—"}
            </p>
          ) : (
            <p className="mt-4 text-[12.5px] text-muted">{t("phase2.autoInstallments")}</p>
          )}
        </Card>

        {/* the login of the client's own owner: created where the client is, because it only means
            something when it is pinned to this company (ADR-022) */}
      <PortalAccountsCard clientId={clientId} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line pb-2 last:border-0">
      <dt className="text-muted">{label}</dt>
      <dd className="truncate font-semibold text-ink">{String(value ?? "—")}</dd>
    </div>
  );
}

function RequestsTab({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const requests = useQuery({
    queryKey: ["clients", clientId, "requests", "all"],
    queryFn: () => api.get<RequestPage>(`/clients/${clientId}/requests`, { page_size: 25 }),
  });
  return (
    <Card padded={false}>
      <div className="px-[20px] py-[18px]">
        <DataTable
          loading={requests.isPending}
          rows={requests.data?.items ?? []}
          keyOf={(row) => String(row.id)}
          onRowClick={(row) => navigate(`/inbox/${row.id}`)}
          rowTone={(row) => (row.is_overdue ? "warn" : undefined)}
          empty={<StateBlock kind="empty" body={t("portal.empty")} />}
          columns={[
            { id: "number", head: t("inbox.request"), width: 80, render: (row) => <span className="font-bold text-primary">#{row.number}</span> },
            { id: "subject", head: t("inbox.details"), render: (row) => row.subject },
            { id: "employee", head: t("dashboard.colEmployee"), width: 150, render: (row) => row.employee_name ?? "—" },
            { id: "channel", head: t("dashboard.colChannel"), width: 100, render: (row) => t(`channel.${row.channel}`) },
            {
              id: "status",
              head: t("dashboard.colStatus"),
              width: 130,
              render: (row) => <Badge tone={row.status === "resolved" ? "g" : row.is_overdue ? "r" : "a"}>{t(`status.${row.status}`)}</Badge>,
            },
          ]}
        />
      </div>
    </Card>
  );
}

function StaffTab({ clientId, onOpenAll }: { clientId: string; onOpenAll: () => void }) {
  const { t } = useTranslation();
  const insured = useQuery({
    queryKey: ["clients", clientId, "insured", 1],
    queryFn: () => api.get<InsuredList>(`/clients/${clientId}/insured`, { page_size: 8 }),
  });
  return (
    <Card padded={false}>
      <div className="flex items-center justify-between gap-3 px-[20px] pt-[18px]">
        <SectionTitle>{t("clients.insured.title")}</SectionTitle>
        <Button size="sm" onClick={onOpenAll}>
          {t("common.view")}
        </Button>
      </div>
      <div className="px-[20px] pb-[18px]">
        <DataTable
          loading={insured.isPending}
          rows={insured.data?.page.items ?? []}
          keyOf={(row) => String(row.id)}
          empty={<StateBlock kind="empty" />}
          columns={[
            { id: "name", head: t("clients.insured.colName"), render: (row) => <span className="font-semibold text-ink">{row.name}</span> },
            { id: "dept", head: t("clients.insured.colDept"), width: 160, render: (row) => row.department ?? "—" },
            {
              id: "status",
              head: t("clients.insured.colStatus"),
              width: 130,
              render: (row) => <Badge tone={row.coverage_status === "active" ? "g" : row.coverage_status === "pending" ? "a" : "t"}>{t(`coverage.${row.coverage_status}`)}</Badge>,
            },
          ]}
        />
      </div>
    </Card>
  );
}

function DocumentsTab({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const { enabled: phase2 } = usePhase2();
  const [creating, setCreating] = useState(false);
  const docs = useQuery({
    queryKey: ["clients", clientId, "documents"],
    queryFn: () => api.get<ClientDocument[]>(`/clients/${clientId}/documents`),
  });
  return (
    <Card padded={false}>
      <div className="px-[20px] pt-[18px]">
        <SectionTitle
          action={
            // the second door to the same dialog: a policy is booked where the client is (R7). It only
            // appears when the flag is on, because POST /policies answers 503 while Phase 2 is closed.
            phase2 ? (
              <Button size="sm" icon={<Plus size={14} strokeWidth={1.8} />} onClick={() => setCreating(true)}>
                {t("phase2.newPolicy")}
              </Button>
            ) : undefined
          }
        >
          {t("portal.documents")}
        </SectionTitle>
      </div>
      <div className="px-[20px] py-[18px]">
        {creating ? <NewPolicyDialog clientId={clientId} onClose={() => setCreating(false)} /> : null}
        <DataTable
          loading={docs.isPending}
          rows={docs.data ?? []}
          keyOf={(row) => String(row.id)}
          empty={<StateBlock kind="empty" title={t("portal.documents")} />}
          columns={[
            { id: "name", head: t("portal.documents"), render: (row) => <span className="font-semibold text-ink">{row.filename}</span> },
            { id: "mime", head: t("inbox.details"), width: 140, render: (row) => row.mime },
            { id: "size", head: t("common.rows"), width: 100, render: (row) => `${formatNumber(Math.round((row.size ?? 0) / 1024))} KB` },
            { id: "when", head: t("leads.lastActivity"), width: 120, render: (row) => relativeTime(row.created_at) },
            {
              id: "open",
              head: t("common.download"),
              width: 90,
              align: "end",
              render: (row) =>
                row.url ? (
                  <a href={row.url} target="_blank" rel="noreferrer" className="text-[12.5px] font-semibold text-primary hover:underline">
                    {t("common.open")}
                  </a>
                ) : (
                  <span className="text-[12.5px] text-muted">{t("common.noMedia")}</span>
                ),
            },
          ]}
        />
      </div>
    </Card>
  );
}

function CallsTab({ clientId }: { clientId: string }) {
  const { t } = useTranslation();
  const calls = useQuery({
    queryKey: ["clients", clientId, "calls"],
    queryFn: () => api.get<ClientCall[]>(`/clients/${clientId}/calls`),
  });
  return (
    <Card padded={false}>
      <div className="px-[20px] py-[18px]">
        <DataTable
          loading={calls.isPending}
          rows={calls.data ?? []}
          keyOf={(row) => String(row.id)}
          empty={<StateBlock kind="empty" title={t("inbox.inboundCall")} />}
          columns={[
            {
              id: "who",
              head: t("inbox.employee"),
              render: (row) => <span className="font-semibold text-ink">{row.caller_name ?? row.from_number ?? "—"}</span>,
            },
            {
              id: "direction",
              head: t("channel.call"),
              width: 120,
              render: (row) => (row.direction === "in" ? t("inbox.inboundCall") : t("common.call")),
            },
            { id: "when", head: t("leads.lastActivity"), width: 160, render: (row) => relativeTime(row.started_at) },
            { id: "duration", head: t("inbox.voiceNote"), width: 120, align: "end", render: (row) => formatDuration(row.duration_s ?? 0) },
          ]}
        />
      </div>
    </Card>
  );
}
