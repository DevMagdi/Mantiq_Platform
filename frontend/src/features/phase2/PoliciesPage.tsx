/**
 * Screen 12 — policies, payment plan and commissions (Phase 2). Shipped in full but gated
 * by `phase2.enabled`, so the data path is real as soon as the flag is on.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, CalendarClock, CheckCircle2, Download, FileCheck2, FilePlus2, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Modal } from "@/components/Modal";
import { Badge, Button, Card, DataTable, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { downloadGet, saveFile } from "@/lib/api/download";
import { useToast } from "@/components/Toast";
import { daysUntil, formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { AnyPage, InstallmentPlan } from "@/lib/api/views";
import type { components } from "@/lib/api/schema";
import { NewPolicyDialog } from "./NewPolicyDialog";
import { Phase2Screen } from "./phase2Gate";

type PolicyRow = components["schemas"]["PolicyOut"];
type PolicyKpis = components["schemas"]["PolicyKpisOut"];

export default function PoliciesPage() {
  return (
    <Phase2Screen>
      <PoliciesContent />
    </Phase2Screen>
  );
}

/** What the API answers from `POST /policies/{id}/issue` (the number that was printed + the file). */
type IssuedPolicy = { status: string; policy_no: string; document: { filename: string; size: number; checksum: string } };

