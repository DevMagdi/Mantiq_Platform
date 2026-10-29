/**
 * The Excel/CSV import dialog: upload → read the dry-run → apply it. It never claims a row was saved until
 * the API's own commit report says so, and the numbers it shows are the report's, not a counted list.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { Badge, Button, DataTable } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import type { LeadImportJob } from "@/lib/api/views";

import { canCommit, entriesOf, IMPORT_COLUMNS, previewRows, statOf, summaryChips, type PreviewRow } from "./importModel";

export function ImportLeadsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement | null>(null);
  const [job, setJob] = useState<LeadImportJob | null>(null);
  const [filename, setFilename] = useState("");

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<LeadImportJob>("/leads/import", form);
    },
    onSuccess: (preview) => {
      setJob(preview);
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const commit = useMutation({
    mutationFn: (id: string) => api.post<LeadImportJob>(`/leads/import/${id}/commit`),
    onSuccess: async (report) => {
      setJob(report);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["leads"] }), queryClient.invalidateQueries({ queryKey: ["sales"] }), queryClient.invalidateQueries({ queryKey: ["sales-team"] })]);
      toast(t("leads.importDone", { n: Number(report.stats?.created ?? 0) }));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const applied = job?.status === "completed";
  const chips = summaryChips(job, {
    rows: t("leads.importStatRows"),
    ready: t("leads.importStatReady"),
    created: t("leads.importStatCreated"),
    skipped: t("leads.importStatSkipped"),
    warnings: t("leads.importStatWarnings"),
    errors: t("leads.importStatErrors"),
  });
  const errors = entriesOf(job, "error");
  const warnings = entriesOf(job, "warning");
  const skipped = entriesOf(job, "skipped");

  return (
    <Modal
      title={t("leads.importTitle")}
      width={760}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{applied ? t("common.close") : t("common.cancel")}</Button>
          {applied ? null : (
            <Button variant="primary" busy={commit.isPending} disabled={!canCommit(job)} onClick={() => (job ? commit.mutate(job.id) : undefined)}>
              {t("leads.importApply")}
            </Button>
          )}
        </>
      }
    >
      {job === null ? (
        <div className="space-y-3" data-testid="import-pick">
          <p className="text-[13px] leading-[22px] text-muted">{t("leads.importHint")}</p>
          <ul className="flex flex-wrap gap-1.5">
            {IMPORT_COLUMNS.map((column) => (
              <li key={column.key}>
                <Badge tone={column.required ? "b" : "t"}>
                  {t(`leads.importCol.${column.key}`)}
                  {column.required ? " *" : ""}
                </Badge>
              </li>
            ))}
          </ul>
          <input
            ref={input}
            id="leads-import-file"
            type="file"
            accept=".xlsx,.csv,text/csv"
            className="field py-2 text-[12.5px]"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setFilename(file.name);
              upload.mutate(file);
            }}
          />
          <Button full icon={<Upload size={16} strokeWidth={1.8} />} busy={upload.isPending} disabled={!filename} onClick={() => input.current?.click()}>
            {t("leads.importChoose")}
          </Button>
        </div>
      ) : (
        <div className="space-y-4" data-testid="import-preview">
          <p className="flex items-center gap-2 text-[12.5px] font-semibold text-ink">
            <FileSpreadsheet size={15} strokeWidth={1.8} className="text-[var(--primary-ink)]" />
            <span dir="ltr">{job.filename ?? filename}</span>
          </p>

          <ul className="flex flex-wrap gap-2" data-testid="import-stats">
            {chips.map((chip) => (
              <li
                key={chip.key}
                className="rounded-btn px-2.5 py-1.5 text-[12px] font-semibold"
                style={{
                  background: chip.tone === "bad" ? "var(--r-bg)" : chip.tone === "good" ? "var(--g-bg)" : "var(--kanban-col)",
                  color: chip.tone === "bad" ? "var(--r-ink)" : chip.tone === "good" ? "var(--g-ink)" : "var(--ink)",
                }}
              >
                {chip.label}: <span className="tabular-nums">{chip.value}</span>
              </li>
            ))}
          </ul>

          {applied ? null : (
            <p className="rounded-btn px-3 py-2 text-[12.5px] font-semibold" style={{ background: "var(--a-bg)", color: "var(--a-ink)" }}>
              {t("leads.importPreviewNote", { n: statOf(job, "ready") })}
            </p>
          )}

          <DataTable<PreviewRow>
            columns={[
              {
                id: "name",
                head: t("leads.importCol.name"),
                render: (row) => <span className="font-semibold text-ink">{row.name}</span>,
              },
              {
                id: "mobile",
                head: t("leads.importCol.mobile"),
                render: (row) => <span dir="ltr">{row.mobile || "—"}</span>,
              },
              {
                id: "contact",
                head: t("leads.importCol.position"),
                render: (row) => row.contact,
              },
              {
                id: "insurance",
                head: t("leads.importCol.insurance_type"),
                render: (row) => row.insurance,
              },
              {
                id: "headcount",
                head: t("leads.importCol.headcount"),
                render: (row) => <span className="tabular-nums">{row.headcount}</span>,
              },
              {
                id: "followup",
                head: t("leads.importCol.followup"),
                render: (row) => <span dir="ltr">{row.followup}</span>,
              },
            ]}
            rows={previewRows(job)}
            keyOf={(row) => `${row.name}-${row.mobile}`}
          />

          {errors.length + warnings.length + skipped.length > 0 ? (
            <ul className="space-y-1 text-[12px]" data-testid="import-report">
              {[...errors, ...skipped, ...warnings].slice(0, 12).map((entry, index) => (
                <li key={`${entry.row}-${entry.field}-${index}`} className="flex items-start gap-2 border-t border-line pt-1.5">
                  <span className="shrink-0 font-bold tabular-nums text-muted" dir="ltr">
                    {entry.row ?? "—"}
                  </span>
                  <span className="font-semibold text-ink">{entry.field}</span>
                  <span className={entry.kind === "error" ? "text-[var(--r-ink)]" : "text-muted"}>{entry.message}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Modal>
  );
}
