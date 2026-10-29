/**
 * Screen 14 — reports: 30-day request volume, first-response trend and insurer performance.
 * Compliance colours are the spec's thresholds (≥90 green, 80–89 amber, <80 red, §9.14).
 * The design's satisfaction / full-resolution numbers are not tracked by any model in this phase,
 * so they render as "غير متتبع بعد" instead of a fabricated figure.
 */
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download, FileText, Gauge, Timer, TrendingUp } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Badge, Button, Card, DataTable, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { downloadFile, saveFile } from "@/lib/api/download";
import { formatNumber, formatPercent } from "@/lib/format";
import type { DashboardChannel, DashboardSummary, InsurerPerformance } from "@/lib/api/views";
import { Phase2Screen } from "./phase2Gate";

const COMPLIANCE_COLORS = { good: "#10B981", mid: "#F59E0B", bad: "#F04438" } as const;

function complianceColor(pct: number | null | undefined): string {
  const value = pct ?? 0;
  if (value >= 90) return COMPLIANCE_COLORS.good;
  if (value >= 80) return COMPLIANCE_COLORS.mid;
  return COMPLIANCE_COLORS.bad;
}

export default function ReportsPage() {
  return (
    <Phase2Screen>
      <ReportsContent />
    </Phase2Screen>
  );
}