function PoliciesContent() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [openPlan, setOpenPlan] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const issue = useMutation({
    mutationFn: (id: string) => api.post<IssuedPolicy>(`/policies/${id}/issue`),
    onMutate: (id) => setBusyId(id),
    onSuccess: async (issued) => {
      // the row is re-read from the server instead of patched locally: the number and the file are the
      // server's facts, and a stale "draft" badge after a successful issue is the kind of bug a manager
      // reports as "الإصدار مش شغال"
      await queryClient.invalidateQueries({ queryKey: ["policies"] });
      toast(t("phase2.issued", { policy: issued.policy_no }));
    },
    onError: (error: Error) => toast(error.message, "error"),
    onSettled: () => setBusyId(null),
  });

  async function download(row: PolicyRow) {
    setDownloadingId(String(row.id));
    try {
      saveFile(await downloadGet(`/policies/${row.id}/document`, `${row.policy_no ?? "policy"}.pdf`));
    } catch (error) {
      toast(error instanceof Error ? error.message : t("common.errorBody"), "error");
    } finally {
      setDownloadingId(null);
    }
  }

  const list = useQuery({ queryKey: ["policies"], queryFn: () => api.get<AnyPage>("/policies", { page_size: 50 }) });
  const kpis = useQuery({ queryKey: ["policies", "kpis"], queryFn: () => api.get<PolicyKpis>("/policies/kpis") });
  const rows = (list.data?.items ?? []) as PolicyRow[];

  return (
    <>
      <PageHeader
        title={t("phase2.policies")}
        subtitle={t("phase2.policiesSubtitle")}
        actions={
          <div className="flex items-center gap-2">
            {/* the pipeline is a different job from booking the policy it produces; both doors stay */}
            <Button icon={<FileCheck2 size={15} strokeWidth={1.8} />} onClick={() => navigate("/issuing")}>
              {t("phase2.issuingTitle")}
            </Button>
            <Button variant="primary" icon={<FilePlus2 size={15} strokeWidth={1.8} />} onClick={() => setCreating(true)}>
              {t("phase2.newPolicy")}
            </Button>
          </div>
        }
      />

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile
          label={t("phase2.kpiActive")}
          value={formatNumber(kpis.data?.active ?? 0)}
          hint={t("phase2.kpiActiveHint", { n: formatNumber(kpis.data?.expiring_90d ?? 0) })}
          tone="g"
          icon={<FileCheck2 size={18} strokeWidth={1.8} />}
        />
        <Tile
          label={t("phase2.kpiMonth")}
          value={formatNumber(kpis.data?.installments_this_month ?? 0)}
          hint={t("phase2.kpiMonthHint", { n: formatNumber(kpis.data?.installments_this_month ?? 0) })}
          tone="b"
          icon={<CalendarClock size={18} strokeWidth={1.8} />}
        />
        <Tile
          label={t("phase2.kpiLate")}
          value={formatNumber(kpis.data?.late_installments ?? 0)}
          hint={t("phase2.kpiLateHint")}
          tone={Number(kpis.data?.late_installments ?? 0) > 0 ? "r" : "g"}
          icon={<TriangleAlert size={18} strokeWidth={1.8} />}
        />
        <Tile
          label={t("phase2.kpiCommission")}
          value={kpis.data?.expected_commission != null ? formatMoney(kpis.data.expected_commission, kpis.data.currency) : "—"}
          hint={t("phase2.kpiCommissionHint")}
          tone="t"
          icon={<Banknote size={18} strokeWidth={1.8} />}
        />
      </div>

      <Card padded={false}>
        <div className="px-[20px] pt-[18px]">
          <SectionTitle>{t("phase2.colPolicy")}</SectionTitle>
        </div>
        <div className="px-[20px] pb-[18px]">
          {list.isPending ? (
            <StateBlock kind="loading" />
          ) : list.isError ? (
            <StateBlock kind="error" action={<Button onClick={() => void list.refetch()}>{t("common.retry")}</Button>} />
          ) : (
            <DataTable
              rows={rows}
              keyOf={(row) => String(row.id)}
              onRowClick={(row) => setOpenPlan(String(row.id))}
              empty={<StateBlock kind="empty" title={t("phase2.policies")} />}
              columns={[
                {
                  id: "policy",
                  head: t("phase2.colPolicy"),
                  width: 170,
                  render: (row) => (
                    <div>
                      <p className="text-[13px] font-bold text-ink">{row.policy_no ?? "—"}</p>
                      <p className="text-[11.5px] text-muted">{row.insurer?.name ?? "—"}</p>
                    </div>
                  ),
                },
                {
                  id: "client",
                  head: t("phase2.colClient"),
                  width: 160,
                  // the name, not the id: this column printed an em dash for every policy until R7
                  render: (row) => <span className="text-[12.5px] text-ink">{row.client?.name ?? "—"}</span>,
                },
                { id: "coverage", head: t("phase2.colCoverage"), width: 150, render: (row) => row.insurance_type?.name ?? "—" },
                { id: "premium", head: t("phase2.colPremium"), width: 140, align: "end", render: (row) => formatMoney(row.premium_amount, row.currency) },
                { id: "payment", head: t("phase2.colPayment"), width: 110, render: (row) => row.payment_plan ?? "—" },
                {
                  id: "next",
                  head: t("phase2.colNext"),
                  width: 150,
                  render: (row) => {
                    const left = daysUntil(row.end_date ?? null);
                    if (left === null) return "—";
                    return (
                      <span className={`text-[12.5px] font-semibold ${left <= 90 ? "text-[var(--a-ink)]" : "text-muted"}`}>
                        {t("clients.renewalInDays", { n: formatNumber(left) })} · {formatDate(row.end_date)}
                      </span>
                    );
                  },
                },
                {
                  id: "status",
                  head: t("phase2.colStatus"),
                  width: 110,
                  align: "end",
                  // `phase2.issued` is the *toast* ("تم إصدار الوثيقة {policy}…"), so the badge has its own
                  // namespace: reading the status straight out of the screen's strings printed that whole
                  // sentence, uninterpolated, in a 110px cell (found while wiring the R7 e2e)
                  render: (row) => (
                    <Badge tone={row.status === "issued" ? "g" : row.status === "cancelled" ? "r" : "a"}>
                      {t(`phase2.policyStatus.${row.status}`, { defaultValue: row.status })}
                    </Badge>
                  ),
                },
                {
                  id: "document",
                  head: t("phase2.colDocument"),
                  width: 130,
                  align: "end",
                  // one cell, one action: a draft offers «إصدار», an issued policy offers «تحميل». Nothing
                  // renders a download link for a file that does not exist yet.
                  render: (row) =>
                    row.document ? (
                      <Button
                        size="sm"
                        icon={<Download size={14} strokeWidth={1.8} />}
                        busy={downloadingId === String(row.id)}
                        onClick={(event) => {
                          event.stopPropagation();
                          void download(row);
                        }}
                      >
                        {t("common.download")}
                      </Button>
                    ) : row.status === "draft" ? (
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<FilePlus2 size={14} strokeWidth={1.8} />}
                        busy={busyId === String(row.id)}
                        onClick={(event) => {
                          event.stopPropagation();
                          issue.mutate(String(row.id));
                        }}
                      >
                        {t("phase2.issueAction")}
                      </Button>
                    ) : (
                      <span className="text-[12px] text-muted">—</span>
                    ),
                },
              ]}
            />
          )}
        </div>
      </Card>

      {openPlan ? <InstallmentsDialog policyId={openPlan} onClose={() => setOpenPlan(null)} /> : null}
      {creating ? <NewPolicyDialog onClose={() => setCreating(false)} /> : null}
    </>
  );
}

