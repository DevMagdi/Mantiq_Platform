/**
 * Screen 6 — manager view of the sales team: one card per rep with a 10px stage bar, a high-load warning
 * above 20 open leads, the unassigned pool that can be redistributed, and the office roster underneath.
 *
 * Two rules this screen exists to keep:
 * - the pool is drained by the **server** (`unassigned_only`), because a client loop over one page could
 *   only ever move `page_size` rows while telling the manager it moved the lot;
 * - a refused move shows the API's own message. «لم نتمكن من تحميل هذه البيانات» tells nobody why 37 leads
 *   stayed where they were.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, FileSpreadsheet, TriangleAlert, UserPlus } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Modal } from "@/components/Modal";
import { Avatar, Badge, Button, Card, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { formatNumber, formatPercent } from "@/lib/format";
import type { BulkAssignResult, Lead, RepWorkload } from "@/lib/api/views";

import { ImportLeadsDialog } from "./ImportLeadsDialog";
import { TeamMembersCard } from "./TeamMembersCard";

const STAGE_ORDER = ["new", "contacted", "meeting_scheduled", "quote_sent", "negotiation"] as const;
const STAGE_COLOR: Record<string, string> = {
  new: "var(--b-ink)",
  contacted: "var(--primary)",
  meeting_scheduled: "var(--a-ink)",
  quote_sent: "var(--t-ink)",
  negotiation: "var(--g-ink)",
};

const HIGH_LOAD = 20;

export default function SalesTeamPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [redistributing, setRedistributing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);

  const team = useQuery({ queryKey: ["sales", "team"], queryFn: () => api.get<RepWorkload[]>("/sales/team") });
  const unassigned = useQuery({
    queryKey: ["leads", "unassigned"],
    queryFn: () => api.get<{ items: Lead[]; total: number }>("/leads", { unassigned: true, page_size: 100 }),
  });

  const pool = unassigned.data?.items ?? [];
  const poolTotal = unassigned.data?.total ?? 0;

  async function refresh() {
    // every list that shows a lead, a workload number or a seat reads one of these three keys
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["leads"] }),
      queryClient.invalidateQueries({ queryKey: ["sales"] }),
      queryClient.invalidateQueries({ queryKey: ["sales-team"] }),
    ]);
  }

  const redistribute = useMutation({
    mutationFn: (input: { to: string[]; onlySelected: boolean }) =>
      api.post<BulkAssignResult>("/leads/assign-bulk", input.onlySelected ? { lead_ids: picked, to: input.to } : { unassigned_only: true, to: input.to }),
    onSuccess: async (result) => {
      await refresh();
      setRedistributing(false);
      setPicked([]);
      const moved = result.assigned ?? 0;
      // "nothing moved" is a different fact from "the request failed", and both are said differently
      if (moved === 0) toast(t("leads.redistributeNothing"), "warn");
      else toast(t("leads.redistributeDone", { n: formatNumber(moved), skipped: formatNumber(result.skipped?.length ?? 0) }));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  if (team.isPending) return <StateBlock kind="loading" />;
  if (team.isError) {
    return <StateBlock kind="error" action={<Button onClick={() => void team.refetch()}>{t("common.retry")}</Button>} />;
  }
  const rows = team.data ?? [];
  const totals = rows.reduce(
    (acc, row) => ({ open: acc.open + row.open_leads, won: acc.won + row.won, meetings: acc.meetings + row.meetings }),
    { open: 0, won: 0, meetings: 0 },
  );

  return (
    <>
      <PageHeader
        title={t("leads.team")}
        subtitle={t("leads.teamSubtitle")}
        actions={
          <>
            <Button icon={<FileSpreadsheet size={16} strokeWidth={1.8} />} onClick={() => setImporting(true)}>
              {t("leads.importLeads")}
            </Button>
            <Button variant="primary" icon={<UserPlus size={16} strokeWidth={1.8} />} onClick={() => navigate("/leads/new")}>
              {t("leads.newLead")}
            </Button>
          </>
        }
      />

      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="!py-4">
          <div className="text-[12.5px] text-muted">{t("leads.allLeads")}</div>
          <div className="mt-1 text-[26px] font-bold tabular-nums text-ink">{formatNumber(totals.open)}</div>
        </Card>
        <Card className="!py-4">
          <div className="text-[12.5px] text-muted">{t("leads.kpiMeetings")}</div>
          <div className="mt-1 text-[26px] font-bold tabular-nums text-ink">{formatNumber(totals.meetings)}</div>
        </Card>
        <Card className="!py-4">
          <div className="text-[12.5px] text-muted">{t("leads.kpiConverted")}</div>
          <div className="mt-1 text-[26px] font-bold tabular-nums text-ink">{formatNumber(totals.won)}</div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-4">
        {rows.map((rep) => {
          const high = rep.open_leads > HIGH_LOAD;
          return (
            <Card key={rep.rep_id} style={high ? { background: "var(--row-warn)" } : undefined}>
              <div className="flex items-start gap-3">
                <Avatar name={rep.name} size={38} />
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-[14px] font-bold text-ink">{rep.name}</h3>
                  <p className="mt-0.5 text-[12px] text-muted">
                    {formatNumber(rep.open_leads)} {t("leads.allLeads")} · {formatPercent(rep.conversion_rate ?? null)} {t("leads.conversion")}
                  </p>
                </div>
                {high ? <Badge tone="a">{t("leads.loadHigh")}</Badge> : <Badge tone="g">{t("leads.loadBalanced")}</Badge>}
              </div>

              <div className="mt-4 flex h-[10px] w-full overflow-hidden rounded-pill bg-white">
                {STAGE_ORDER.map((stage) => {
                  const pct = rep.stages_pct?.[stage] ?? 0;
                  return (
                    <span
                      key={stage}
                      title={`${t(`leadStatus.${stage}`)} · ${rep.stages?.[stage] ?? 0}`}
                      style={{ width: `${pct}%`, background: STAGE_COLOR[stage] }}
                    />
                  );
                })}
              </div>

              <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
                <Stat label={t("leads.colMeeting")} value={rep.meetings} icon={<CalendarCheck size={13} strokeWidth={1.8} />} />
                <Stat label={t("leads.colWon")} value={rep.won} />
                <Stat label={t("leads.insured_count")} value={rep.open_leads} />
              </dl>

              {high ? (
                <p className="mt-3 flex items-center gap-1.5 text-[12px] font-semibold text-[var(--a-ink)]">
                  <TriangleAlert size={13} strokeWidth={1.8} />
                  {t("leads.overloaded")}
                </p>
              ) : null}

              <button type="button" className="mt-3 text-[12.5px] font-semibold text-primary hover:underline" onClick={() => navigate(`/leads?rep=${rep.rep_id}`)}>
                {t("common.view")}
              </button>
            </Card>
          );
        })}
      </div>

      <Card className="mt-5" style={{ background: "var(--row-warn)" }} padded={false}>
        <div className="px-[20px] py-[18px]">
          <SectionTitle
            action={
              <Button variant="primary" disabled={poolTotal === 0} onClick={() => setRedistributing(true)}>
                {t("leads.redistribute")}
              </Button>
            }
          >
            {t("leads.unassignedTitle")}
          </SectionTitle>
          {unassigned.isPending ? (
            <StateBlock kind="loading" />
          ) : pool.length === 0 ? (
            <p className="text-[13px] text-muted">{t("leads.empty")}</p>
          ) : (
            <>
              {/* the list is a page; the "all unassigned" mode is not, and saying so stops the cap reading as a lie */}
              {poolTotal > pool.length ? (
                <p className="mb-2 text-[11.5px] text-muted">{t("leads.poolPaged", { shown: formatNumber(pool.length), total: formatNumber(poolTotal) })}</p>
              ) : null}
              <ul className="space-y-2" data-testid="unassigned-pool">
                {pool.map((lead) => (
                  <li key={lead.id} className="flex items-center justify-between gap-3 border-t border-line pt-2 text-[13px]">
                    <label className="flex min-w-0 flex-1 items-center gap-2">
                      <input
                        type="checkbox"
                        aria-label={t("leads.pickRow", { name: lead.name })}
                        checked={picked.includes(lead.id)}
                        onChange={(event) => setPicked(event.target.checked ? [...picked, lead.id] : picked.filter((id) => id !== lead.id))}
                      />
                      <button type="button" className="truncate font-semibold text-ink hover:text-primary" onClick={() => navigate(`/leads/${lead.id}`)}>
                        {lead.name}
                      </button>
                    </label>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="text-muted">{t(`leadStatus.${lead.status}`)}</span>
                      <button
                        type="button"
                        className="font-semibold text-primary hover:underline"
                        onClick={() => {
                          setPicked([lead.id]);
                          setRedistributing(true);
                        }}
                      >
                        {t("leads.reassignRow")}
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </Card>

      <div className="mt-5">
        <TeamMembersCard />
      </div>

      {redistributing ? (
        <RedistributeDialog
          // only a live seat can take work: the server refuses an inactive target with 422, and the dialog's
          // own empty state already says «لا يوجد بيّاع نشط» — so the list it offers is the active one
          reps={rows.filter((row) => row.is_active)}
          poolTotal={poolTotal}
          selectedCount={picked.length}
          busy={redistribute.isPending}
          onClose={() => setRedistributing(false)}
          onSubmit={(input) => redistribute.mutate(input)}
        />
      ) : null}

      {importing ? <ImportLeadsDialog onClose={() => setImporting(false)} /> : null}
    </>
  );
}

function Stat({ label, value, icon }: { label: string; value: number | null | undefined; icon?: ReactNode }) {
  return (
    <div className="rounded-btn bg-white py-2">
      <dd className="text-[17px] font-bold tabular-nums text-ink">{formatNumber(value ?? 0)}</dd>
      <dt className="mt-0.5 flex items-center justify-center gap-1 text-[11px] text-muted">
        {icon}
        {label}
      </dt>
    </div>
  );
}

type Mode = "all" | "selected";

function RedistributeDialog({
  reps,
  poolTotal,
  selectedCount,
  onSubmit,
  onClose,
  busy,
}: {
  reps: RepWorkload[];
  poolTotal: number;
  selectedCount: number;
  onSubmit: (input: { to: string[]; onlySelected: boolean }) => void;
  onClose: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const [to, setTo] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>(selectedCount > 0 ? "selected" : "all");
  const targets = mode === "all" ? poolTotal : selectedCount;
  const ready = to.length > 0 && targets > 0;

  return (
    <Modal
      title={t("leads.redistribute")}
      width={520}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => onSubmit({ to, onlySelected: mode === "selected" })}>
            {t("leads.redistributeGo", { n: formatNumber(targets) })}
          </Button>
        </>
      }
    >
      <div className="flex gap-2">
        <ModeButton active={mode === "all"} disabled={poolTotal === 0} onClick={() => setMode("all")} label={t("leads.redistributeAll", { n: formatNumber(poolTotal) })} />
        <ModeButton
          active={mode === "selected"}
          disabled={selectedCount === 0}
          onClick={() => setMode("selected")}
          label={t("leads.redistributeSelection", { n: formatNumber(selectedCount) })}
        />
      </div>

      <p className="mt-3 text-[12.5px] leading-[20px] text-muted">{t("leads.redistributeBody")}</p>

      <fieldset className="mt-3 border-0 p-0" data-testid="redistribute-reps">
        <legend className="label">{t("leads.redistributePickReps")}</legend>
        <ul className="space-y-1.5">
          {reps.map((rep) => (
            <li key={rep.rep_id}>
              <label className="flex cursor-pointer items-center gap-2.5 rounded-btn border border-line px-3 py-2 text-[13px] hover:bg-[#FAFBFF]">
                <input
                  type="checkbox"
                  checked={to.includes(rep.rep_id)}
                  onChange={(event) => setTo(event.target.checked ? [...to, rep.rep_id] : to.filter((id) => id !== rep.rep_id))}
                />
                <Avatar name={rep.name} size={26} />
                <span className="min-w-0 flex-1 truncate font-semibold text-ink">{rep.name}</span>
                <span className="shrink-0 text-[12px] text-muted" dir="ltr">
                  {formatNumber(rep.open_leads)}
                </span>
              </label>
            </li>
          ))}
          {reps.length === 0 ? <li className="text-[12.5px] text-muted">{t("leads.noReps")}</li> : null}
        </ul>
      </fieldset>

      {to.length > 1 ? <p className="mt-3 text-[11.5px] leading-[18px] text-muted">{t("leads.redistributeSplit", { n: formatNumber(to.length) })}</p> : null}
    </Modal>
  );
}

function ModeButton({ active, disabled, label, onClick }: { active: boolean; disabled: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex-1 rounded-btn border px-3 py-2 text-[12.5px] font-semibold transition-colors duration-150 disabled:opacity-50"
      style={{ borderColor: active ? "var(--primary)" : "var(--line-2)", background: active ? "var(--primary-soft)" : "var(--card)", color: active ? "var(--primary-ink)" : "var(--ink)" }}
    >
      {label}
    </button>
  );
}