function ReportsContent() {
  const { t } = useTranslation();
  const toast = useToast();
  const [range, setRange] = useState<7 | 30>(30);
  const [exporting, setExporting] = useState(false);

  /** The workbook is built server-side from the same scoped query this screen reads (spec §6). */
  const exportExcel = async () => {
    setExporting(true);
    try {
      const file = await downloadFile("/exports/insurer-performance");
      saveFile(file);
      toast(
        file.rows === null
          ? t("phase2.exportDonePlain", { file: file.filename })
          : t("phase2.exportDone", { rows: file.rows, file: file.filename }),
        file.truncated ? "warn" : "ok",
      );
      if (file.truncated) toast(t("phase2.exportTruncated"), "warn");
    } catch (error) {
      toast(error instanceof Error ? error.message : t("phase2.exportFailed"), "error");
    } finally {
      setExporting(false);
    }
  };

  const channels = useQuery({
    queryKey: ["dashboard", "channels", range],
    queryFn: () => api.get<DashboardChannel[]>("/dashboard/channels", { days: range }),
  });
  const summary = useQuery({ queryKey: ["dashboard", "summary"], queryFn: () => api.get<DashboardSummary>("/dashboard/summary") });
  const performance = useQuery({
    queryKey: ["reports", "insurer-performance"],
    queryFn: () => api.get<InsurerPerformance[]>("/reports/insurer-performance"),
  });

  const totals = useMemo(() => {
    const rows = channels.data ?? [];
    return rows.reduce(
      (acc, row) => ({
        requests: acc.requests + row.whatsapp + row.call + row.email + row.manual,
        peak: Math.max(acc.peak, row.whatsapp + row.call + row.email + row.manual),
      }),
      { requests: 0, peak: 1 },
    );
  }, [channels.data]);

  const chart = useMemo(
    () =>
      (channels.data ?? []).map((row) => ({
        day: String(row.date).slice(5),
        total: row.whatsapp + row.call + row.email + row.manual,
      })),
    [channels.data],
  );

  return (
    <>
      <PageHeader
        title={t("phase2.reports")}
        subtitle={t("phase2.reportsSubtitle")}
        actions={
          <>
            <div className="inline-flex items-center gap-1 rounded-pill bg-kanban p-1">
              {([7, 30] as const).map((days) => (
                <button
                  key={days}
                  type="button"
                  onClick={() => setRange(days)}
                  className="rounded-pill px-3 py-1 text-[12.5px] font-semibold transition duration-150"
                  style={range === days ? { background: "var(--primary)", color: "#fff" } : { color: "var(--muted)" }}
                  aria-pressed={range === days}
                >
                  {days === 7 ? t("common.today") : t("phase2.last30")}
                </button>
              ))}
            </div>
            <Button icon={<Download size={15} strokeWidth={1.8} />} busy={exporting} onClick={() => void exportExcel()}>
              {t("phase2.exportExcel")}
            </Button>
            <Button icon={<FileText size={15} strokeWidth={1.8} />} title={t("phase2.exportPdfPending")} disabled>
              {t("phase2.exportPdf")}
            </Button>
          </>
        }
      />

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Tile label={t("phase2.last30")} value={formatNumber(totals.requests)} hint={t("phase2.fasterBy")} tone="b" icon={<TrendingUp size={18} strokeWidth={1.8} />} />
        <Tile
          label={t("phase2.avgResolution")}
          value={summary.data?.avg_first_response_minutes == null ? "—" : `${formatNumber(Math.round(summary.data.avg_first_response_minutes))} ${t("common.minutes")}`}
          hint={t("phase2.kpiResolutionNote")}
          tone="t"
          icon={<Timer size={18} strokeWidth={1.8} />}
        />
        <Tile label={t("phase2.satisfaction")} value="—" hint={t("phase2.notTracked")} tone="a" icon={<Gauge size={18} strokeWidth={1.8} />} />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <SectionTitle action={<Badge tone="b">{t("phase2.firstResponseChart")}</Badge>}>{t("phase2.last30")}</SectionTitle>
          {channels.isPending ? (
            <StateBlock kind="loading" />
          ) : channels.isError ? (
            <StateBlock kind="error" action={<Button onClick={() => void channels.refetch()}>{t("common.retry")}</Button>} />
          ) : (
            <div className="h-[260px]" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} width={44} />
                  <Tooltip
                    cursor={{ fill: "var(--primary-soft)" }}
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid var(--line)",
                      fontSize: 12,
                      background: "#fff",
                    }}
                  />
                  <Bar dataKey="total" radius={[6, 6, 0, 0]} maxBarSize={26}>
                    {chart.map((row) => (
                      <Cell key={row.day} fill={row.total >= totals.peak * 0.8 ? "var(--primary)" : "var(--primary-soft)"} stroke="var(--primary)" strokeOpacity={0.35} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card padded={false}>
          <div className="px-[20px] pt-[18px]">
            <SectionTitle>{t("phase2.insurerPerf")}</SectionTitle>
          </div>
          <div className="px-[20px] pb-[18px]">
            <DataTable
              loading={performance.isPending}
              rows={performance.data ?? []}
              keyOf={(row) => row.insurer ?? ""}
              empty={<StateBlock kind="empty" title={t("phase2.insurerPerf")} />}
              columns={[
                { id: "insurer", head: t("phase2.colInsurer"), render: (row) => <span className="text-[13px] font-semibold text-ink">{row.insurer}</span> },
                { id: "requests", head: t("phase2.colRequests"), width: 70, align: "end", render: (row) => formatNumber(row.requests ?? 0) },
                {
                  id: "resolution",
                  head: t("phase2.colResolution"),
                  width: 80,
                  align: "end",
                  render: (row) => (row.avg_first_response_minutes == null ? "—" : `${formatNumber(Math.round(row.avg_first_response_minutes))} ${t("common.minutes")}`),
                },
                {
                  id: "compliance",
                  head: t("phase2.colCompliance"),
                  width: 92,
                  align: "end",
                  render: (row) => (
                    <span className="inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[12px] font-bold tabular-nums text-white" style={{ background: complianceColor(row.sla_pct) }}>
                      {formatPercent(row.sla_pct ?? null)}
                    </span>
                  ),
                },
              ]}
            />
          </div>
        </Card>
      </div>
    </>
  );
}

function Tile({ label, value, hint, tone, icon }: { label: string; value: string; hint: string; tone: "b" | "g" | "a" | "r" | "t"; icon: ReactNode }) {
  return (
    <Card className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-muted">{label}</div>
        <div className="mt-1.5 text-kpi font-bold tabular-nums text-ink">{value}</div>
        <div className="mt-1 text-[12px] text-muted">{hint}</div>
      </div>
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px]" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
        {icon}
      </span>
    </Card>
  );
}