function InstallmentsDialog({ policyId, onClose }: { policyId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [payingId, setPayingId] = useState<string | null>(null);

  const plan = useQuery({
    queryKey: ["policies", policyId, "installments"],
    queryFn: () => api.get<InstallmentPlan>(`/policies/${policyId}/installments`),
  });

  /*
   * The state comes from the API now, not from comparing dates in the browser (R7). Two reasons: the row is
   * *persisted* as late by the server so a report can count it, and a screen that re-derives state is a screen
   * that disagrees with the ledger the moment the clock crosses midnight.
   */
  const pay = useMutation({
    mutationFn: (installmentId: string) => api.post<{ installment: { status: string } }>(`/policies/${policyId}/installments/${installmentId}/pay`),
    onMutate: (installmentId) => setPayingId(installmentId),
    onSuccess: async () => {
      // the plan, the list (a paid row may change the KPIs) and the commission are all server facts
      await queryClient.invalidateQueries({ queryKey: ["policies"] });
      // …and so is the receipt this payment just wrote: the ledger, the cashbox, AR and the executive
      // dashboard all read it, and a stale cashbox after a payment is exactly the P1 the round forbids
      await queryClient.invalidateQueries({ queryKey: ["finance"] });
      await queryClient.invalidateQueries({ queryKey: ["executive"] });
      toast(t("phase2.paidNow"));
    },
    onError: (error: Error) => {
      toast(error.message, "error");
      // a 409 means somebody else recorded it first: re-read instead of showing a stale «دفع»
      void plan.refetch();
    },
    onSettled: () => setPayingId(null),
  });

  const items = plan.data?.items ?? [];
  const totals = plan.data?.totals;
  const commission = plan.data?.commission;

  return (
    <Modal
      title={t("phase2.installments", { policy: plan.data?.policy.policy_no ?? "—" })}
      onClose={onClose}
      width={620}
      actions={
        <Button variant="primary" onClick={onClose}>
          {t("common.close")}
        </Button>
      }
    >
      {plan.isPending ? (
        <StateBlock kind="loading" />
      ) : plan.isError ? (
        <StateBlock kind="error" body={t("common.errorBody")} />
      ) : (
        <>
          {totals ? (
            <div className="mb-3 grid grid-cols-3 gap-2 rounded-btn bg-[var(--kanban-col)] p-3 text-center">
              <div>
                <p className="text-[11px] text-muted">{t("phase2.totalPaid")}</p>
                <p className="text-[13px] font-bold tabular-nums text-ink">{formatMoney(totals.paid_amount, plan.data?.policy.currency)}</p>
              </div>
              <div>
                <p className="text-[11px] text-muted">{t("phase2.totalLate")}</p>
                <p className="text-[13px] font-bold tabular-nums" style={{ color: totals.late_amount > 0 ? "var(--r-ink)" : "var(--ink)" }}>
                  {formatMoney(totals.late_amount, plan.data?.policy.currency)}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-muted">{t("phase2.totalOutstanding")}</p>
                <p className="text-[13px] font-bold tabular-nums text-ink">{formatMoney(totals.outstanding_amount, plan.data?.policy.currency)}</p>
              </div>
            </div>
          ) : null}

          {/* the commission is either a number or the words «غير محسوبة» — never a zero standing in for unknown */}
          <div className="mb-3 flex items-center justify-between gap-3 rounded-btn border border-line px-3 py-2">
            <span className="text-[12.5px] font-semibold text-ink">{t("phase2.commission")}</span>
            {commission?.state === "computed" ? (
              <span className="text-[12.5px] font-bold tabular-nums text-ink">
                {formatMoney(commission.amount, plan.data?.policy.currency)}
                {commission.rate != null ? <span className="ms-1 text-[11px] font-normal text-muted">({formatNumber(commission.rate)}%)</span> : null}
              </span>
            ) : (
              <span className="text-[12px] text-muted">
                {t("phase2.commissionNotComputed")}
                {commission?.rate === 0 ? ` — ${t("phase2.commissionZeroRate")}` : ""}
              </span>
            )}
          </div>

          <p className="text-[12.5px] text-muted">{t("phase2.autoInstallments")}</p>
          <ul className="mt-3 max-h-[320px] space-y-1 overflow-auto">
            {items.map((row) => {
              const status = row.status ?? "upcoming";
              const late = status === "late";
              const paid = status === "paid";
              return (
                <li key={String(row.id)} className="row-line flex items-center justify-between gap-3 py-2 text-[13px]">
                  <span className="tabular-nums text-muted">
                    {formatNumber(row.sequence_no ?? 0)} · {formatDate(row.due_date)}
                  </span>
                  <span className="font-semibold text-ink tabular-nums">{formatMoney(row.amount, plan.data?.policy.currency)}</span>
                  <span className="flex items-center gap-2">
                    <Badge tone={paid ? "g" : late ? "r" : "b"}>{paid ? t("phase2.paid") : late ? t("phase2.lateLabel") : t("phase2.upcoming")}</Badge>
                    {/* a paid row has no action; an unpaid one records the payment here, once */}
                    {paid ? (
                      <span className="w-[86px]" aria-hidden />
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<CheckCircle2 size={13} strokeWidth={1.8} />}
                        busy={payingId === String(row.id)}
                        onClick={() => pay.mutate(String(row.id))}
                      >
                        {t("phase2.markPaid")}
                      </Button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Modal>
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
