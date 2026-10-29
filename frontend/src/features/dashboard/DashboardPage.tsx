/**
 * Screen 1 — لوحة التحكم: 4 KPI cards with hint lines, the 7-day channel chart,
 * the status breakdown and the latest-requests table (design page 1).
 */
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { useState } from "react";
import { ArrowDownRight, ArrowUpRight, Clock3, Inbox, TriangleAlert, Gauge } from "lucide-react";

import { Badge, Button, Card, PageHeader, SectionTitle, StateBlock, DataTable } from "@/components/ui";
import { ApiError_, api } from "@/lib/api/client";
import { formatNumber, formatPercent, relativeTime } from "@/lib/format";

interface Summary {
  requests_today: number;
  requests_yesterday?: number;
  open_requests?: number;
  overdue?: number;
  sla_compliance_pct?: number | null;
  avg_first_response_minutes?: number | null;
  avg_first_response_delta_minutes?: number | null;
  clients_total?: number;
}

interface ChannelRow {
  date: string;
  whatsapp: number;
  call: number;
  email: number;
  manual: number;
}

interface StatusRow {
  key: string;
  count: number;
  pct: number;
}

interface RecentRow {
  id: string;
  number: number;
  subject: string;
  status: string;
  channel: string;
  employee_name: string;
  client_name: string;
  insurance_type: string | null;
  insurer: string | null;
  is_overdue: boolean;
  sla_hours_late: number | null;
  last_activity_at?: string;
}

const CHANNELS = ["whatsapp", "call", "email", "manual"] as const;
const CHANNEL_COLOR: Record<(typeof CHANNELS)[number], string> = {
  whatsapp: "var(--g-ink)",
  call: "var(--primary)",
  email: "var(--a-ink)",
  manual: "var(--line-2)",
};

interface TodayBoard {
  overdue_requests: { count: number; items: RecentRow[] };
  lead_followups: { count: number; items: { id: string; name: string; mobile: string | null; next_followup_at: string | null }[] };
  installments_due: { count: number; items: { id: string; policy_no: string | null; due_date: string; amount: number }[] };
}

