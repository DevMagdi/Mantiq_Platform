/**
 * Screen 9 — lead detail: stepper, needs, timeline, conversion CTA.
 * The convert button is the only way to reach `won` (spec §5.3) and it is green `#05603A`
 * because it is a money event, not a navigation event.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, MessageSquare, PhoneCall, Search, StickyNote, Users, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { Modal } from "@/components/Modal";
import { Avatar, Badge, Button, Card, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { Stepper } from "@/components/Stepper";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { formatDateTime, formatMoney, formatNumber, relativeTime } from "@/lib/format";
import type { LeadActivity, LeadDetail as LeadDetailType } from "@/lib/api/views";

const STEPS = ["new", "contacted", "meeting_scheduled", "quote_sent", "won"] as const;

export default function LeadDetailPage() {
  const { leadId = "" } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isManager } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [convertOpen, setConvertOpen] = useState(false);
  const [lostOpen, setLostOpen] = useState(false);
  const [note, setNote] = useState("");
  const [meeting, setMeeting] = useState<{ when: string; where: string }>({ when: "", where: "" });

  const lead = useQuery({ queryKey: ["leads", leadId], queryFn: () => api.get<LeadDetailType>(`/leads/${leadId}`) });
  const activities = useQuery({
    queryKey: ["leads", leadId, "activities"],
    queryFn: () => api.get<LeadActivity[]>(`/leads/${leadId}/activities`),
  });
  const reps = useQuery({
    queryKey: ["sales", "team"],
    queryFn: () => api.get<{ rep_id: string; name: string }[]>("/sales/team"),
    enabled: isManager,
  });
  const [assignTo, setAssignTo] = useState<string>("");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["leads"] });
    void queryClient.invalidateQueries({ queryKey: ["clients"] });
    // an assignment moves a number on the team screen's workload cards, so those are re-read too
    void queryClient.invalidateQueries({ queryKey: ["sales"] });
    void queryClient.invalidateQueries({ queryKey: ["sales-team"] });
  };

  const setStatus = useMutation({
    mutationFn: (status: string) => api.post(`/leads/${leadId}/status`, { status }),
    onSuccess: () => {
      invalidate();
      toast(t("leads.moved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const convert = useMutation({
    mutationFn: () => api.post<{ client_id: string }>(`/leads/${leadId}/convert`),
    onSuccess: (result) => {
      invalidate();
      setConvertOpen(false);
      toast(t("leads.convertDone"));
      navigate(`/clients/${result.client_id}`);
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const addNote = useMutation({
    mutationFn: () => api.post(`/leads/${leadId}/activities`, { body: note.trim() }),
    onSuccess: () => {
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["leads", leadId, "activities"] });
      toast(t("leads.noteSaved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const addMeeting = useMutation({
    mutationFn: () =>
      api.post(`/leads/${leadId}/meetings`, {
        scheduled_at: new Date(meeting.when).toISOString(),
        location: meeting.where || null,
      }),
    onSuccess: () => {
      setMeeting({ when: "", where: "" });
      void queryClient.invalidateQueries({ queryKey: ["leads", leadId, "activities"] });
      toast(t("leads.meetingSaved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const assign = useMutation({
    mutationFn: () => api.post(`/leads/${leadId}/assign`, { assigned_to: assignTo || null }),
    onSuccess: () => {
      invalidate();
      toast(t("leads.assignSaved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  const lose = useMutation({
    mutationFn: (payload: { lost_reason_id: string; note: string | null }) => api.post(`/leads/${leadId}/lose`, payload),
    onSuccess: () => {
      setLostOpen(false);
      invalidate();
      toast(t("leads.lost"), "warn");
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  if (lead.isPending) return <StateBlock kind="loading" />;
  if (lead.isError) {
    return (
      <StateBlock
        kind={lead.error instanceof Error && "status" in lead.error && (lead.error as { status: number }).status === 404 ? "notfound" : "error"}
        action={<Button onClick={() => void lead.refetch()}>{t("common.retry")}</Button>}
      />
    );
  }
  const data = lead.data;
  const index = STEPS.indexOf(data.status as (typeof STEPS)[number]);
  const nextStatus = index >= 0 && index < STEPS.length - 1 ? STEPS[index + 1] : null;
  const prevStatus = index > 0 ? STEPS[index - 1] : null;
  const isWon = data.status === "won";

  return (
    <>
      <PageHeader
        title={t("leads.leadTitle")}
        subtitle={
          <button type="button" className="text-primary hover:underline" onClick={() => navigate("/leads")}>
            {t("leads.allLeads")}
          </button>
        }
        actions={
          <>
            {prevStatus ? <Button onClick={() => setStatus.mutate(prevStatus)}>{t("leads.followup")}</Button> : null}
            {nextStatus && !isWon ? (
              <Button
                onClick={() => {
                  if (nextStatus === "won") setConvertOpen(true);
                  else setStatus.mutate(nextStatus);
                }}
              >
                {t("leads.moved")}
              </Button>
            ) : null}
            {!isWon && data.status !== "lost" ? (
              <Button variant="danger" icon={<X size={15} strokeWidth={2} />} onClick={() => setLostOpen(true)}>
                {t("leads.lost")}
              </Button>
            ) : null}
            {isWon && data.converted_client_id ? (
              <Button variant="primary" icon={<Users size={16} strokeWidth={1.8} />} onClick={() => navigate(`/clients/${data.converted_client_id}`)}>
                {t("clients.openFile")}
              </Button>
            ) : null}
          </>
        }
      />

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-5">
          <Card>
            <div className="flex items-start gap-3.5">
              <Avatar name={data.name} size={48} />
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-[20px] font-bold text-ink">{data.name}</h2>
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                  <Badge tone={isWon ? "g" : data.status === "lost" ? "r" : "b"}>{t(`leadStatus.${data.status}`)}</Badge>
                  {data.source ? <span>{data.source}</span> : null}
                  {data.followup_overdue ? <Badge tone="a">{t("leads.followupLate")}</Badge> : null}
                </div>
              </div>
            </div>
            <div className="mt-5">
              <Stepper status={data.status} />
            </div>
            {!isWon ? (
              <button
                type="button"
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-btn px-[18px] py-[11px] text-[14px] font-semibold text-white transition duration-150 disabled:opacity-60"
                style={{ background: "#05603A" }}
                onClick={() => setConvertOpen(true)}
                disabled={convert.isPending}
              >
                {t("leads.convertCta")}
              </button>
            ) : (
              <p className="mt-4 text-[13px] font-semibold text-[var(--g-ink)]">{t("leads.movedToClients")}</p>
            )}
          </Card>

          <Card>
            <SectionTitle>{t("leads.needsTitle")}</SectionTitle>
            <p className="text-[13.5px] leading-[22px] text-ink">{data.needs_text ?? "—"}</p>
            <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-[13px] sm:grid-cols-3">
              <Field label={t("leads.needHeadcount")} value={formatNumber(data.headcount ?? 0)} />
              <Field label={t("leads.needInsuranceType")} value={data.insurance_type ?? "—"} />
              <Field label={t("leads.needCurrent")} value={data.insurer ?? "—"} />
              <Field label={t("leads.needBudget")} value={data.budget ? formatMoney(data.budget) : "—"} />
              <Field label={t("leads.needExpiry")} value={data.current_policy_expiry ? formatDateTime(data.current_policy_expiry) : "—"} />
              <Field label={t("leads.lastActivity")} value={relativeTime(data.last_activity_at)} />
            </dl>
          </Card>

          <Card>
            <SectionTitle action={<span className="text-[12px] text-muted">{t("leads.last30")}</span>}>{t("leads.timeline")}</SectionTitle>
            {activities.isPending ? (
              <StateBlock kind="loading" />
            ) : (activities.data ?? []).length === 0 ? (
              <p className="py-2 text-[13px] text-muted">{t("leads.timelineEmpty")}</p>
            ) : (
              <ol className="space-y-3">
                {(activities.data ?? []).map((row) => (
                  <li key={row.id} className="flex gap-3">
                    <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[var(--primary-soft)] text-[var(--primary-ink)]">
                      {row.type === "call" ? (
                        <PhoneCall size={14} strokeWidth={1.8} />
                      ) : row.type === "meeting" ? (
                        <CalendarPlus size={14} strokeWidth={1.8} />
                      ) : row.type === "whatsapp" ? (
                        <MessageSquare size={14} strokeWidth={1.8} />
                      ) : (
                        <StickyNote size={14} strokeWidth={1.8} />
                      )}
                    </span>
                    <div className="min-w-0 flex-1 border-b border-line pb-3">
                      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                        <span className="font-semibold text-ink">{t(`activity.${row.type}`, { defaultValue: row.type })}</span>
                        <span>{row.actor_name ?? "—"}</span>
                        <span>·</span>
                        <span>{relativeTime(row.occurred_at)}</span>
                      </div>
                      {row.body ? <p className="mt-1 text-[13px] leading-[21px] text-ink">{row.body}</p> : null}
                    </div>
                  </li>
                ))}
              </ol>
            )}

            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <input
                className="field flex-1"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder={t("leads.addNote")}
                aria-label={t("leads.addNote")}
              />
              <Button variant="primary" busy={addNote.isPending} disabled={note.trim().length === 0} onClick={() => addNote.mutate()}>
                {t("common.save")}
              </Button>
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <SectionTitle>{t("leads.colContact")}</SectionTitle>
            <p className="text-[13px] font-semibold text-ink">{data.contact_name ?? "—"}</p>
            <a href={`tel:${data.mobile ?? ""}`} className="mt-2 flex items-center justify-between rounded-btn border border-line px-3 py-2 text-[13px]" dir="ltr">
              <span className="tabular-nums text-ink">{data.mobile ?? "—"}</span>
              <PhoneCall size={15} strokeWidth={1.8} className="text-[var(--g-ink)]" />
            </a>
            {data.email ? <p className="mt-2 truncate text-[12.5px] text-muted">{data.email}</p> : null}
          </Card>

          <Card>
            <SectionTitle>{t("leads.scheduleMeeting")}</SectionTitle>
            <label className="label" htmlFor="meeting-when">
              {t("leads.meetingWhen")}
            </label>
            <input
              id="meeting-when"
              type="datetime-local"
              className="field"
              value={meeting.when}
              onChange={(event) => setMeeting((current) => ({ ...current, when: event.target.value }))}
            />
            <label className="label mt-3" htmlFor="meeting-where">
              {t("leads.meetingLocation")}
            </label>
            <input
              id="meeting-where"
              className="field"
              value={meeting.where}
              onChange={(event) => setMeeting((current) => ({ ...current, where: event.target.value }))}
            />
            <Button variant="primary" full busy={addMeeting.isPending} disabled={meeting.when.length === 0} onClick={() => addMeeting.mutate()}>
              <span>{t("leads.scheduleMeeting")}</span>
            </Button>
          </Card>

          {isManager ? (
            <Card>
              <SectionTitle>{t("leads.assignedTo")}</SectionTitle>
              <p className="mb-2 text-[12.5px] text-muted">{t("leads.assignHint")}</p>
              <select className="field" value={assignTo} onChange={(event) => setAssignTo(event.target.value)} aria-label={t("leads.assign")}>
                <option value="">{data.assignee_name ?? t("leads.unassigned")}</option>
                {(reps.data ?? []).map((rep) => (
                  <option key={rep.rep_id} value={rep.rep_id}>
                    {rep.name}
                  </option>
                ))}
              </select>
              <Button full busy={assign.isPending} disabled={assignTo.length === 0 || assignTo === data.assignee_id} onClick={() => assign.mutate()}>
                {t("leads.saveAssign")}
              </Button>
            </Card>
          ) : null}
        </div>
      </div>

      {convertOpen ? (
        <Modal
          title={t("leads.confirmWon")}
          onClose={() => setConvertOpen(false)}
          actions={
            <>
              <Button onClick={() => setConvertOpen(false)}>{t("common.cancel")}</Button>
              <Button variant="primary" busy={convert.isPending} onClick={() => convert.mutate()}>
                {t("leads.convert")}
              </Button>
            </>
          }
        >
          <p className="text-[13px] leading-[21px] text-muted">{t("leads.convertBody")}</p>
        </Modal>
      ) : null}

      {lostOpen ? <LostDialog onClose={() => setLostOpen(false)} busy={lose.isPending} onSubmit={(payload) => lose.mutate(payload)} /> : null}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-muted">{label}</dt>
      <dd className="mt-0.5 font-semibold text-ink">{value}</dd>
    </div>
  );
}

function LostDialog({
  onClose,
  onSubmit,
  busy,
}: {
  onClose: () => void;
  onSubmit: (payload: { lost_reason_id: string; note: string | null }) => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const reasons = useQuery({ queryKey: ["lost-reasons"], queryFn: () => api.get<{ id: string; name: string }[]>("/lost-reasons") });
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [touched, setTouched] = useState(false);

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
            onClick={() => {
              setTouched(true);
              if (reason) onSubmit({ lost_reason_id: reason, note: note.trim() || null });
            }}
          >
            {t("leads.lost")}
          </Button>
        </>
      }
    >
      <label className="label" htmlFor="detail-lost-reason">
        {t("leads.reason")}
      </label>
      <select id="detail-lost-reason" className="field" value={reason} onChange={(event) => setReason(event.target.value)}>
        <option value="">{reasons.isPending ? t("common.loading") : t("common.none")}</option>
        {(reasons.data ?? []).map((row) => (
          <option key={row.id} value={row.id}>
            {row.name}
          </option>
        ))}
      </select>
      {touched && !reason ? (
        <p className="mt-2 flex items-center gap-1 text-[12px] font-semibold text-[var(--r-ink)]">
          <Search size={13} strokeWidth={1.8} />
          {t("leads.reason")}
        </p>
      ) : null}
      <label className="label mt-4" htmlFor="detail-lost-note">
        {t("leads.lostNoteLabel")}
      </label>
      <textarea id="detail-lost-note" className="field min-h-[80px]" value={note} onChange={(event) => setNote(event.target.value)} />
    </Modal>
  );
}

