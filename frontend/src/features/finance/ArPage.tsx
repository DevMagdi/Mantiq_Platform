/**
 * R10 — customer receivables with aging. The buckets are the API's own (`0–30 / 31–60 / 61–90 / 90+`, plus
 * `current` for money that is not due yet — folding that into "overdue" would be a wrong number).
 * Clicking a client opens the drill-down: the exact installments and the receipts already taken.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { Badge, Button, Card, DataTable, Kpi, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { components } from "@/lib/api/schema";

type ArSummary = components["schemas"]["ArSummaryOut"];
type ArClientPage = components["schemas"]["ArClientPageOut"];
type ArClientRow = components["schemas"]["ArClientRowOut"];
type ArClientDetail = components["schemas"]["ArClientDetailOut"];

const BUCKETS = ["current", "d0_30", "d31_60", "d61_90", "d90_plus"] as const;

export default function ArPage() {
  const { t } = useTranslation();
  const [openClient, setOpenClient] = useState<ArClientRow | null>(null);

  const summary = useQuery({ queryKey: ["finance", "ar", "summary"], queryFn: () => api.get<ArSummary>("/finance/ar/summary") });
  const clients = useQuery({
    queryKey: ["finance", "ar", "clients"],
    queryFn: () => api.get<ArClientPage>("/finance/ar/clients", { page_size: 50 }),
  });

  if (summary.isPending || clients.isPending) return <StateBlock kind="loading" />;
  if (summary.isError || clients.isError) return <StateBlock kind="error" />;

  const data = summary.data;
  const bucketOf = (row: ArClientRow, key: string) => (row.buckets ?? []).find((bucket) => bucket.bucket === key)?.amount ?? 0;

  return (
    <>
      <PageHeader
        title={t("finance.ar")}
        subtitle={t("finance.arSubtitle", { date: data.as_of })}
        actions={<Badge tone={data.overdue_total > 0 ? "a" : "g"}>{t("finance.clientsOverdue", { n: formatNumber(data.overdue_clients_count) })}</Badge>}
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t("finance.outstanding")} value={formatMoney(data.outstanding_total)} tone="b" />
        <Kpi label={t("finance.overdue")} value={formatMoney(data.overdue_total)} tone="r" />
        <Kpi label={t("finance.notDue")} value={formatMoney(data.current_total)} tone="g" />
        <Kpi label={t("finance.clientsWithBalance")} value={formatNumber(data.clients_count)} tone="t" />
      </div>

      <Card className="mt-4">
        <SectionTitle>{t("finance.aging")}</SectionTitle>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
          {(data.buckets ?? []).map((bucket) => (
            <div key={bucket.bucket} className="rounded-[10px] border border-line px-3 py-2.5">
              <div className="text-[12px] font-semibold text-muted">{t(`finance.bucket.${bucket.bucket}`)}</div>
              <div className="mt-1 text-[16px] font-bold tabular-nums text-ink">{formatMoney(bucket.amount)}</div>
              <div className="text-[11.5px] text-muted">{t("finance.installmentsCount", { n: formatNumber(bucket.count) })}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="mt-4">
        <SectionTitle>{t("finance.clients")}</SectionTitle>
        <DataTable
          columns={[
            { id: "client", head: t("finance.client"), render: (row) => <span className="font-semibold text-ink">{row.client_name}</span> },
            { id: "owner", head: t("finance.owner"), render: (row) => row.owner_name ?? "—" },
            { id: "outstanding", head: t("finance.outstanding"), align: "end", render: (row) => formatMoney(row.outstanding_total) },
            { id: "overdue", head: t("finance.overdue"), align: "end", render: (row) => <span style={{ color: row.overdue_total > 0 ? "var(--r-ink)" : undefined }}>{formatMoney(row.overdue_total)}</span> },
            ...BUCKETS.map((bucket) => ({
              id: bucket,
              head: t(`finance.bucket.${bucket}`),
              align: "end" as const,
              render: (row: ArClientRow) => formatMoney(bucketOf(row, bucket)),
            })),
            { id: "oldest", head: t("finance.oldestDue"), render: (row) => (row.oldest_due_date ? formatDate(row.oldest_due_date) : "—") },
          ]}
          rows={clients.data.items ?? []}
          keyOf={(row) => String(row.client_id)}
          onRowClick={(row) => setOpenClient(row)}
          empty={<StateBlock kind="empty" title={t("finance.noReceivables")} />}
        />
      </Card>

      {openClient ? <ClientDetail client={openClient} onClose={() => setOpenClient(null)} /> : null}
    </>
  );
}

function ClientDetail({ client, onClose }: { client: ArClientRow; onClose: () => void }) {
  const { t } = useTranslation();
  const detail = useQuery({
    queryKey: ["finance", "ar", "client", String(client.client_id)],
    queryFn: () => api.get<ArClientDetail>(`/finance/ar/clients/${client.client_id}`),
  });

  return (
    <Modal title={t("finance.clientStatement", { name: client.client_name })} onClose={onClose} width={880}>
      {detail.isPending ? (
        <StateBlock kind="loading" />
      ) : detail.isError ? (
        <StateBlock kind="error" action={<Button onClick={() => void detail.refetch()}>{t("common.retry")}</Button>} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Kpi label={t("finance.outstanding")} value={formatMoney(detail.data.outstanding_total)} tone="b" />
            <Kpi label={t("finance.overdue")} value={formatMoney(detail.data.overdue_total)} tone="r" />
            <Kpi label={t("finance.lastPayment")} value={detail.data.last_payment_at ? formatDate(detail.data.last_payment_at) : t("finance.never")} tone="g" />
          </div>

          <SectionTitle>{t("finance.openInstallments")}</SectionTitle>
          <DataTable
            columns={[
              { id: "policy", head: t("executive.policy"), render: (row) => row.policy_no ?? "—" },
              { id: "seq", head: t("finance.sequence"), align: "end", render: (row) => formatNumber(row.sequence_no) },
              { id: "due", head: t("finance.dueDate"), render: (row) => formatDate(row.due_date) },
              { id: "amount", head: t("finance.amount"), align: "end", render: (row) => formatMoney(row.amount) },
              { id: "days", head: t("finance.daysOverdue"), align: "end", render: (row) => formatNumber(row.days_overdue) },
              { id: "bucket", head: t("finance.aging"), render: (row) => <Badge tone={row.bucket === "current" ? "g" : "a"}>{t(`finance.bucket.${row.bucket}`)}</Badge> },
            ]}
            rows={detail.data.installments ?? []}
            keyOf={(row) => String(row.installment_id)}
            empty={<StateBlock kind="empty" title={t("finance.noReceivables")} />}
          />

          <SectionTitle>{t("finance.receipts")}</SectionTitle>
          <DataTable
            columns={[
              { id: "no", head: t("finance.entryNo"), render: (row) => row.entry_no },
              { id: "posted", head: t("finance.postedAt"), render: (row) => formatDate(row.posted_at) },
              { id: "method", head: t("finance.method"), render: (row) => (row.method ? t(`finance.methodValue.${row.method}`) : "—") },
              { id: "amount", head: t("finance.amount"), align: "end", render: (row) => formatMoney(row.amount) },
              { id: "status", head: t("finance.status"), render: (row) => <Badge tone={row.status === "posted" ? "g" : "r"}>{t(`finance.statusValue.${row.status}`)}</Badge> },
            ]}
            rows={detail.data.payments ?? []}
            keyOf={(row) => String(row.id)}
            empty={<StateBlock kind="empty" title={t("finance.noReceipts")} />}
          />
        </>
      )}
    </Modal>
  );
}
