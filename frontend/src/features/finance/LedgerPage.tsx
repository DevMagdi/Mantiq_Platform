/**
 * R10 — the ledger. Append-only on purpose: the only action a posted row offers is **void**, with a reason,
 * and the row stays visible as `void` afterwards (ADR-030 §2). Posting a manual movement (an expense, an
 * adjustment) writes the same kind of row the installment receipt does, so the two cannot drift apart.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Badge, Button, Card, DataTable, PageHeader, Segmented, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatDate, formatMoney } from "@/lib/format";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth";

type LedgerPage = components["schemas"]["LedgerPageOut"];
type VoidResult = components["schemas"]["LedgerVoidOut"];
type LedgerEntry = components["schemas"]["LedgerEntryOut"];

const TYPES = ["premium_receivable", "commission_receivable", "commission_payable", "expense", "adjustment"] as const;

export default function LedgerPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can, canAny } = useAuth();
  const [status, setStatus] = useState<"" | "posted" | "void">("");
  const [posting, setPosting] = useState(false);
  const [voiding, setVoiding] = useState<LedgerEntry | null>(null);
  const [reason, setReason] = useState("");

  const ledger = useQuery({
    queryKey: ["finance", "ledger", status],
    queryFn: () => api.get<LedgerPage>("/finance/ledger", status ? { status, page_size: 50 } : { page_size: 50 }),
  });

  const voidEntry = useMutation({
    mutationFn: (payload: { id: string; reason: string }) =>
      api.post<VoidResult>(`/finance/ledger/${payload.id}/void`, { reason: payload.reason }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["finance"] });
      /*
       * R10.1 — a void no longer stops at the ledger. The API reopens the installment (or returns the
       * commission payout to `approved`) in the same transaction, so every screen that shows the *plan*
       * or the commissions is stale the moment this succeeds. Invalidating only `finance` left the policy
       * plan still reading "paid" behind a receipt that no longer exists — the inconsistency this round
       * was asked to end, reproduced on screen instead of in the database.
       */
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["policies"] }),
        queryClient.invalidateQueries({ queryKey: ["executive"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
      toast(t("finance.voided", { no: result.entry.entry_no, balance: formatMoney(result.cashbox_balance) }));
      if (result.reopened?.kind === "installment") toast(t("finance.voidedReopenedInstallment"));
      if (result.reopened?.kind === "commission") toast(t("finance.voidedReopenedCommission"));
      setVoiding(null);
      setReason("");
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const rows = ledger.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={t("finance.ledger")}
        subtitle={t("finance.ledgerSubtitle")}
        actions={
          <>
            <Segmented
              value={status || "all"}
              onChange={(next) => setStatus(next === "all" ? "" : (next as "posted" | "void"))}
              options={[
                { value: "all", label: t("common.all") },
                { value: "posted", label: t("finance.posted") },
                { value: "void", label: t("finance.void") },
              ]}
            />
            {can("finance.post") ? (
              <Button variant="primary" icon={<Plus size={15} strokeWidth={2} />} onClick={() => setPosting(true)}>
                {t("finance.newEntry")}
              </Button>
            ) : null}
          </>
        }
      />

      <Card>
        {ledger.isPending ? (
          <StateBlock kind="loading" />
        ) : ledger.isError ? (
          <StateBlock kind="error" action={<Button onClick={() => void ledger.refetch()}>{t("common.retry")}</Button>} />
        ) : (
          <DataTable
            columns={[
              { id: "no", head: t("finance.entryNo"), render: (row) => <span className="font-semibold text-ink">{row.entry_no}</span> },
              { id: "type", head: t("finance.type"), render: (row) => t(`finance.entryType.${row.entry_type}`) },
              {
                id: "direction",
                head: t("finance.direction"),
                render: (row) => <Badge tone={row.direction === "in" ? "g" : "a"}>{t(`finance.directionValue.${row.direction}`)}</Badge>,
              },
              { id: "amount", head: t("finance.amount"), align: "end", render: (row) => formatMoney(row.amount) },
              { id: "client", head: t("finance.client"), render: (row) => row.client_name ?? "—" },
              { id: "method", head: t("finance.method"), render: (row) => (row.method ? t(`finance.methodValue.${row.method}`) : "—") },
              { id: "posted", head: t("finance.postedAt"), render: (row) => formatDate(row.posted_at) },
              {
                id: "status",
                head: t("finance.status"),
                render: (row) => (
                  <Badge tone={row.status === "posted" ? "g" : "r"}>{t(`finance.statusValue.${row.status}`)}</Badge>
                ),
              },
              {
                id: "actions",
                head: "",
                align: "end",
                render: (row) =>
                  row.status === "posted" && canAny("finance.void") ? (
                    <Button
                      variant="ghost"
                      icon={<Ban size={14} strokeWidth={1.8} />}
                      onClick={() => {
                        setVoiding(row);
                        setReason("");
                      }}
                    >
                      {t("finance.void")}
                    </Button>
                  ) : null,
              },
            ]}
            rows={rows}
            keyOf={(row) => String(row.id)}
            empty={<StateBlock kind="empty" title={t("finance.noEntries")} body={t("finance.noEntriesBody")} />}
          />
        )}
      </Card>

      {posting ? <PostEntryDialog onClose={() => setPosting(false)} /> : null}

      {voiding ? (
        <Modal title={t("finance.voidTitle", { no: voiding.entry_no })} onClose={() => setVoiding(null)}>
          <p className="text-[13px] leading-[21px] text-muted">{t("finance.voidBody", { amount: formatMoney(voiding.amount) })}</p>
          <label className="mt-3 block text-[12.5px] font-semibold text-ink" htmlFor="void-reason">
            {t("finance.voidReason")}
          </label>
          <input
            id="void-reason"
            className="input mt-1.5 w-full"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={t("finance.voidReasonHint")}
          />
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setVoiding(null)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              busy={voidEntry.isPending}
              disabled={reason.trim().length < 3}
              onClick={() => voidEntry.mutate({ id: String(voiding.id), reason: reason.trim() })}
            >
              {t("finance.voidConfirm")}
            </Button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}

function PostEntryDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    entry_type: "expense" as (typeof TYPES)[number],
    direction: "out" as "in" | "out",
    amount: "",
    method: "cash" as "cash" | "bank" | "wallet" | "other",
    client_id: "",
    note: "",
  });

  const clients = useQuery({
    queryKey: ["clients", "finance-picker"],
    queryFn: () => api.get<{ items: { id: string; name: string }[] }>("/clients", { page_size: 200 }),
  });

  const post = useMutation({
    mutationFn: () =>
      api.post<LedgerEntry>("/finance/ledger", {
        entry_type: form.entry_type,
        direction: form.direction,
        amount: Number(form.amount),
        method: form.method,
        client_id: form.client_id || null,
        note: form.note || null,
      }),
    onSuccess: async (entry) => {
      await queryClient.invalidateQueries({ queryKey: ["finance"] });
      toast(t("finance.entryPosted", { no: entry.entry_no }));
      onClose();
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  // the type decides the direction: a receipt cannot be money out (the API refuses it, so the form must not offer it)
  const moneyIn = form.entry_type === "premium_receivable" || form.entry_type === "commission_receivable";

  return (
    <Modal title={t("finance.newEntry")} onClose={onClose}>
      <div className="grid gap-3">
        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.type")}
          <select
            className="input mt-1.5 w-full"
            value={form.entry_type}
            onChange={(event) => {
              const next = event.target.value as (typeof TYPES)[number];
              const inMoney = next === "premium_receivable" || next === "commission_receivable";
              const outMoney = next === "commission_payable" || next === "expense";
              setForm((current) => ({
                ...current,
                entry_type: next,
                direction: next === "adjustment" ? current.direction : inMoney ? "in" : outMoney ? "out" : current.direction,
              }));
            }}
          >
            {TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`finance.entryType.${type}`)}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.direction")}
          <select
            className="input mt-1.5 w-full"
            value={form.direction}
            disabled={form.entry_type !== "adjustment"}
            onChange={(event) => setForm((current) => ({ ...current, direction: event.target.value as "in" | "out" }))}
          >
            <option value="in">{t("finance.directionValue.in")}</option>
            <option value="out">{t("finance.directionValue.out")}</option>
          </select>
        </label>

        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.amount")}
          <input
            className="input mt-1.5 w-full"
            type="number"
            min="0.01"
            step="0.01"
            value={form.amount}
            onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
          />
          <span className="mt-1 block text-[11.5px] font-normal text-muted">{t("finance.amountHint")}</span>
        </label>

        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.method")}
          <select
            className="input mt-1.5 w-full"
            value={form.method}
            onChange={(event) => setForm((current) => ({ ...current, method: event.target.value as typeof form.method }))}
          >
            {(["cash", "bank", "wallet", "other"] as const).map((method) => (
              <option key={method} value={method}>
                {t(`finance.methodValue.${method}`)}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.client")}
          <select
            className="input mt-1.5 w-full"
            value={form.client_id}
            onChange={(event) => setForm((current) => ({ ...current, client_id: event.target.value }))}
          >
            <option value="">{t("finance.noClient")}</option>
            {(clients.data?.items ?? []).map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-[12.5px] font-semibold text-ink">
          {t("finance.note")}
          <input
            className="input mt-1.5 w-full"
            value={form.note}
            onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))}
            maxLength={500}
          />
        </label>

        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            variant="primary"
            busy={post.isPending}
            disabled={!Number(form.amount) || Number(form.amount) <= 0 || (moneyIn && form.direction !== "in")}
            onClick={() => post.mutate()}
          >
            {t("finance.post")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
