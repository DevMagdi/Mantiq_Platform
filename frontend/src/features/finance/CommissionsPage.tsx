/**
 * R10 — commissions owed to a rep (or the office) per policy. The payout is a sequence the API enforces:
 * `accrued → approved → paid`, and a second pay is a `409` — so the buttons here follow the same order
 * instead of offering "pay" on a row that has not been approved (ADR-030 §4).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, HandCoins } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useToast } from "@/components/Toast";
import { Badge, Button, Card, DataTable, PageHeader, Segmented, StateBlock, type Tone } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatDate, formatMoney } from "@/lib/format";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth";

type Settlement = components["schemas"]["SettlementOut"];
type SettlementPage = components["schemas"]["SettlementPageOut"];

const STATUS_TONE: Record<string, Tone> = { accrued: "b", approved: "a", paid: "g", cancelled: "r" };

export default function CommissionsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [status, setStatus] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["finance", "commissions", status],
    queryFn: () => api.get<SettlementPage>("/finance/commissions", status ? { status, page_size: 50 } : { page_size: 50 }),
  });

  const act = useMutation({
    mutationFn: (payload: { id: string; action: "approve" | "pay" }) =>
      api.post<Settlement>(`/finance/commissions/${payload.id}/${payload.action}`, payload.action === "pay" ? { method: "bank" } : {}),
    onMutate: (payload) => setBusyId(payload.id),
    onSuccess: async (row, payload) => {
      await queryClient.invalidateQueries({ queryKey: ["finance"] });
      toast(payload.action === "approve" ? t("finance.commissionApproved", { amount: formatMoney(row.amount) }) : t("finance.commissionPaid", { amount: formatMoney(row.amount), entry: row.ledger_entry_no ?? "—" }));
    },
    onError: (error: Error) => toast(error.message, "error"),
    onSettled: () => setBusyId(null),
  });

  const rows = list.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={t("finance.commissions")}
        subtitle={t("finance.commissionsSubtitle")}
        actions={
          <Segmented
            value={status || "all"}
            onChange={setStatus}
            options={[
              { value: "all", label: t("common.all") },
              { value: "accrued", label: t("finance.statusValue.accrued") },
              { value: "approved", label: t("finance.statusValue.approved") },
              { value: "paid", label: t("finance.statusValue.paid") },
            ]}
          />
        }
      />

      <Card>
        {list.isPending ? (
          <StateBlock kind="loading" />
        ) : list.isError ? (
          <StateBlock kind="error" action={<Button onClick={() => void list.refetch()}>{t("common.retry")}</Button>} />
        ) : (
          <DataTable
            columns={[
              { id: "policy", head: t("executive.policy"), render: (row) => <span className="font-semibold text-ink">{row.policy_no ?? "—"}</span> },
              { id: "client", head: t("finance.client"), render: (row) => row.client_name ?? "—" },
              { id: "payee", head: t("finance.payee"), render: (row) => row.payee_name ?? t("finance.office") },
              { id: "amount", head: t("finance.amount"), align: "end", render: (row) => formatMoney(row.amount) },
              { id: "status", head: t("finance.status"), render: (row) => <Badge tone={STATUS_TONE[row.status] ?? "b"}>{t(`finance.statusValue.${row.status}`)}</Badge> },
              { id: "accrued", head: t("finance.accruedAt"), render: (row) => (row.accrued_at ? formatDate(row.accrued_at) : "—") },
              { id: "paid", head: t("finance.paidAt"), render: (row) => (row.paid_at ? formatDate(row.paid_at) : "—") },
              {
                id: "entry",
                head: t("finance.entryNo"),
                render: (row) => row.ledger_entry_no ?? "—",
              },
              {
                id: "actions",
                head: "",
                align: "end",
                render: (row) =>
                  can("commissions.manage") ? (
                    row.status === "accrued" ? (
                      <Button
                        variant="ghost"
                        icon={<CheckCircle2 size={14} strokeWidth={1.8} />}
                        busy={busyId === String(row.id)}
                        onClick={() => act.mutate({ id: String(row.id), action: "approve" })}
                      >
                        {t("finance.approve")}
                      </Button>
                    ) : row.status === "approved" ? (
                      <Button
                        variant="ghost"
                        icon={<HandCoins size={14} strokeWidth={1.8} />}
                        busy={busyId === String(row.id)}
                        onClick={() => act.mutate({ id: String(row.id), action: "pay" })}
                      >
                        {t("finance.pay")}
                      </Button>
                    ) : null
                  ) : null,
              },
            ]}
            rows={rows}
            keyOf={(row) => String(row.id)}
            empty={<StateBlock kind="empty" title={t("finance.noCommissions")} body={t("finance.noCommissionsBody")} />}
          />
        )}
      </Card>
    </>
  );
}
