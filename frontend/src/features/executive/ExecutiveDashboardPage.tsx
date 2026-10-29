/**
 * R10 — the executive control dashboard (screen 15). Every figure on this page is a field of one live
 * response: `GET /executive/dashboard`. Nothing is computed in the browser, nothing is placeholder data,
 * and the one number the office cannot produce yet — a collection target — is rendered as "لا يوجد هدف"
 * instead of a percentage against a denominator somebody invented (ADR-030 §7/§8).
 */
import { useQuery } from "@tanstack/react-query";
import { Banknote, Clock3, Coins, HandCoins, TrendingUp, TriangleAlert, Users, Vault } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge, Card, DataTable, Kpi, PageHeader, SectionTitle, StateBlock, type Tone } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatMoney, formatNumber } from "@/lib/format";
import type { components } from "@/lib/api/schema";

type Dashboard = components["schemas"]["ExecutiveDashboardOut"];

const ALERT_TONE: Record<string, Tone> = { high: "r", medium: "a", low: "b" };

export default function ExecutiveDashboardPage() {
  const { t } = useTranslation();
  const dashboard = useQuery({
    queryKey: ["executive", "dashboard"],
    queryFn: () => api.get<Dashboard>("/executive/dashboard"),
  });

  if (dashboard.isPending) return <StateBlock kind="loading" />;
  if (dashboard.isError) {
    const forbidden = String((dashboard.error as Error)?.message ?? "").includes("صلاحية");
    return <StateBlock kind={forbidden ? "forbidden" : "error"} />;
  }

  const data = dashboard.data;
  const kpis = data.kpis;
  const target = kpis.collections_target;
  const aging = data.ar_aging ?? [];
  const channelMix = data.channel_mix ?? [];
  const sales = data.sales_performance ?? [];
  const renewals = data.renewals_next_30d ?? [];
  const alerts = data.alerts ?? [];
  const agingPeak = Math.max(1, ...aging.map((bucket) => bucket.amount));
  const channelTotal = Math.max(1, channelMix.reduce((total, row) => total + row.count, 0));

  return (
    <>
      <PageHeader
        title={t("executive.title")}
        subtitle={t("executive.subtitle", { period: data.period.label })}
        actions={<Badge tone="b">{data.period.from_date} → {data.period.to_date}</Badge>}
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label={t("executive.collectionsMtd")}
          value={formatMoney(kpis.collections_mtd)}
          delta={target === null ? t("executive.noTarget") : t("executive.ofTarget", { target: formatMoney(target) })}
          tone="g"
          icon={<Banknote size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.outstandingAr")}
          value={formatMoney(kpis.outstanding_ar)}
          delta={t("executive.overdueOfIt", { amount: formatMoney(kpis.overdue_ar) })}
          tone="a"
          icon={<Clock3 size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.cashbox")}
          value={formatMoney(kpis.cashbox_balance)}
          delta={t("executive.cashboxNote")}
          tone="b"
          icon={<Vault size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.commissionsAccrued")}
          value={formatMoney(kpis.commissions_accrued)}
          delta={t("executive.commissionsPaidMtd", { amount: formatMoney(kpis.commissions_paid_mtd) })}
          tone="t"
          icon={<Coins size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.openRequests")}
          value={formatNumber(kpis.open_requests)}
          delta={t("executive.overdueRequests", { n: formatNumber(kpis.overdue_requests) })}
          tone="r"
          icon={<TriangleAlert size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.policiesIssued")}
          value={formatNumber(kpis.policies_issued_mtd)}
          delta={t("executive.leadsThisMonth", { created: formatNumber(kpis.new_leads_mtd), won: formatNumber(kpis.converted_leads_mtd) })}
          tone="g"
          icon={<TrendingUp size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.renewals")}
          value={formatNumber(renewals.length)}
          delta={t("executive.renewalsNote")}
          tone="a"
          icon={<HandCoins size={17} strokeWidth={1.8} />}
        />
        <Kpi
          label={t("executive.salesReps")}
          value={formatNumber(sales.length)}
          delta={t("executive.salesRepsNote")}
          tone="b"
          icon={<Users size={17} strokeWidth={1.8} />}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <SectionTitle>{t("executive.arAging")}</SectionTitle>
          <div className="mt-3 flex flex-col gap-2.5">
            {aging.map((bucket) => (
              <div key={bucket.bucket} className="flex items-center gap-3">
                <span className="w-[104px] shrink-0 text-[12.5px] font-semibold text-muted">
                  {t(`finance.bucket.${bucket.bucket}`)}
                </span>
                <span className="h-2.5 flex-1 overflow-hidden rounded-pill bg-kanban">
                  <span
                    className="block h-full rounded-pill"
                    style={{
                      width: `${Math.max(2, (bucket.amount / agingPeak) * 100)}%`,
                      background: bucket.bucket === "current" ? "var(--b-ink)" : bucket.bucket === "d90_plus" ? "var(--r-ink)" : "var(--a-ink)",
                    }}
                  />
                </span>
                <span className="w-[130px] shrink-0 text-end text-[12.5px] font-semibold tabular-nums text-ink">
                  {formatMoney(bucket.amount)}
                </span>
                <span className="w-[46px] shrink-0 text-end text-[12px] tabular-nums text-muted">{formatNumber(bucket.count)}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <SectionTitle>{t("executive.channelMix")}</SectionTitle>
          <div className="mt-3 flex flex-col gap-2.5">
            {channelMix.map((row) => (
              <div key={row.channel} className="flex items-center gap-3">
                <span className="w-[104px] shrink-0 text-[12.5px] font-semibold text-muted">{t(`executive.channel.${row.channel}`)}</span>
                <span className="h-2.5 flex-1 overflow-hidden rounded-pill bg-kanban">
                  <span
                    className="block h-full rounded-pill"
                    style={{ width: `${row.count === 0 ? 0 : Math.max(2, (row.count / channelTotal) * 100)}%`, background: "var(--primary)" }}
                  />
                </span>
                <span className="w-[80px] shrink-0 text-end text-[12.5px] font-semibold tabular-nums text-ink">{formatNumber(row.count)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <SectionTitle>{t("executive.salesPerformance")}</SectionTitle>
        <DataTable
          columns={[
            { id: "name", head: t("executive.rep"), render: (row) => <span className="font-semibold text-ink">{row.name}</span> },
            { id: "policies", head: t("executive.policies"), align: "end", render: (row) => formatNumber(row.policies_issued_mtd) },
            { id: "premium", head: t("executive.premium"), align: "end", render: (row) => formatMoney(row.premium_mtd) },
            { id: "collected", head: t("executive.collected"), align: "end", render: (row) => formatMoney(row.collected_mtd) },
          ]}
          rows={sales}
          keyOf={(row) => String(row.user_id ?? row.name)}
          empty={<StateBlock kind="empty" title={t("executive.noSales")} />}
        />
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <SectionTitle>{t("executive.renewals30")}</SectionTitle>
          <DataTable
            columns={[
              { id: "policy", head: t("executive.policy"), render: (row) => <span className="font-semibold text-ink">{row.policy_no ?? "—"}</span> },
              { id: "client", head: t("executive.client"), render: (row) => row.client_name ?? "—" },
              { id: "end", head: t("executive.endDate"), render: (row) => row.end_date },
              { id: "days", head: t("executive.daysLeft"), align: "end", render: (row) => formatNumber(row.days_left) },
              { id: "premium", head: t("executive.premium"), align: "end", render: (row) => formatMoney(row.premium_amount) },
            ]}
            rows={renewals}
            keyOf={(row) => String(row.policy_id)}
            empty={<StateBlock kind="empty" title={t("executive.noRenewals")} />}
          />
        </Card>

        <Card>
          <SectionTitle>{t("executive.alerts")}</SectionTitle>
          {alerts.length === 0 ? (
            <StateBlock kind="empty" title={t("executive.noAlerts")} />
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {alerts.map((alert) => (
                <li key={alert.code} className="flex items-center justify-between gap-3 rounded-[10px] border border-line px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold text-ink">{t(`executive.alert.${alert.code}`, { defaultValue: alert.message })}</div>
                    <div className="text-[12px] text-muted">{t(`executive.severity.${alert.severity}`, { defaultValue: alert.severity })}</div>
                  </div>
                  <Badge tone={ALERT_TONE[alert.severity] ?? "b"}>{formatNumber(alert.count)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
