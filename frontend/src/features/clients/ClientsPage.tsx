/**
 * Screen 8 — clients list. A lead that was converted this month gets a green banner and
 * an `--row-ok` tint, because the whole point of the list is to show the pipeline landing.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Building2, CheckCircle2, CalendarClock, FileSignature } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Avatar, Badge, Button, Card, DataTable, PageHeader, SearchInput, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { daysUntil, formatDate, formatNumber } from "@/lib/format";
import type { ClientPage } from "@/lib/api/views";

type Filter = "all" | "converted" | "company" | "individual" | "expiring";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "clients.filterAll" },
  { value: "converted", label: "clients.filterConverted" },
  { value: "company", label: "clients.filterCompanies" },
  { value: "individual", label: "clients.filterIndividuals" },
  { value: "expiring", label: "clients.filterExpiring" },
];

const RECENT_MS = 30 * 86_400_000;

export default function ClientsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const list = useQuery({
    queryKey: ["clients", "list", filter, query],
    queryFn: () => api.get<ClientPage>("/clients", { q: query || undefined, page_size: 50 }),
  });
  const kpis = useQuery({ queryKey: ["clients", "kpis"], queryFn: () => api.get<Record<string, number | null>>("/clients/kpis") });

  const rows = useMemo(() => {
    const items = list.data?.items ?? [];
    return items.filter((row) => {
      if (filter === "converted") return row.is_converted_from_lead === true;
      if (filter === "company") return row.type === "company";
      if (filter === "individual") return row.type === "individual";
      if (filter === "expiring") {
        const left = daysUntil(row.end_date ?? null);
        return left !== null && left <= 90;
      }
      return true;
    });
  }, [list.data, filter]);

  const justConverted = (list.data?.items ?? []).filter(
    (row) => row.is_converted_from_lead && Date.now() - new Date(row.converted_at ?? 0).getTime() < RECENT_MS,
  );

  return (
    <>
      <PageHeader
        title={t("clients.title")}
        subtitle={t("clients.subtitle")}
        actions={
          <>
            <SearchInput value={query} onChange={setQuery} width={260} placeholder={t("clients.search")} />
            <Button variant="primary" onClick={() => navigate("/leads/new")}>
              {t("clients.newClient")}
            </Button>
          </>
        }
      />

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiTile
          icon={<Building2 size={18} strokeWidth={1.8} />}
          tone="b"
          label={t("clients.kpiTotal")}
          value={formatNumber(kpis.data?.total_clients ?? 0)}
          hint={t("clients.monthDelta", { n: formatNumber(kpis.data?.converted_from_leads ?? 0) })}
        />
        <KpiTile
          icon={<FileSignature size={18} strokeWidth={1.8} />}
          tone="g"
          label={t("clients.kpiConverted")}
          value={formatNumber(kpis.data?.converted_from_leads ?? 0)}
          hint={t("clients.bestSource", { source: "إحالة" })}
        />
        <KpiTile
          icon={<CalendarClock size={18} strokeWidth={1.8} />}
          tone="a"
          label={t("clients.kpiExpiring")}
          value={formatNumber(kpis.data?.expiring_90d ?? 0)}
          hint={t("clients.renewalChance")}
        />
      </div>

      {justConverted.length > 0 ? (
        <div
          className="mb-4 flex flex-wrap items-center gap-2 rounded-card px-4 py-3 text-[13px] font-semibold"
          style={{ background: "var(--g-bg)", color: "var(--g-ink)" }}
        >
          <CheckCircle2 size={16} strokeWidth={2} />
          {justConverted.slice(0, 2).map((row) => (
            <span key={row.id}>
              {t("clients.convertedBanner", { name: row.name })}
              <button type="button" className="ms-2 underline" onClick={() => navigate(`/clients/${row.id}`)}>
                {t("clients.openFile")}
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <Card padded={false}>
        <div className="px-[20px] pt-[18px]">
          <div className="flex flex-wrap items-center gap-1.5 pb-3">
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setFilter(option.value)}
                className="rounded-pill px-3.5 py-1.5 text-[12.5px] font-semibold transition duration-150"
                style={
                  filter === option.value
                    ? { background: "var(--primary)", color: "#fff" }
                    : { background: "var(--kanban-col)", color: "var(--muted)" }
                }
                aria-pressed={filter === option.value}
              >
                {t(option.label)}
              </button>
            ))}
          </div>
        </div>
        <div className="px-[20px] pb-[18px]">
          {list.isError ? (
            <StateBlock kind="error" action={<Button onClick={() => void list.refetch()}>{t("common.retry")}</Button>} />
          ) : (
            <DataTable
              loading={list.isPending}
              rows={rows}
              keyOf={(row) => row.id ?? ""}
              onRowClick={(row) => navigate(`/clients/${row.id}`)}
              rowTone={(row) => (row.is_converted_from_lead ? "ok" : undefined)}
              empty={<StateBlock kind="empty" title={t("clients.empty")} />}
              columns={[
                {
                  id: "client",
                  head: t("clients.colClient"),
                  width: 260,
                  render: (row) => (
                    <div className="flex items-center gap-2.5">
                      <Avatar name={row.name} size={32} />
                      <div className="min-w-0">
                        <p className="truncate text-[13.5px] font-bold text-ink">{row.name}</p>
                        <p className="text-[11.5px] text-muted">{row.since_date ? t("clients.since", { year: String(row.since_date).slice(0, 4) }) : "—"}</p>
                      </div>
                    </div>
                  ),
                },
                {
                  id: "coverage",
                  head: t("clients.colCoverage"),
                  width: 220,
                  render: (row) =>
                    row.insurance_type || row.insurer ? (
                      <span className="text-[12.5px] text-muted">
                        {t("clients.coverageLine", { type: row.insurance_type ?? "—", insurer: row.insurer ?? "—" })}
                      </span>
                    ) : (
                      <span className="text-[12.5px] text-muted">—</span>
                    ),
                },
                {
                  id: "staff",
                  head: t("clients.colStaff"),
                  width: 110,
                  render: (row) => <span className="tabular-nums text-ink">{formatNumber(row.insured_count ?? 0)}</span>,
                },
                { id: "owner", head: t("clients.colOwner"), width: 140, render: (row) => row.account_owner_name ?? "—" },
                {
                  id: "source",
                  head: t("clients.colSource"),
                  width: 160,
                  render: (row) =>
                    row.is_converted_from_lead && row.converted_at ? (
                      <Badge tone="g">{t("clients.fromLead", { date: formatDate(row.converted_at) })}</Badge>
                    ) : (
                      <span className="text-[12.5px] text-muted">—</span>
                    ),
                },
                {
                  id: "status",
                  head: t("clients.colStatus"),
                  width: 170,
                  render: (row) => {
                    const left = daysUntil(row.end_date ?? null);
                    if (left !== null && left <= 90) {
                      return (
                        <span className="text-[12.5px] font-semibold text-[var(--a-ink)]">
                          {t("clients.renewalInDays", { n: formatNumber(left) })}
                        </span>
                      );
                    }
                    return <Badge tone={row.status === "active" ? "g" : "a"}>{t(`coverage.${row.status === "active" ? "active" : "pending"}`)}</Badge>;
                  },
                },
                {
                  id: "actions",
                  head: "",
                  width: 70,
                  align: "end",
                  render: () => <ArrowLeft size={17} strokeWidth={1.8} className="text-line-2" />,
                },
              ]}
            />
          )}
        </div>
      </Card>
    </>
  );
}

function KpiTile({
  icon,
  tone,
  label,
  value,
  hint,
}: {
  icon: ReactNode;
  tone: "b" | "g" | "a" | "r" | "t";
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Card className="flex items-start justify-between gap-3">
      <div>
        <div className="text-[13px] text-muted">{label}</div>
        <div className="mt-1.5 text-kpi font-bold tabular-nums text-ink">{value}</div>
        <div className="mt-1 text-[12px] font-semibold text-muted">{hint}</div>
      </div>
      <span className="grid h-9 w-9 place-items-center rounded-[10px]" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
        {icon}
      </span>
    </Card>
  );
}