export default function DashboardPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState(false);

  const summary = useQuery({ queryKey: ["dashboard", "summary"], queryFn: () => api.get<Summary>("/dashboard/summary") });
  const channels = useQuery({
    queryKey: ["dashboard", "channels"],
    queryFn: () => api.get<ChannelRow[]>("/dashboard/channels", { days: 7 }),
  });
  const status = useQuery({
    queryKey: ["dashboard", "status-breakdown"],
    queryFn: () => api.get<StatusRow[]>("/dashboard/status-breakdown"),
  });
  const today = useQuery({ queryKey: ["dashboard", "today"], queryFn: () => api.get<TodayBoard>("/dashboard/today") });
  const recent = useQuery({
    queryKey: ["dashboard", "recent"],
    queryFn: () => api.get<RecentRow[]>("/dashboard/recent-requests", { limit: 8 }),
  });

  const data = summary.data;
  /* The summary needs `requests.view`; a role without it gets one clear state, not four empty tiles. */
  const forbidden = summary.isError && summary.error instanceof ApiError_ && summary.error.code === "forbidden";
  const yesterday = data?.requests_yesterday ?? 0;
  const todayDelta = data && yesterday > 0 ? Math.round(((data.requests_today - yesterday) / yesterday) * 100) : null;
  const speedDelta = data?.avg_first_response_delta_minutes ?? null;

  const totals = (channels.data ?? []).reduce(
    (acc, row) => ({
      whatsapp: acc.whatsapp + row.whatsapp,
      call: acc.call + row.call,
      email: acc.email + row.email,
      manual: acc.manual + row.manual,
    }),
    { whatsapp: 0, call: 0, email: 0, manual: 0 },
  );
  const peak = Math.max(1, ...(channels.data ?? []).flatMap((row) => CHANNELS.map((c) => row[c])));

  const longDate = new Intl.DateTimeFormat(i18n.language === "ar" ? "ar-EG" : "en-EG", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date());

  return (
    <>
      <PageHeader
        title={t("dashboard.title")}
        subtitle={
          <span>
            {longDate} · {t("dashboard.subtitle")}
          </span>
        }
        actions={
          <Button variant="primary" onClick={() => setDialog(true)}>
            {t("dashboard.newRequest")}
          </Button>
        }
      />

      {today.data ? (
        <Card className="mb-5">
          <SectionTitle>{t("today.title")}</SectionTitle>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <div className="mb-1 text-[13px] font-bold text-ink">
                {t("today.overdue")} · {today.data.overdue_requests?.count ?? 0}
              </div>
              {today.data.overdue_requests?.items?.length ?? 0 === 0 ? <p className="text-[12.5px] text-muted">{t("today.empty")}</p> : null}
              {(today.data.overdue_requests?.items ?? []).map((row) => (
                <Link key={row.id} to={`/inbox/${row.id}`} className="block py-1 text-[13px] text-primary">
                  #{row.number} — {row.subject}
                </Link>
              ))}
            </div>
            <div>
              <div className="mb-1 text-[13px] font-bold text-ink">
                {t("today.followups")} · {today.data.lead_followups?.count ?? 0}
              </div>
              {today.data.lead_followups?.items?.length ?? 0 === 0 ? <p className="text-[12.5px] text-muted">{t("today.empty")}</p> : null}
              {(today.data.lead_followups?.items ?? []).map((lead) => (
                <Link key={lead.id} to={`/leads/${lead.id}`} className="block py-1 text-[13px] text-primary">
                  {lead.name} — {lead.mobile ?? ""}
                </Link>
              ))}
            </div>
            <div>
              <div className="mb-1 text-[13px] font-bold text-ink">
                {t("today.installments")} · {today.data.installments_due?.count ?? 0}
              </div>
              {today.data.installments_due?.items?.length ?? 0 === 0 ? <p className="text-[12.5px] text-muted">{t("today.empty")}</p> : null}
              {(today.data.installments_due?.items ?? []).map((inst) => (
                <div key={inst.id} className="py-1 text-[13px] text-ink">
                  {inst.policy_no ?? "—"} — {inst.due_date}
                </div>
              ))}
            </div>
          </div>
        </Card>
      ) : null}

      {forbidden ? (
        <StateBlock kind="forbidden" />
      ) : (
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label={t("dashboard.kpiToday")}
          value={data ? formatNumber(data.requests_today) : undefined}
          hint={
            todayDelta !== null && data
              ? {
                  text: t(todayDelta >= 0 ? "dashboard.vsYesterday" : "dashboard.vsYesterdayDown", {
                    n: formatNumber(Math.abs(todayDelta)),
                  }),
                  tone: todayDelta >= 0 ? "g" : "r",
                }
              : undefined
          }
          icon={<Inbox size={18} strokeWidth={1.8} />}
          tone="b"
        />
        <Kpi
          label={t("dashboard.kpiFirstResponse")}
          value={
            data?.avg_first_response_minutes == null ? "—" : `${formatNumber(Math.round(data.avg_first_response_minutes))} ${t("dashboard.minutes")}`
          }
          hint={
            speedDelta == null
              ? undefined
              : {
                  text: t(speedDelta >= 0 ? "dashboard.fasterBy" : "dashboard.slowerBy", { n: formatNumber(Math.abs(Math.round(speedDelta))) }),
                  tone: speedDelta >= 0 ? "g" : "a",
                }
          }
          icon={<Clock3 size={18} strokeWidth={1.8} />}
          tone="t"
        />
        <Kpi
          label={t("dashboard.kpiSla")}
          value={data ? formatPercent(data.sla_compliance_pct ?? null) : undefined}
          hint={
            data
              ? { text: t("dashboard.slaBreaches", { n: formatNumber(data.overdue ?? 0) }), tone: (data.overdue ?? 0) > 0 ? "r" : "g" }
              : undefined
          }
          icon={<Gauge size={18} strokeWidth={1.8} />}
          tone="g"
        />
        <Kpi
          label={t("dashboard.kpiOverdue")}
          value={data ? formatNumber(data.overdue ?? 0) : undefined}
          hint={data ? { text: t("dashboard.ofOpen", { n: formatNumber(data.open_requests ?? 0) }), tone: "a" } : undefined}
          icon={<TriangleAlert size={18} strokeWidth={1.8} />}
          tone="r"
          // The number is useless without the list behind it: «متأخرة 7» is a question, and this card is the
          // only place that answers it. A real `<Link>` (not a click handler on a div) so it is keyboard
          // reachable, middle-clickable, and has an accessible name that says where it goes.
          to="/inbox?filter=overdue"
          linkLabel={t("dashboard.overdueAria", { n: formatNumber(data?.overdue ?? 0) })}
        />
      </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <SectionTitle
            action={
              <div className="flex items-center gap-3 text-[12px] text-muted">
                {CHANNELS.map((channel) => (
                  <span key={channel} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: CHANNEL_COLOR[channel] }} />
                    {t(`channel.${channel}`)} <span className="tabular-nums">{formatNumber(totals[channel])}</span>
                  </span>
                ))}
              </div>
            }
          >
            {t("dashboard.byChannel")}
          </SectionTitle>
          {channels.isPending ? (
            <StateBlock kind="loading" />
          ) : channels.isError ? (
            <StateBlock kind="error" action={<Button onClick={() => void channels.refetch()}>{t("common.retry")}</Button>} />
          ) : (
            <ChannelBars rows={channels.data ?? []} peak={peak} />
          )}
        </Card>

        <Card>
          <SectionTitle>{t("dashboard.statusCard")}</SectionTitle>
          {status.isPending ? (
            <StateBlock kind="loading" />
          ) : (
            <ul className="space-y-3">
              {(status.data ?? []).map((row) => (
                <li key={row.key}>
                  <div className="mb-1 flex items-center justify-between text-[13px]">
                    <span className="font-semibold text-ink">{t(`status.${row.key}`)}</span>
                    <span className="tabular-nums text-muted">{formatNumber(row.count)}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-pill bg-[#EDEFF6]">
                    <div
                      className="h-full rounded-pill transition-all duration-150"
                      style={{
                        width: `${Math.max(2, row.pct)}%`,
                        background:
                          row.key === "overdue" ? "var(--r-ink)" : row.key === "new" ? "var(--primary)" : "var(--g-ink)",
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card className="mt-5" padded={false}>
        <div className="px-[20px] pt-[18px]">
          <SectionTitle
            action={
              <button type="button" className="text-[13px] font-semibold text-primary hover:underline" onClick={() => navigate("/inbox")}>
                {t("nav.inbox")}
              </button>
            }
          >
            {t("dashboard.recent")}
          </SectionTitle>
        </div>
        <div className="px-[20px] pb-[18px]">
          <DataTable
            loading={recent.isPending}
            rows={recent.data ?? []}
            keyOf={(row) => row.id}
            onRowClick={(row) => navigate(`/inbox/${row.id}`)}
            rowTone={(row) => (row.is_overdue ? "warn" : undefined)}
            columns={[
              {
                id: "number",
                head: t("dashboard.colRequest"),
                width: 96,
                render: (row) => <span className="font-bold text-primary">#{row.number}</span>,
              },
              { id: "employee", head: t("dashboard.colEmployee"), width: 170, render: (row) => row.employee_name },
              { id: "company", head: t("dashboard.colCompany"), width: 170, render: (row) => row.client_name },
              {
                id: "coverage",
                head: t("dashboard.colCoverage"),
                width: 190,
                render: (row) => (
                  <span className="text-[13px] text-muted">
                    {[row.insurer, row.insurance_type].filter(Boolean).join(" · ") || "—"}
                  </span>
                ),
              },
              { id: "channel", head: t("dashboard.colChannel"), width: 110, render: (row) => t(`channel.${row.channel}`) },
              {
                id: "status",
                head: t("dashboard.colStatus"),
                width: 170,
                render: (row) =>
                  row.is_overdue ? (
                    <span className="text-[13px] font-bold text-[var(--r-ink)]">
                      {t("status.overdue")}
                      {row.sla_hours_late ? ` ${formatNumber(row.sla_hours_late)} ${t("common.hours")}` : ""}
                    </span>
                  ) : (
                    <Badge tone={row.status === "resolved" ? "g" : row.status === "new" ? "b" : "a"}>{t(`status.${row.status}`)}</Badge>
                  ),
              },
              {
                id: "activity",
                head: t("leads.lastActivity"),
                width: 90,
                align: "end",
                render: (row) => <span className="text-[12px] text-muted">{relativeTime(row.last_activity_at)}</span>,
              },
            ]}
          />
        </div>
      </Card>

      {dialog ? <NewRequestDialog onClose={() => setDialog(false)} onCreated={(id) => navigate(`/inbox/${id}`)} /> : null}
    </>
  );
}

function Kpi({
  label,
  value,
  hint,
  icon,
  tone,
  to,
  linkLabel,
}: {
  label: string;
  value?: string;
  hint?: { text: string; tone: "g" | "a" | "b" | "r" };
  icon: React.ReactNode;
  tone: "b" | "g" | "a" | "r" | "t";
  /** When set, the whole card is a link to the filtered view this number describes. */
  to?: string;
  linkLabel?: string;
}) {
  const card = (
    <Card className="flex h-full items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-muted">{label}</div>
        {value === undefined ? <div className="skeleton mt-2 h-8 w-24" /> : <div className="mt-1.5 text-kpi font-bold tabular-nums text-ink">{value}</div>}
        {hint ? (
          <div
            className="mt-1.5 flex items-center gap-1 text-[12px] font-semibold"
            style={{
              color:
                hint.tone === "g"
                  ? "var(--g-ink)"
                  : hint.tone === "r"
                    ? "var(--r-ink)"
                    : hint.tone === "a"
                      ? "var(--a-ink)"
                      : "var(--muted)",
            }}
          >
            {hint.tone === "g" ? <ArrowUpRight size={14} strokeWidth={2} /> : null}
            {hint.tone === "r" ? <ArrowDownRight size={14} strokeWidth={2} /> : null}
            {hint.text}
          </div>
        ) : null}
      </div>
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px]" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
        {icon}
      </span>
    </Card>
  );
  if (!to) return card;
  return (
    <Link
      to={to}
      aria-label={linkLabel}
      data-testid="kpi-overdue-link"
      className="block h-full rounded-card transition hover:-translate-y-[1px] hover:shadow-card focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
    >
      {card}
    </Link>
  );
}

function ChannelBars({ rows, peak }: { rows: ChannelRow[]; peak: number }) {
  const { i18n, t } = useTranslation();
  const weekday = new Intl.DateTimeFormat(i18n.language === "ar" ? "ar-EG" : "en-GB", { weekday: "short" });
  return (
    <div className="flex h-[236px] items-end gap-3">
      {rows.map((row) => {
        const total = CHANNELS.reduce((sum, channel) => sum + row[channel], 0);
        return (
          <div key={row.date} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
            <div className="flex w-full flex-1 items-end justify-center gap-[3px]">
              {CHANNELS.map((channel) => (
                <div
                  key={channel}
                  title={`${t(`channel.${channel}`)}: ${row[channel]}`}
                  className="w-full max-w-[13px] rounded-t-[4px] transition-all duration-150"
                  style={{
                    height: `${Math.max(3, (row[channel] / peak) * 100)}%`,
                    background: CHANNEL_COLOR[channel],
                    opacity: total === 0 ? 0.2 : 1,
                  }}
                />
              ))}
            </div>
            <div className="text-[11px] text-muted">{weekday.format(new Date(`${row.date}T00:00:00Z`))}</div>
          </div>
        );
      })}
    </div>
  );
}

function NewRequestDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const [subject, setSubject] = useState("");
  const [mobile, setMobile] = useState("");
  const [clientId, setClientId] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const clients = useQuery({
    queryKey: ["clients", "picker"],
    queryFn: () => api.get<{ items: { id: string; name: string }[] }>("/clients", { page_size: 50 }),
  });

  async function submit() {
    if (subject.trim().length < 3 || !clientId) {
      setFailure(t("newLead.required"));
      return;
    }
    setBusy(true);
    try {
      const created = await api.post<{ id: string }>("/requests", {
        subject: subject.trim(),
        client_id: clientId,
        channel: "manual",
        contact_mobile: mobile || undefined,
      });
      onCreated(created.id);
      onClose();
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-[rgba(16,24,40,.5)] p-4" role="dialog" aria-modal="true">
      <Card className="w-full max-w-[460px]">
        <SectionTitle>{t("dashboard.newRequest")}</SectionTitle>
        <label className="label" htmlFor="nr-client">
          {t("dashboard.colCompany")}
        </label>
        <select id="nr-client" className="field" value={clientId} onChange={(event) => setClientId(event.target.value)}>
          <option value="">{clients.isPending ? t("common.loading") : t("common.none")}</option>
          {(clients.data?.items ?? []).map((client) => (
            <option key={client.id} value={client.id}>
              {client.name}
            </option>
          ))}
        </select>
        <label className="label mt-4" htmlFor="nr-subject">
          {t("inbox.request")}
        </label>
        <textarea id="nr-subject" className="field min-h-[86px]" value={subject} onChange={(event) => setSubject(event.target.value)} />
        <label className="label mt-4" htmlFor="nr-mobile">
          {t("newLead.mobile")}
        </label>
        <input id="nr-mobile" className="field" dir="ltr" value={mobile} onChange={(event) => setMobile(event.target.value)} />
        {failure ? <p className="mt-3 text-[12px] font-semibold text-[var(--r-ink)]">{failure}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} onClick={() => void submit()}>
            {t("common.confirm")}
          </Button>
        </div>
      </Card>
    </div>
  );
}
