/**
 * The lead-import report, as data the dialog can render without re-deciding anything.
 *
 * Why a model file: the importer's answer is a *report* (rows that will be created, rows refused, rows
 * kept but adjusted), and the rules about which is which belong to the API. This module only shapes it —
 * so the table, the chips and the commit button all read one description of the same job, and the unit
 * test can pin the shaping without a DOM.
 */
import type { LeadImportJob } from "@/lib/api/views";

/** What a row of the sheet can end up as. The keys are the API's, not a second vocabulary. */
export type ReportEntry = {
  row: number | null;
  field: string;
  message: string;
  kind: "error" | "warning" | "skipped";
};

export const IMPORT_STAGES = ["pick", "preview", "done"] as const;
export type ImportStage = (typeof IMPORT_STAGES)[number];

/** The columns the importer reads, in the order a sheet should present them (both spellings are accepted). */
export const IMPORT_COLUMNS: readonly { key: string; ar: string; en: string; required?: boolean }[] = [
  { key: "name", ar: "الاسم", en: "Name", required: true },
  { key: "mobile", ar: "الموبايل", en: "Mobile", required: true },
  { key: "company", ar: "الشركة", en: "Company" },
  { key: "position", ar: "البوزيشن", en: "Position" },
  { key: "email", ar: "الإيميل", en: "Email" },
  { key: "source", ar: "المصدر", en: "Source" },
  { key: "insurance_type", ar: "نوع التأمين", en: "Insurance type" },
  { key: "headcount", ar: "عدد الأفراد", en: "Headcount" },
  { key: "followup", ar: "تاريخ المتابعة", en: "Follow-up" },
  { key: "rep", ar: "البياع المسؤول", en: "Rep" },
];

export function entriesOf(job: LeadImportJob | null, kind: ReportEntry["kind"]): ReportEntry[] {
  const rows = (job?.error_report ?? []) as unknown as Partial<ReportEntry>[];
  return rows
    .filter((entry) => (entry.kind ?? "error") === kind)
    .map((entry) => ({
      row: typeof entry.row === "number" ? entry.row : null,
      field: String(entry.field ?? ""),
      message: String(entry.message ?? ""),
      kind,
    }));
}

export function statOf(job: LeadImportJob | null, key: string): number {
  return Number(job?.stats?.[key] ?? 0);
}

export type SummaryChip = { key: string; label: string; value: number; tone: "neutral" | "good" | "bad" };

/**
 * The chips above the preview and on the final report: what the file held, and what became of it.
 * `created` only exists after a commit, and zero-count noise is dropped — a manager reads three chips,
 * not five where two always say 0.
 */
export function summaryChips(job: LeadImportJob | null, labels: Record<string, string>): SummaryChip[] {
  const rows: SummaryChip[] = [
    { key: "rows", label: labels.rows ?? "rows", value: job?.total_rows ?? 0, tone: "neutral" },
    { key: "created", label: labels.created ?? "created", value: statOf(job, "created"), tone: "good" },
    { key: "ready", label: labels.ready ?? "ready", value: statOf(job, "ready"), tone: "good" },
    { key: "skipped_duplicate", label: labels.skipped ?? "skipped", value: statOf(job, "skipped_duplicate"), tone: "neutral" },
    { key: "warnings", label: labels.warnings ?? "warnings", value: statOf(job, "warnings"), tone: "neutral" },
    { key: "errors", label: labels.errors ?? "errors", value: statOf(job, "errors"), tone: "bad" },
  ];
  return rows.filter((row) => row.key === "rows" || row.key === "ready" || row.value > 0);
}

/** Only a preview that has something to create may be applied; a sheet of pure errors is not a request. */
export function canCommit(job: LeadImportJob | null): boolean {
  return Boolean(job) && job?.status === "dry_run" && statOf(job, "ready") > 0;
}

export type PreviewRow = {
  name: string;
  mobile: string;
  contact: string;
  insurance: string;
  headcount: string;
  followup: string;
};

/** The preview table reads the payload the API resolved, not the raw sheet — that is the promise being shown. */
export function previewRows(job: LeadImportJob | null): PreviewRow[] {
  return (job?.preview ?? []).map((row) => ({
    name: String(row.name ?? ""),
    mobile: String(row.mobile ?? ""),
    contact: [row.contact_name, row.contact_title].filter(Boolean).join(" · ") || "—",
    insurance: String(row.insurance_type ?? "—"),
    headcount: row.headcount == null ? "—" : String(row.headcount),
    followup: row.next_followup_at ? String(row.next_followup_at).slice(0, 10) : "—",
  }));
}
