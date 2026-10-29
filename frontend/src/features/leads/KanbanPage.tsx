/**
 * Screen 3 — Kanban of the rep's own leads (5 columns, drag to move, conversion confirm).
 * Isolation is visible in the UI: a rep only ever receives their own cards because
 * `/leads/board` and `/leads/stats` are built from the same `Scope` as the list (spec §6).
 */
import { useMemo, useState } from "react";
import { DndContext, PointerSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ChevronDown, ChevronLeft, FileSpreadsheet, GripVertical, PhoneCall, Users, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Avatar, Badge, Button, Card, Chip, PageHeader, SearchInput, StateBlock } from "@/components/ui";
import type { RepWorkload } from "@/lib/api/views";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { asText, includesText, lower } from "@/lib/text";
import type { Lead, LeadBoardColumn } from "@/lib/api/views";
import { useAuth } from "@/lib/auth";
import { followupLabel, formatNumber } from "@/lib/format";

import { ImportLeadsDialog } from "./ImportLeadsDialog";

type Board = { columns: LeadBoardColumn[] };

/** Every stage the board can be narrowed to — the empty value is "no filter", not a stage. */
const STAGE_FILTERS = [
  { value: "", label: "common.all" },
  { value: "new", label: "leads.colNew" },
  { value: "contacted", label: "leads.colContacted" },
  { value: "meeting_scheduled", label: "leads.colMeeting" },
  { value: "quote_sent", label: "leads.colQuote" },
  { value: "won", label: "leads.colWon" },
] as const;

const COLUMNS = [
  { key: "new", label: "leads.colNew" },
  { key: "contacted", label: "leads.colContacted" },
  { key: "meeting_scheduled", label: "leads.colMeeting" },
  { key: "quote_sent", label: "leads.colQuote" },
  { key: "won", label: "leads.colWon" },
] as const;

