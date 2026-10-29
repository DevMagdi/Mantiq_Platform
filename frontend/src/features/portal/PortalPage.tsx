/**
 * Screen 7 — the client-owner portal: no sidebar, max 1100px, a dark `--sidebar` banner,
 * three KPI cards, the employee request thread and a 330px documents column.
 * The API never returns internal notes, rep names or commissions to this role (spec §6).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, LogOut, MessageSquare } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { Badge, Button, Card, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { downloadGet, saveFile } from "@/lib/api/download";
import { useAuth } from "@/lib/auth";
import { formatDate, formatNumber, relativeTime, responseRating } from "@/lib/format";
import type { PortalOverview, PortalRequestItem } from "@/lib/api/views";
import type { components } from "@/lib/api/schema";

/** The sheet's praise label is derived from the measured average, never hardcoded. */
const RATING_KEY: Record<NonNullable<ReturnType<typeof responseRating>>, "portal.ratingExcellent" | "portal.ratingGood" | "portal.ratingSlow"> = {
  excellent: "portal.ratingExcellent",
  good: "portal.ratingGood",
  slow: "portal.ratingSlow",
};

type PortalRequests = components["schemas"]["PortalRequestsOut"];
type PortalDocument = components["schemas"]["PortalDocumentOut"];
type PortalPolicy = components["schemas"]["PortalPolicyOut"];

