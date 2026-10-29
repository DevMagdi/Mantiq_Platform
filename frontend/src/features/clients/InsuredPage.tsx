/**
 * Screen 11 — insured employees + the Excel import. The upload is a two-step flow by design:
 * the API returns a dry-run report first, nothing is written until the operator confirms (§9.11).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FileSpreadsheet, Upload, UserPlus, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import { Modal } from "@/components/Modal";
import { Avatar, Badge, Button, Card, DataTable, PageHeader, SearchInput, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { formatDate, formatNumber, maskMobile } from "@/lib/format";
import type { ClientRow, ImportJob, InsuredList } from "@/lib/api/views";

export default function InsuredPage() {
  const { clientId = "" } = useParams();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [job, setJob] = useState<ImportJob | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const client = useQuery({ queryKey: ["clients", clientId], queryFn: () => api.get<ClientRow>(`/clients/${clientId}`) });
  const list = useQuery({
    queryKey: ["clients", clientId, "insured", page, query],
    queryFn: () => api.get<InsuredList>(`/clients/${clientId}/insured`, { page, page_size: 25, q: query || undefined }),
  });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<ImportJob>(`/clients/${clientId}/insured/import`, form);
    },
    onSuccess: (result) => setJob(result),
    onError: (error: Error) => toast(error.message, "error"),
  });

  const commit = useMutation({
    mutationFn: (jobId: string) => api.post<ImportJob>(`/import-jobs/${jobId}/commit`),
    onSuccess: async (result) => {
      setJob(result);
      await queryClient.invalidateQueries({ queryKey: ["clients", clientId, "insured"] });
      await queryClient.invalidateQueries({ queryKey: ["clients", clientId, "overview"] });
      toast(t("clients.insured.report"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const kpis = list.data?.kpis ?? {};
  const total = list.data?.page.total ?? 0;

  return (
    <>
      <PageHeader
        title={t("clients.insured.title")}
        subtitle={t("clients.insured.subtitle", { client: client.data?.name ?? "—", n: formatNumber(total) })}
        actions={
          <>
            <SearchInput value={query} onChange={setQuery} width={260} placeholder={t("clients.search")} />
            <Button icon={<Upload size={15} strokeWidth={1.8} />} onClick={() => fileInput.current?.click()} busy={upload.isPending}>
              {t("clients.insured.importExcel")}
            </Button>
            <Button variant="primary" icon={<UserPlus size={15} strokeWidth={1.8} />} onClick={() => setAddOpen(true)}>
              {t("clients.insured.add")}
            </Button>
          </>
        }
      />

      <input
        ref={fileInput}
        type="file"
        accept=".xlsx"
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) upload.mutate(file);
          event.target.value = "";
        }}
      />

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Tile label={t("clients.insured.kpiCovered")} hint={t("clients.insured.kpiCoveredHint")} value={formatNumber(kpis.active ?? 0)} tone="g" icon={<CheckCircle2 size={18} strokeWidth={1.8} />} />
        <Tile label={t("clients.insured.kpiPending")} hint={t("clients.insured.kpiPendingHint")} value={formatNumber(kpis.pending ?? 0)} tone="a" icon={<FileSpreadsheet size={18} strokeWidth={1.8} />} />
        <Tile label={t("clients.insured.kpiRequests")} hint={t("clients.insured.kpiRequestsHint", { n: formatNumber(kpis.ended ?? 0) })} value={formatNumber(kpis.total ?? 0)} tone="b" icon={<UserPlus size={18} strokeWidth={1.8} />} />
      </div>

      <Card padded={false}>
        <div className="px-[20px] pt-[18px]">
          <SectionTitle
            action={
              total > 25 ? (
                <div className="flex items-center gap-2 text-[12.5px] text-muted">
                  <button type="button" className="font-semibold text-primary disabled:text-line-2" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>
                    {t("common.back")}
                  </button>
                  <span>
                    {formatNumber(page)} / {formatNumber(Math.max(1, Math.ceil(total / 25)))}
                  </span>
                  <button
                    type="button"
                    className="font-semibold text-primary disabled:text-line-2"
                    disabled={page * 25 >= total}
                    onClick={() => setPage((value) => value + 1)}
                  >
                    {t("common.open")}
                  </button>
                </div>
              ) : null
            }
          >
            {t("clients.file.tabs.staff")}
          </SectionTitle>
        </div>
        <div className="px-[20px] pb-[18px]">
          {list.isPending ? (
            <StateBlock kind="loading" />
          ) : list.isError ? (
            <StateBlock kind="error" action={<Button onClick={() => void list.refetch()}>{t("common.retry")}</Button>} />
          ) : (
            <DataTable
              rows={list.data?.page.items ?? []}
              keyOf={(row) => String(row.id)}
              empty={
                <StateBlock
                  kind="empty"
                  title={t("clients.insured.title")}
                  body={t("clients.insured.dropHint")}
                  action={<Button variant="primary" onClick={() => fileInput.current?.click()}>{t("clients.insured.importExcel")}</Button>}
                />
              }
              columns={[
                {
                  id: "name",
                  head: t("clients.insured.colName"),
                  render: (row) => (
                    <div className="flex items-center gap-2.5">
                      <Avatar name={row.name} size={30} />
                      <div className="min-w-0">
                        <p className="truncate text-[13px] font-bold text-ink">{row.name}</p>
                        {row.job_title ? <p className="truncate text-[11.5px] text-muted">{row.job_title}</p> : null}
                      </div>
                    </div>
                  ),
                },
                {
                  id: "mobile",
                  head: t("clients.insured.colMobile"),
                  width: 140,
                  render: (row) => <span className="tabular-nums text-muted">{maskMobile(row.mobile, 1)}</span>,
                },
                { id: "dept", head: t("clients.insured.colDept"), width: 150, render: (row) => row.department ?? "—" },
                {
                  id: "status",
                  head: t("clients.insured.colStatus"),
                  width: 130,
                  render: (row) => (
                    <Badge tone={row.coverage_status === "active" ? "g" : row.coverage_status === "pending" ? "a" : "t"}>
                      {t(`coverage.${row.coverage_status}`)}
                    </Badge>
                  ),
                },
                {
                  id: "end",
                  head: t("clients.colEnd"),
                  width: 130,
                  align: "end",
                  render: (row) => <span className="text-[12.5px] text-muted">{row.coverage_end ? formatDate(row.coverage_end) : "—"}</span>,
                },
              ]}
            />
          )}
        </div>
      </Card>

      {job ? (
        <Modal
          title={job.status === "completed" ? t("clients.insured.report") : t("clients.insured.preview")}
          onClose={() => setJob(null)}
          width={620}
          actions={
            job.status === "completed" ? (
              <Button variant="primary" onClick={() => setJob(null)}>
                {t("common.close")}
              </Button>
            ) : (
              <>
                <Button onClick={() => setJob(null)}>{t("common.cancel")}</Button>
                <Button variant="primary" busy={commit.isPending} disabled={(job.total_rows ?? 0) === 0} onClick={() => commit.mutate(job.id)}>
                  {t("clients.insured.confirm")}
                </Button>
              </>
            )
          }
        >
          <p className="text-[13px] text-muted">{t("clients.insured.previewBody")}</p>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <Mini
              label={t("clients.insured.willCreate")}
              value={job.status === "completed" ? (job.stats?.created ?? 0) : (job.stats?.will_create ?? 0)}
              tone="g"
            />
            <Mini
              label={t("clients.insured.willUpdate")}
              value={job.status === "completed" ? (job.stats?.updated ?? 0) : (job.stats?.will_update ?? 0)}
              tone="b"
            />
            <Mini label={t("clients.insured.rejected")} value={job.stats?.rejected ?? 0} tone="r" />
          </div>
          <div className="mt-4 max-h-[260px] overflow-auto rounded-btn border border-line">
            <table className="w-full text-[12.5px]">
              <tbody>
                {(job.error_report ?? []).length === 0 ? (
                  <tr>
                    <td className="px-3 py-2 text-muted">
                      {job.filename ?? "—"} · {formatNumber(job.total_rows ?? 0)} {t("clients.insured.row")}
                    </td>
                  </tr>
                ) : null}
                {(job.error_report ?? []).map((row, index) => (
                  <tr key={index} className="row-line">
                    <td className="px-3 py-2 tabular-nums text-muted">{t("clients.insured.row")} {String(row.row ?? index + 1)}</td>
                    <td className="px-3 py-2 font-semibold text-[var(--r-ink)]">
                      <span className="inline-flex items-center gap-1">
                        <X size={13} strokeWidth={2} />
                        {String(row.message ?? row.error ?? "—")}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-muted">{String(row.name ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 flex items-center gap-1.5 text-[12.5px] text-muted">
            <Download size={14} strokeWidth={1.8} />
            {t("clients.insured.report")}
          </p>
        </Modal>
      ) : null}

      {addOpen ? <AddInsuredDialog clientId={clientId} onClose={() => setAddOpen(false)} onSaved={() => void list.refetch()} /> : null}
    </>
  );
}

function Tile({ label, value, hint, tone, icon }: { label: string; value: string; hint: string; tone: "b" | "g" | "a" | "r" | "t"; icon: ReactNode }) {
  return (
    <Card className="flex items-start justify-between gap-3">
      <div>
        <div className="text-[13px] text-muted">{label}</div>
        <div className="mt-1.5 text-kpi font-bold tabular-nums text-ink">{value}</div>
        <div className="mt-1 text-[12px] text-muted">{hint}</div>
      </div>
      <span className="grid h-9 w-9 place-items-center rounded-[10px]" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
        {icon}
      </span>
    </Card>
  );
}

function Mini({ label, value, tone }: { label: string; value: number; tone: "g" | "b" | "r" }) {
  return (
    <div className="rounded-btn px-3 py-2.5" style={{ background: `var(--${tone}-bg)`, color: `var(--${tone}-ink)` }}>
      <div className="text-[11.5px] font-semibold">{label}</div>
      <div className="mt-0.5 text-[20px] font-bold tabular-nums">{formatNumber(value)}</div>
    </div>
  );
}

function AddInsuredDialog({ clientId, onClose, onSaved }: { clientId: string; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [form, setForm] = useState({ name: "", mobile: "", department: "", job_title: "" });
  const create = useMutation({
    mutationFn: () =>
      api.post(`/clients/${clientId}/insured`, {
        name: form.name.trim(),
        mobile: form.mobile.replace(/\D/g, "") || undefined,
        department: form.department || undefined,
        job_title: form.job_title || undefined,
      }),
    onSuccess: () => {
      toast(t("clients.insured.add"));
      onSaved();
      onClose();
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  return (
    <Modal
      title={t("clients.insured.add")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={create.isPending} disabled={form.name.trim().length < 2} onClick={() => create.mutate()}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="ins-name">{t("clients.insured.colName")}</label>
      <input id="ins-name" className="field" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <label className="label mt-3" htmlFor="ins-mobile">{t("clients.insured.colMobile")}</label>
      <input id="ins-mobile" className="field" dir="ltr" placeholder="01012345678" value={form.mobile} onChange={(event) => setForm({ ...form, mobile: event.target.value })} />
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="ins-dept">{t("clients.insured.colDept")}</label>
          <input id="ins-dept" className="field" value={form.department} onChange={(event) => setForm({ ...form, department: event.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="ins-job">{t("clients.file.contact")}</label>
          <input id="ins-job" className="field" value={form.job_title} onChange={(event) => setForm({ ...form, job_title: event.target.value })} />
        </div>
      </div>
    </Modal>
  );
}