export default function KanbanPage() {
  const { t } = useTranslation();
  const { user, isManager } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState<Lead | null>(null);
  const [losing, setLosing] = useState<Lead | null>(null);
  const [importing, setImporting] = useState(false);
  // Manager filters. `rep` and `pool` are server-side: a browser that filtered one page of 500 cards would
  // report "محمد عنده 3 ليدز" while the API held 40 — the number and the cards must come from one query.
  const [repId, setRepId] = useState<string>("");
  const [stage, setStage] = useState<string>("");
  const [pool, setPool] = useState(false);

  const team = useQuery({
    queryKey: ["sales", "team"],
    queryFn: () => api.get<RepWorkload[]>("/sales/team"),
    enabled: isManager,
  });

  const board = useQuery({
    queryKey: ["leads", "board", { repId, pool }],
    // a rep never sends `assigned_to`: the scope already answers "mine", and a stray id would only narrow
    queryFn: () =>
      api.get<Board>("/leads/board", {
        ...(isManager && repId ? { assigned_to: repId } : {}),
        ...(isManager && pool ? { unassigned: true } : {}),
      }),
  });
  const stats = useQuery({ queryKey: ["leads", "stats"], queryFn: () => api.get<Record<string, number | null>>("/leads/stats") });

  const setStatus = useMutation({
    mutationFn: (input: { id: string; status: string }) => api.post(`/leads/${input.id}/status`, { status: input.status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast(t("leads.moved"));
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast(t("common.errorBody"), "error");
    },
  });

  const convert = useMutation({
    mutationFn: (id: string) => api.post<{ client_id: string }>(`/leads/${id}/convert`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
      await queryClient.invalidateQueries({ queryKey: ["clients"] });
      toast(t("leads.convertDone"));
      setConfirming(null);
    },
    onError: () => toast(t("common.errorBody"), "error"),
  });

  const lose = useMutation({
    mutationFn: (input: { id: string; lost_reason_id: string; note: string | null }) => api.post(`/leads/${input.id}/lose`, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
      setLosing(null);
      toast(t("leads.lost"), "warn");
    },
    onError: () => toast(t("common.errorBody"), "error"),
  });

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const columns = useMemo(() => {
    const source = board.data?.columns ?? [];
    const needle = lower(asText(query)).trim();
    // the stage dropdown hides whole columns rather than emptying them: a manager asking "أين وصلنا في
    // مرحلة العرض?" wants one lane, not five lanes where four say «لا يوجد»
    return COLUMNS.filter((column) => !isManager || !stage || column.key === stage).map((column) => {
      const found = source.find((entry) => entry.status === column.key);
      const items = (found?.items ?? []).filter((lead) => !needle || includesText(`${asText(lead.name)} ${asText(lead.mobile)}`, needle));
      return { ...column, items, count: items.length };
    });
  }, [board.data, query, stage, isManager]);

  function onDragEnd(event: DragEndEvent) {
    const leadId = String(event.active.id);
    const target = event.over?.id ? String(event.over.id) : null;
    const current = event.active.data.current?.status;
    if (!target || target === current) return;
    const lead = (board.data?.columns ?? []).flatMap((column) => column.items ?? []).find((row) => row.id === leadId);
    if (!lead) return;
    if (target === "won") {
      // conversion is a transaction (client + contract + audit) — never a silent drop
      setConfirming(lead);
      return;
    }
    void setStatus.mutateAsync({ id: leadId, status: target });
  }

  const kpis = [
    { label: t("leads.kpiMine"), value: stats.data?.my_leads, hint: t("leads.newWeek", { n: formatNumber(stats.data?.new_week ?? 0) }) },
    { label: t("leads.kpiMeetings"), value: stats.data?.meetings_week, hint: t("leads.todayCount", { n: formatNumber(stats.data?.meetings_today ?? 0) }) },
    { label: t("leads.kpiOverdue"), value: stats.data?.followups_overdue, hint: t("leads.needsAction") },
    {
      label: t("leads.kpiConverted"),
      value: stats.data?.converted_month,
      hint: t("leads.conversionRate", { n: formatNumber(stats.data?.conversion_rate_pct ?? 0) }),
    },
  ];

  return (
    <>
      <PageHeader
        title={t("leads.titleKanban", { name: user?.full_name?.split(" ")[0] ?? "" })}
        subtitle={
          <span>
            {t("leads.repView")} · {t("leads.activeCount", { n: formatNumber(stats.data?.my_leads ?? 0) })}
          </span>
        }
        actions={
          <>
            <SearchInput value={query} onChange={setQuery} width={260} placeholder={t("leads.search")} />
            {isManager ? (
              <>
                <Button icon={<Users size={16} strokeWidth={1.8} />} onClick={() => navigate("/sales-team")}>
                  {t("leads.managerView")}
                </Button>
                {/* a sheet of leads is a manager's action; the API refuses a rep, so the button is not offered */}
                <Button icon={<FileSpreadsheet size={16} strokeWidth={1.8} />} onClick={() => setImporting(true)}>
                  {t("leads.importLeads")}
                </Button>
              </>
            ) : null}
            <Button variant="primary" onClick={() => navigate("/leads/new")}>
              {t("leads.newLead")}
            </Button>
          </>
        }
      />

      {!isManager ? (
        <div
          className="mb-4 flex items-center gap-2 rounded-btn px-3.5 py-2.5 text-[12.5px] font-semibold"
          style={{ background: "var(--primary-soft)", color: "var(--primary-ink)" }}
        >
          <AlertCircle size={15} strokeWidth={1.8} />
          {t("leads.isolationBanner")}
        </div>
      ) : null}

      {isManager ? (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold text-muted">{t("leads.filterRep")}</span>
            <span className="relative">
              <select
                className="field w-[190px] py-[7px] pe-8 text-[13px]"
                value={repId}
                onChange={(event) => setRepId(event.target.value)}
                aria-label={t("leads.filterRep")}
              >
                <option value="">{t("leads.filterAllReps")}</option>
                {(team.data ?? []).map((rep) => (
                  <option key={rep.rep_id} value={rep.rep_id}>
                    {rep.name} · {formatNumber(rep.open_leads)}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted" style={{ insetInlineEnd: 10 }} />
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-semibold text-muted">{t("leads.filterStage")}</span>
            <span className="relative">
              <select
                className="field w-[170px] py-[7px] pe-8 text-[13px]"
                value={stage}
                onChange={(event) => setStage(event.target.value)}
                aria-label={t("leads.filterStage")}
              >
                {STAGE_FILTERS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {t(entry.label)}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted" style={{ insetInlineEnd: 10 }} />
            </span>
          </label>

          <label className="mb-2 flex items-center gap-2 text-[12.5px] font-semibold text-ink">
            <input
              type="checkbox"
              checked={pool}
              onChange={(event) => setPool(event.target.checked)}
              aria-label={t("leads.filterUnassigned")}
            />
            {t("leads.filterUnassigned")}
          </label>
        </div>
      ) : null}

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi) => (
          <Card key={kpi.label} className="!py-4">
            <div className="text-[12.5px] text-muted">{kpi.label}</div>
            <div className="mt-1 flex items-baseline gap-2">
              {kpi.value === undefined ? (
                <div className="skeleton h-7 w-10" />
              ) : (
                <span className="text-[26px] font-bold tabular-nums text-ink">{formatNumber(kpi.value)}</span>
              )}
              <span className="text-[12px] font-semibold text-muted">{kpi.hint}</span>
            </div>
          </Card>
        ))}
      </div>

      {board.isPending ? (
        <StateBlock kind="loading" />
      ) : board.isError ? (
        <StateBlock kind="error" action={<Button onClick={() => void board.refetch()}>{t("common.retry")}</Button>} />
      ) : (
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <div className={`grid grid-cols-1 gap-3 md:grid-cols-2 ${isManager && stage ? "" : "xl:grid-cols-5"}`}>
            {columns.map((column) => (
              <Column
                key={column.key}
                id={column.key}
                title={column.label}
                count={column.count}
                locked={column.key === "won"}
                onLost={(lead) => setLosing(lead)}
                onOpen={(lead) => navigate(`/leads/${lead.id}`)}
                leads={column.items}
              />
            ))}
          </div>
        </DndContext>
      )}

      {confirming ? (
        <Modal
          title={t("leads.confirmWon")}
          onClose={() => setConfirming(null)}
          actions={
            <>
              <Button onClick={() => setConfirming(null)}>{t("common.cancel")}</Button>
              <Button variant="primary" busy={convert.isPending} onClick={() => convert.mutate(confirming.id)}>
                {t("leads.convert")}
              </Button>
            </>
          }
        >
          <p className="text-[13px] leading-[21px] text-muted">{t("leads.convertBody")}</p>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
            <dt className="text-muted">{t("leads.colLead")}</dt>
            <dd className="font-semibold text-ink">{confirming.name}</dd>
            <dt className="text-muted">{t("newLead.headcount")}</dt>
            <dd className="text-ink tabular-nums">{formatNumber(confirming.headcount ?? 0)}</dd>
            <dt className="text-muted">{t("leads.colContact")}</dt>
            <dd className="text-ink">{confirming.contact_name ?? "—"}</dd>
          </dl>
        </Modal>
      ) : null}

      {importing ? <ImportLeadsDialog onClose={() => setImporting(false)} /> : null}

      {losing ? (
        <LostDialog
          lead={losing}
          onClose={() => setLosing(null)}
          busy={lose.isPending}
          onSubmit={(payload) => lose.mutate(payload)}
          fail={(message) => toast(message, "error")}
        />
      ) : null}
    </>
  );
}

function Column({
  id,
  title,
  count,
  leads,
  locked,
  onOpen,
  onLost,
}: {
  id: string;
  title: string;
  count: number;
  leads: Lead[];
  locked: boolean;
  onOpen: (lead: Lead) => void;
  onLost: (lead: Lead) => void;
}) {
  const { t } = useTranslation();
  const drop = useDroppable({ id });
  return (
    <div
      ref={drop.setNodeRef}
      className={`rounded-card p-2.5 transition-colors duration-150 ${drop.isOver ? "ring-2 ring-[var(--primary)]" : ""}`}
      style={{ background: "var(--kanban-col)" }}
      data-column={id}
    >
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-[13px] font-bold text-ink">{t(title)}</h3>
        <span className="rounded-pill bg-white px-2 py-0.5 text-[11.5px] font-bold tabular-nums text-muted">{count}</span>
      </div>
      <ul className="space-y-2">
        {leads.length === 0 ? <li className="px-1 py-3 text-[12px] text-muted">{t("leads.empty")}</li> : null}
        {leads.map((lead) => (
          <li key={lead.id}>
            <LeadCard lead={lead} draggable={!locked} onOpen={onOpen} onLost={onLost} />
          </li>
        ))}
      </ul>
      {locked ? <p className="px-1 pb-1 pt-2 text-[11.5px] text-muted">{t("leads.converted")}</p> : null}
    </div>
  );
}

function LeadCard({
  lead,
  draggable,
  onOpen,
  onLost,
}: {
  lead: Lead;
  draggable: boolean;
  onOpen: (lead: Lead) => void;
  onLost: (lead: Lead) => void;
}) {
  const { t } = useTranslation();
  const drag = useDraggable({ id: lead.id, data: { status: lead.status }, disabled: !draggable });
  const follow = followupLabel(lead.next_followup_at, {
    today: t("leads.followupToday"),
    tomorrow: t("leads.followupTomorrow"),
    late: t("leads.followupLate"),
    next: t("leads.followupNext"),
  });

  return (
    <article
      ref={drag.setNodeRef}
      {...(draggable ? drag.attributes : {})}
      {...(draggable ? drag.listeners : {})}
      style={{
        transform: drag.transform ? `translate(${drag.transform.x}px, ${drag.transform.y}px)` : undefined,
        opacity: drag.isDragging ? 0.7 : 1,
      }}
      data-lead-card={lead.id}
      data-lead-status={lead.status}
      className="rounded-card border border-line bg-card px-3.5 py-3 shadow-card"
    >
      <div className="flex items-start gap-2">
        <Avatar name={lead.name} size={26} />
        <button type="button" className="min-w-0 flex-1 text-start" onClick={() => onOpen(lead)}>
          <h4 className="truncate text-[13.5px] font-bold text-ink">{lead.name}</h4>
          <p className="mt-0.5 truncate text-[12px] text-muted">
            {lead.contact_name ?? "—"} · {formatNumber(lead.headcount ?? 0)} {t("leads.insured_count")}
          </p>
        </button>
        {draggable ? <GripVertical size={15} strokeWidth={1.8} className="mt-0.5 shrink-0 text-line-2" /> : null}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <Chip tone={follow.tone}>{follow.text}</Chip>
        {lead.followup_overdue ? <Badge tone="r">{t("leads.kpiOverdue")}</Badge> : null}
        {lead.insurance_type ? <span className="text-[11.5px] text-muted">{lead.insurance_type}</span> : null}
      </div>

      <p className="mt-2 flex items-center gap-1 text-[11.5px] text-muted" dir="ltr">
        <PhoneCall size={12} strokeWidth={1.8} />
        <span className="tabular-nums">{lead.mobile ?? "—"}</span>
      </p>

      <footer className="mt-2 flex items-center justify-between border-t border-line pt-2">
        <span className="truncate text-[11.5px] text-muted">{lead.assignee_name ?? t("leads.unassigned")}</span>
        <span className="flex items-center gap-1">
          {draggable ? (
            <button
              type="button"
              className="grid h-6 w-6 place-items-center rounded-full text-muted transition-colors duration-150 hover:bg-[var(--r-bg)] hover:text-[var(--r-ink)]"
              onClick={() => onLost(lead)}
              aria-label={t("leads.lost")}
              title={t("leads.lost")}
            >
              <X size={13} strokeWidth={2} />
            </button>
          ) : null}
          <button type="button" className="flex items-center gap-0.5 text-[12px] font-semibold text-primary" onClick={() => onOpen(lead)}>
            {t("common.open")}
            <ChevronLeft size={14} strokeWidth={2} />
          </button>
        </span>
      </footer>
    </article>
  );
}

function LostDialog({
  lead,
  onClose,
  onSubmit,
  busy,
  fail,
}: {
  lead: Lead;
  onClose: () => void;
  onSubmit: (payload: { id: string; lost_reason_id: string; note: string | null }) => void;
  busy: boolean;
  fail: (message: string) => void;
}) {
  const { t } = useTranslation();
  const reasons = useQuery({ queryKey: ["lost-reasons"], queryFn: () => api.get<{ id: string; name: string }[]>("/lost-reasons") });
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");

  return (
    <Modal
      title={t("leads.confirmLost")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            variant="danger"
            busy={busy}
            disabled={reason.length === 0}
            onClick={() => (reason ? onSubmit({ id: lead.id, lost_reason_id: reason, note: note.trim() || null }) : fail(t("leads.reason")))}
          >
            {t("leads.lost")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="lost-reason">
        {t("leads.reason")}
      </label>
      {reasons.isError ? (
        <p className="text-[12px] text-[var(--r-ink)]">{t("common.errorBody")}</p>
      ) : (
        <select id="lost-reason" className="field" value={reason} onChange={(event) => setReason(event.target.value)}>
          <option value="">{reasons.isPending ? t("common.loading") : t("common.none")}</option>
          {(reasons.data ?? []).map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
      )}
      <label className="label mt-4" htmlFor="lost-note">
        {t("leads.lostNoteLabel")}
      </label>
      <textarea id="lost-note" className="field min-h-[80px]" value={note} onChange={(event) => setNote(event.target.value)} />
    </Modal>
  );
}