export default function PortalPage() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);

  const overview = useQuery({ queryKey: ["portal", "overview"], queryFn: () => api.get<PortalOverview>("/portal/overview") });
  const requests = useQuery({ queryKey: ["portal", "requests"], queryFn: () => api.get<PortalRequests>("/portal/requests", { page_size: 10 }) });
  const documents = useQuery({ queryKey: ["portal", "documents"], queryFn: () => api.get<PortalDocument[]>("/portal/documents") });
  const policies = useQuery({ queryKey: ["portal", "policies"], queryFn: () => api.get<PortalPolicy[]>("/portal/policies") });
  const [downloading, setDownloading] = useState<string | null>(null);

  /**
   * A portal download has to carry the bearer token, so an `<a href>` to the API route would answer 401 in
   * the owner's browser. Every file on this screen goes through here.
   */
  async function download(path: string, name: string) {
    setDownloading(path);
    try {
      saveFile(await downloadGet(path, name));
    } catch (error) {
      toast(error instanceof Error ? error.message : t("common.errorBody"), "error");
    } finally {
      setDownloading(null);
    }
  }

  const create = useMutation({
    mutationFn: (payload: { subject: string; body: string }) => api.post<{ request_id: string; number: number }>("/portal/requests", payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["portal"] });
      setOpen(false);
      toast(t("portal.opened"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  if (overview.isPending) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-8">
        <StateBlock kind="loading" />
      </div>
    );
  }
  if (overview.isError) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-8">
        <StateBlock kind="error" action={<Button onClick={() => void overview.refetch()}>{t("common.retry")}</Button>} />
      </div>
    );
  }

  const data = overview.data;
  const kpis = data.kpis;
  const rating = responseRating(kpis.avg_response_minutes);
  const ratingLabel = rating ? t(RATING_KEY[rating]) : "—";
  const premiums = kpis.premiums ?? {};
  const summary = data.policy_summary ?? {};
  const covered = data.covered_count ?? 0;

  return (
    <div className="min-h-screen bg-bg">
      <header className="text-white" style={{ background: "var(--sidebar)" }}>
        <div className="mx-auto flex max-w-[1100px] flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="min-w-0">
            <p className="text-[12.5px] text-white/60">{t("portal.brand")}</p>
            <h1 className="mt-0.5 truncate text-[19px] font-bold">{data.client.name ?? "—"}</h1>
            <p className="mt-1 text-[12.5px] text-white/70">
              {t("portal.greeting")} · {user?.full_name ?? ""} · {t("portal.role")}
            </p>
          </div>
          <div className="flex items-center gap-5">
            <div className="text-center" style={{ minWidth: 44 }}>
              <div className="text-[44px] font-bold leading-none tabular-nums">{formatNumber(covered)}</div>
              <div className="mt-1 text-[11px] text-white/60">{t("leads.insured_count")}</div>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="flex items-center gap-1.5 rounded-btn px-3 py-2 text-[12.5px] font-semibold text-white/80 transition duration-150 hover:bg-white/10 hover:text-white"
            >
              <LogOut size={15} strokeWidth={1.8} />
              {t("portal.signOut")}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1100px] px-6 py-[26px]">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_330px]">
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Kpi
                label={t("portal.kpiRequests")}
                value={formatNumber(kpis.requests_this_month ?? 0)}
                hint={t("portal.kpiRequestsHint", { n: formatNumber((requests.data?.items ?? []).filter((row) => row.status !== "resolved").length) })}
              />
              <Kpi
                label={t("portal.kpiResponse")}
                value={kpis.avg_response_minutes == null ? "—" : `${formatNumber(Math.round(kpis.avg_response_minutes))} ${t("common.minutes")}`}
                hint={ratingLabel}
              />
              <Kpi
                label={t("portal.kpiPremiums")}
                value={premiums.next_due?.amount != null ? formatMoney(premiums.next_due.amount) : "—"}
                hint={t("portal.kpiPremiumsNext", { date: premiums.next_due?.due_date ? formatDate(premiums.next_due.due_date) : "—" })}
              />
            </div>

            <Card>
              <SectionTitle
                action={
                  <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
                    {t("portal.open")}
                  </Button>
                }
              >
                {t("portal.recent")}
              </SectionTitle>
              {requests.isPending ? (
                <StateBlock kind="loading" />
              ) : (requests.data?.items ?? []).length === 0 ? (
                <StateBlock kind="empty" title={t("portal.empty")} />
              ) : (
                <ul className="-mx-1">
                  {(requests.data?.items ?? []).map((row: PortalRequestItem) => (
                    <li key={String(row.id)} className="row-line flex items-start gap-3 px-1 py-3">
                      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full" style={{ background: "var(--primary-soft)", color: "var(--primary-ink)" }}>
                        <MessageSquare size={15} strokeWidth={1.8} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[13px] font-bold text-ink">{row.subject}</span>
                          <Badge tone={row.status === "resolved" ? "g" : "a"}>{t(`status.${row.status}`)}</Badge>
                        </div>
                        <p className="mt-1 line-clamp-2 text-[12.5px] text-muted">{row.messages?.[0]?.body ?? t("portal.replying")}</p>
                        <p className="mt-1 text-[11.5px] text-muted">
                          #{row.number} · {relativeTime(row.created_at)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="space-y-4">
            <Card>
              <SectionTitle>{t("portal.contract")}</SectionTitle>
              <p className="text-[13px] leading-[21px] text-ink">
                {t("portal.policyLine", {
                  type: summary.insurance_type ?? "—",
                  kind: t("portal.groupKind"),
                  insurer: summary.insurer ?? "—",
                  end: summary.end_date ? formatDate(summary.end_date) : "—",
                })}
              </p>
              <dl className="mt-3 space-y-2 text-[12.5px]">
                <div className="flex items-center justify-between border-t border-line pt-2">
                  <dt className="text-muted">{t("clients.colStaff")}</dt>
                  <dd className="font-semibold text-ink">{formatNumber(covered)}</dd>
                </div>
                <div className="flex items-center justify-between border-t border-line pt-2">
                  <dt className="text-muted">{t("clients.file.kpiRenewal")}</dt>
                  <dd className="font-semibold text-ink">
                    {data.renewal_in_days == null ? "—" : `${formatNumber(data.renewal_in_days)} ${t("clients.file.days")}`}
                  </dd>
                </div>
                <div className="flex items-center justify-between border-t border-line pt-2">
                  <dt className="text-muted">{t("clients.file.kpiResponse")}</dt>
                  <dd className="font-semibold text-ink">
                    {kpis.avg_response_minutes == null ? "—" : `${formatNumber(Math.round(kpis.avg_response_minutes))} ${t("common.minutes")}`}
                  </dd>
                </div>
              </dl>
            </Card>

            <Card padded={false}>
              <div className="px-[20px] pt-[18px]">
                <SectionTitle>{t("portal.policies")}</SectionTitle>
              </div>
              <div className="px-[20px] pb-[18px]">
                {policies.isPending ? (
                  <StateBlock kind="loading" />
                ) : (policies.data ?? []).length === 0 ? (
                  <p className="text-[12.5px] text-muted">{t("common.emptyTitle")}</p>
                ) : (
                  <ul className="space-y-2">
                    {(policies.data ?? []).map((policy) => {
                      const file = policy.document;
                      return (
                      <li key={String(policy.id)} className="border-t border-line pt-2 first:border-t-0 first:pt-0">
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-[12.5px] font-bold text-ink" dir="ltr">
                              {policy.policy_no ?? "—"}
                            </p>
                            <p className="truncate text-[11px] text-muted">
                              {policy.insurance_type ?? "—"} · {policy.insurer ?? "—"}
                            </p>
                            <p className="text-[11px] text-muted">
                              {formatDate(policy.start_date)} → {formatDate(policy.end_date)}
                            </p>
                          </div>
                          {file?.download_url ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<Download size={13} strokeWidth={1.8} />}
                              busy={downloading === file.download_url}
                              onClick={() => void download(String(file.download_url), String(file.filename ?? "policy.pdf"))}
                            >
                              {t("common.download")}
                            </Button>
                          ) : (
                            <Badge tone="a">{t(`phase2.${policy.status}`)}</Badge>
                          )}
                        </div>
                      </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </Card>

            <Card padded={false}>
              <div className="px-[20px] pt-[18px]">
                <SectionTitle>{t("portal.documents")}</SectionTitle>
              </div>
              <div className="px-[20px] pb-[18px]" style={{ minWidth: 0 }}>
                {documents.isPending ? (
                  <StateBlock kind="loading" />
                ) : (documents.data ?? []).length === 0 ? (
                  <p className="text-[12.5px] text-muted">{t("common.emptyTitle")}</p>
                ) : (
                  <ul className="space-y-2">
                    {(documents.data ?? []).map((doc) => (
                      <li key={String(doc.id)} className="flex items-center gap-2.5">
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[10px]" style={{ background: "var(--b-bg)", color: "var(--b-ink)" }}>
                          <FileText size={15} strokeWidth={1.8} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[12.5px] font-semibold text-ink">{doc.filename}</p>
                          <p className="text-[11px] text-muted">
                            {doc.request_number ? `#${doc.request_number} · ` : ""}
                            {relativeTime(doc.created_at)}
                          </p>
                        </div>
                        {doc.download_url ? (
                          <button
                            type="button"
                            className="text-[12px] font-semibold text-primary"
                            aria-label={t("common.download")}
                            onClick={() => void download(String(doc.download_url), String(doc.filename ?? "document.pdf"))}
                          >
                            <Download size={14} strokeWidth={1.8} />
                          </button>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Card>

            <Card>
              <SectionTitle>{t("portal.kpiPremiums")}</SectionTitle>
              <dl className="space-y-2 text-[12.5px]">
                <Ledger label={t("phase2.kpiMonth")} value={premiums.total} />
                <Ledger label={t("phase2.paid")} value={premiums.paid} />
                <Ledger label={t("phase2.kpiLate")} value={premiums.overdue} tone="r" />
              </dl>
            </Card>
          </div>
        </div>
      </main>

      {open ? (
        <OpenRequestDialog
          busy={create.isPending}
          onClose={() => setOpen(false)}
          onSubmit={(payload) => create.mutate(payload)}
        />
      ) : null}
    </div>
  );
}

function Ledger({ label, value, tone }: { label: string; value?: number | null; tone?: "r" }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center justify-between border-t border-line pt-2">
      <dt className="text-muted">{label}</dt>
      <dd className="font-semibold tabular-nums" style={{ color: tone === "r" && Number(value ?? 0) > 0 ? "var(--r-ink)" : "var(--ink)" }}>
        {value == null ? t("common.none") : formatMoney(value)}
      </dd>
    </div>
  );
}

function formatMoney(value: string | number | null | undefined): string {
  return new Intl.NumberFormat("ar-EG", { style: "currency", currency: "EGP", maximumFractionDigits: 0 }).format(Number(value ?? 0));
}

function Kpi({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Card>
      <div className="text-[12.5px] text-muted">{label}</div>
      <div className="mt-1.5 text-[22px] font-bold tabular-nums text-ink">{value}</div>
      <div className="mt-1 text-[11.5px] text-muted">{hint}</div>
    </Card>
  );
}

function OpenRequestDialog({
  busy,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: { subject: string; body: string }) => void;
}) {
  const { t } = useTranslation();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const valid = subject.trim().length >= 3 && body.trim().length >= 5;
  return (
    <Modal
      title={t("portal.open")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={!valid} onClick={() => onSubmit({ subject: subject.trim(), body: body.trim() })}>
            {t("common.confirm")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="portal-subject">
        {t("portal.subjectLabel")}
      </label>
      <input id="portal-subject" className="field" value={subject} onChange={(event) => setSubject(event.target.value)} />
      <label className="label mt-3" htmlFor="portal-body">
        {t("portal.bodyLabel")}
      </label>
      <textarea id="portal-body" className="field min-h-[110px]" value={body} onChange={(event) => setBody(event.target.value)} />
      {!valid ? <p className="mt-2 text-[12px] text-muted">{t("newLead.required")}</p> : null}
    </Modal>
  );
}
