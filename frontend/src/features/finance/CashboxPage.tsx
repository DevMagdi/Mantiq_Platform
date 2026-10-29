/**
 * R10 — the office cashbox. `balance` counts **posted cash rows only**: a bank transfer is real money but it
 * is not in the drawer at closing time, and a cashbox total that silently includes it cannot be counted.
 * Every other method is reported next to it, so nothing is hidden — it is just not called cash (ADR-030 §5).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, Vault } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge, Button, Card, DataTable, Kpi, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { ApiError_, api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { formatDate, formatMoney } from "@/lib/format";
import type { components } from "@/lib/api/schema";

type Cashbox = components["schemas"]["CashboxOut"];

export default function CashboxPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [openingAmount, setOpeningAmount] = useState("");
  const [openingNote, setOpeningNote] = useState("");
  const cashbox = useQuery({ queryKey: ["finance", "cashbox"], queryFn: () => api.get<Cashbox>("/finance/cashbox") });
  const openingMut = useMutation({
    mutationFn: () =>
      api.post("/finance/cashbox/opening", {
        amount: Number(openingAmount),
        effective_at: new Date().toISOString(),
        note: openingNote || null,
      }),
    onSuccess: () => {
      toast(t("erp.opening"));
      void queryClient.invalidateQueries({ queryKey: ["finance", "cashbox"] });
    },
    onError: (e) => toast(e instanceof ApiError_ ? e.message : String(e), "error"),
  });

  if (cashbox.isPending) return <StateBlock kind="loading" />;
  if (cashbox.isError)
    return <StateBlock kind="error" action={<Button onClick={() => void cashbox.refetch()}>{t("common.retry")}</Button>} />;

  const data = cashbox.data;

  return (
    <>
      <PageHeader
        title={t("finance.cashbox")}
        subtitle={t("finance.cashboxSubtitle")}
        actions={
          <Badge tone={data.balance >= 0 ? "g" : "r"}>
            <span className="inline-flex items-center gap-1.5">
              <Vault size={13} strokeWidth={1.8} />
              {formatMoney(data.balance)}
            </span>
          </Badge>
        }
      />

      {data.is_net_movements_only ? (
        <div role="note" className="mb-4 rounded-card border border-line bg-card px-4 py-3 text-[13px] font-semibold text-ink">
          {t("finance.cashboxNetNote")} ({data.balance_note})
        </div>
      ) : null}

      {/*
       * R10.1 — the balance is a *net movement*, and the screen has to say so. An accountant reading
       * "رصيد الصندوق" assumes an opening balance plus a daily close; this module has neither (no opening
       * balance exists in the model, and nothing closes the drawer at end of day), so the number is
       * accumulated from the first posted cash row and the page states that in one sentence instead of
       * letting the reader assume the other thing.
       */}
      <Card className="mb-3 flex items-start gap-2.5">
        <Info size={16} strokeWidth={1.9} className="mt-[2px] shrink-0 text-muted" />
        <p className="text-[12.5px] leading-[20px] text-muted">
          {data.since
            ? t("finance.cashboxBasis", { since: formatDate(data.since) })
            : t("finance.cashboxBasisNoHistory")}
        </p>
      </Card>

      {!data.is_net_movements_only && data.opening_effective_at ? (
        <div role="note" className="mb-4 rounded-card border border-line bg-card px-4 py-3 text-[13px] font-semibold text-ink">
          {t("erp.openingCurrent", { amount: formatMoney(data.opening_balance), date: formatDate(data.opening_effective_at) })}
        </div>
      ) : null}

      {can("finance.post") ? (
        <Card className="mb-4">
          <SectionTitle>{t("erp.opening")}</SectionTitle>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-[13px] text-muted">
              {t("erp.openingAmount")}
              <input
                type="number"
                min="0"
                step="0.01"
                value={openingAmount}
                onChange={(e) => setOpeningAmount(e.target.value)}
                className="mt-1 block rounded-card border border-line bg-card px-3 py-2 text-ink"
              />
            </label>
            <label className="text-[13px] text-muted">
              {t("erp.openingNote")}
              <input
                value={openingNote}
                onChange={(e) => setOpeningNote(e.target.value)}
                className="mt-1 block rounded-card border border-line bg-card px-3 py-2 text-ink"
              />
            </label>
            <Button disabled={openingMut.isPending || !openingAmount} onClick={() => openingMut.mutate()}>
              {t("erp.openingSet")}
            </Button>
          </div>
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label={t("finance.cashNetMovement")}
          value={formatMoney(data.balance)}
          delta={t("finance.asOf", { date: formatDate(data.as_of) })}
          tone="b"
        />
        <Kpi label={t("finance.inToday")} value={formatMoney(data.today_in)} delta={t("finance.outToday", { amount: formatMoney(data.today_out) })} tone="g" />
        <Kpi label={t("finance.inMonth")} value={formatMoney(data.month_in)} delta={t("finance.outMonth", { amount: formatMoney(data.month_out) })} tone="t" />
        <Kpi label={t("finance.cashInTotal")} value={formatMoney(data.cash_in_total)} delta={t("finance.cashOutTotal", { amount: formatMoney(data.cash_out_total) })} tone="a" />
      </div>

      <Card className="mt-4">
        <SectionTitle>{t("finance.byMethod")}</SectionTitle>
        <DataTable
          columns={[
            { id: "method", head: t("finance.method"), render: (row) => <span className="font-semibold text-ink">{t(`finance.methodValue.${row.method}`)}</span> },
            { id: "in", head: t("finance.moneyIn"), align: "end", render: (row) => formatMoney(row.in_total) },
            { id: "out", head: t("finance.moneyOut"), align: "end", render: (row) => formatMoney(row.out_total) },
            { id: "balance", head: t("finance.balance"), align: "end", render: (row) => formatMoney(row.balance) },
          ]}
          rows={data.by_method ?? []}
          keyOf={(row) => row.method}
        />
      </Card>

      <Card className="mt-4">
        <SectionTitle>{t("finance.movements")}</SectionTitle>
        <DataTable
          columns={[
            { id: "no", head: t("finance.entryNo"), render: (row) => <span className="font-semibold text-ink">{row.entry_no}</span> },
            { id: "type", head: t("finance.type"), render: (row) => t(`finance.entryType.${row.entry_type}`) },
            { id: "direction", head: t("finance.direction"), render: (row) => <Badge tone={row.direction === "in" ? "g" : "a"}>{t(`finance.directionValue.${row.direction}`)}</Badge> },
            { id: "amount", head: t("finance.amount"), align: "end", render: (row) => formatMoney(row.amount) },
            { id: "client", head: t("finance.client"), render: (row) => row.client_name ?? "—" },
            { id: "posted", head: t("finance.postedAt"), render: (row) => formatDate(row.posted_at) },
            { id: "status", head: t("finance.status"), render: (row) => <Badge tone={row.status === "posted" ? "g" : "r"}>{t(`finance.statusValue.${row.status}`)}</Badge> },
          ]}
          rows={data.movements ?? []}
          keyOf={(row) => String(row.id)}
          empty={<StateBlock kind="empty" title={t("finance.noMovements")} />}
        />
      </Card>
    </>
  );
}
