/**
 * Screen 2: the three-pane inbox (330px queue · thread · 270px details).
 * Bubbles carry `--primary-soft` for outbound, the voice note is a 20-bar player,
 * and playing a recording is a permissioned, audited action (`calls.listen`).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Paperclip,
  Check,
  ChevronDown,
  Clock3,
  Download,
  Headphones,
  MessageSquare,
  Pause,
  PhoneCall,
  Play,
  Search,
  Send,
  Sparkles,
  StickyNote,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Avatar, Badge, Card, Segmented, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { formatDuration, formatNumber, humanDuration, initials, relativeTime } from "@/lib/format";
import type { AiStatus, AiSummary, RequestCall, RequestDetail, RequestMessage, RequestRow as ViewsRequestRow } from "@/lib/api/views";

/* Every row type is the generated OpenAPI model — no handwritten DTOs (spec §3). */
type RequestRow = ViewsRequestRow;
type MessageRow = RequestMessage;
type CallRow = RequestCall;
type Detail = RequestDetail;

type QueueFilter = "open" | "overdue" | "all";

const CHANNEL_ICON: Record<string, ReactNode> = {
  whatsapp: <MessageSquare size={14} strokeWidth={1.8} />,
  call: <PhoneCall size={14} strokeWidth={1.8} />,
  email: <Send size={14} strokeWidth={1.8} />,
  manual: <Paperclip size={14} strokeWidth={1.8} />,
};

export default function InboxPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { requestId } = useParams<{ requestId?: string }>();
  const { can } = useAuth();
  // The queue's filter lives in the URL (`?filter=overdue`), not only in component state: the dashboard's
  // «طلبات متأخرة» card links here, and a link someone pastes into a chat has to open the same view for the
  // next person. Reading it here also means Back/Forward moves the queue, which is what a browser is for.
  const [searchParams, setSearchParams] = useSearchParams();
  const filterParam = searchParams.get("filter");
  const filter: QueueFilter = filterParam === "overdue" || filterParam === "all" ? filterParam : "open";
  /**
   * Opening a request keeps the queue's own query string. The auto-select below used to navigate to
   * `/inbox/<id>` alone, which dropped `?filter=overdue` — the deep link then flipped itself back to the open
   * queue a tick after it opened, and the "shareable filtered view" was shareable for one frame.
   */
  const search = searchParams.toString();
  const openRequest = useCallback(
    (id: string) => navigate({ pathname: `/inbox/${id}`, search }, { replace: !requestId }),
    [navigate, search, requestId],
  );
  const setFilter = (next: QueueFilter) => {
    const params = new URLSearchParams(searchParams);
    if (next === "open") params.delete("filter");
    else params.set("filter", next);
    setSearchParams(params, { replace: true });
  };
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [draft, setDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: ["requests", "queue", filter, query],
    queryFn: () =>
      api.get<{ items: RequestRow[]; total: number }>("/requests", {
        page_size: 40,
        sort: "last_activity_at",
        order: "desc",
        only_open: filter === "open" ? true : undefined,
        only_overdue: filter === "overdue" ? true : undefined,
        q: query || undefined,
      }),
  });

  const rows = list.data?.items ?? [];
  const selected = requestId ?? rows[0]?.id ?? null;

  useEffect(() => {
    if (!requestId && selected) openRequest(selected);
  }, [requestId, selected, openRequest]);

  const detail = useQuery({
    queryKey: ["requests", "detail", selected],
    queryFn: () => api.get<Detail>(`/requests/${selected}`),
    enabled: Boolean(selected),
  });

  const threadLength = detail.data?.messages?.length ?? 0;

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: "end" });
  }, [threadLength, selected]);

  const send = useMutation({
    mutationFn: () => api.post(`/requests/${selected}/messages`, { body: draft.trim() }),
    onSuccess: () => {
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
  });

  const addNote = useMutation({
    mutationFn: () => api.post(`/requests/${selected}/notes`, { body: noteDraft.trim() }),
    onSuccess: () => {
      setNoteDraft("");
      void queryClient.invalidateQueries({ queryKey: ["requests", "detail", selected] });
    },
  });

  /* The AI provider is deployment-switchable (ADR-012): the button exists only when it is on. */
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get<AiStatus>("/ai/status"), staleTime: 60_000 });
  const summarize = useMutation({
    mutationFn: () => api.post<AiSummary>(`/ai/requests/${selected}/summarize`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["requests", "detail", selected] }),
  });

  const setStatus = useMutation({
    mutationFn: (status: string) => api.post(`/requests/${selected}/status`, { status }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["requests"] }),
  });

  const statuses = useMemo(
    () => [
      { value: "in_progress", label: t("status.in_progress") },
      { value: "waiting_insurer", label: t("status.waiting_insurer") },
      { value: "replied", label: t("status.replied") },
      { value: "resolved", label: t("status.resolved") },
    ],
    [t],
  );

  return (
    <div className="flex h-[calc(100vh-54px-48px)] min-h-[520px] gap-5">
      {/* queue */}
      <Card padded={false} className="flex w-[330px] shrink-0 flex-col overflow-hidden">
        <div className="border-b border-line px-4 py-[14px]">
          <div className="flex items-center justify-between">
            <h2 className="text-[15px] font-bold text-ink">{t("inbox.title")}</h2>
            <span
              className="rounded-pill bg-primary-soft px-2 py-[2px] text-[11px] font-bold text-primary-ink tabular-nums"
              data-testid="queue-count"
              aria-label={t("inbox.countAria", { n: formatNumber(list.data?.total ?? 0) })}
            >
              {formatNumber(list.data?.total ?? 0)}
            </span>
          </div>
          <div className="relative mt-3">
            <Search size={15} strokeWidth={1.8} className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted" style={{ insetInlineStart: 9 }} />
            <input
              className="field py-[7px] ps-[29px] text-[13px]"
              placeholder={t("common.search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label={t("common.search")}
            />
          </div>
          <div className="mt-2.5">
            <Segmented
              ariaLabel={t("inbox.title")}
              value={filter}
              onChange={setFilter}
              options={[
                { value: "open", label: t("status.new") },
                { value: "overdue", label: t("status.overdue") },
                { value: "all", label: t("common.all") },
              ]}
            />
          </div>
        </div>

        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {list.isPending ? (
            <div className="p-4">
              <StateBlock kind="loading" />
            </div>
          ) : list.isError ? (
            <StateBlock kind="error" action={<button type="button" className="text-primary" onClick={() => void list.refetch()}>{t("common.retry")}</button>} />
          ) : rows.length === 0 ? (
            <StateBlock
              kind="empty"
              title={filter === "overdue" ? t("inbox.emptyOverdue") : t("inbox.empty")}
              action={
                filter === "overdue" ? (
                  // a dead end is not an empty state: the way out is one click, back to the whole queue
                  <button type="button" className="text-[13px] font-semibold text-primary" onClick={() => setFilter("open")}>
                    {t("inbox.showAll")}
                  </button>
                ) : undefined
              }
            />
          ) : (
            rows.map((row) => {
              const active = row.id === selected;
              return (
                <button
                  key={row.id}
                  type="button"
                  data-request-row={row.id}
                  onClick={() => openRequest(row.id)}
                  className={`w-full border-b border-line px-4 py-3 text-start transition ${active ? "bg-primary-soft/60" : "hover:bg-[#FAFBFF]"}`}
                  style={row.is_overdue && !active ? { background: "var(--row-warn)" } : undefined}
                >
                  <div className="flex items-center gap-2">
                    <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full text-[11px] font-bold text-sidebar" style={{ background: "#C7D2FE" }}>
                      {initials(row.employee_name)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-[13px] font-bold text-ink">{row.employee_name}</span>
                        <span className="shrink-0 text-[11px] text-muted">{relativeTime(row.last_activity_at ?? row.created_at)}</span>
                      </div>
                      <div className="mt-[2px] flex items-center gap-1.5">
                        <span className="text-[12px] font-semibold text-primary">#{row.number}</span>
                        <span className="truncate text-[12px] text-muted">{row.subject}</span>
                      </div>
                      <div className="mt-1.5 flex items-center gap-1.5">
                        <Badge tone={row.is_overdue ? "r" : row.status === "resolved" ? "g" : "b"}>
                          {CHANNEL_ICON[row.channel]}
                          {row.is_overdue ? t("status.overdue") : t(`status.${row.status}`)}
                        </Badge>
                        {row.sla_hours_late ? (
                          <span className="text-[11px] font-bold text-[var(--r-ink)]">{t("inbox.slaLate", { n: row.sla_hours_late })}</span>
                        ) : row.sla_minutes_remaining ? (
                          <span className="flex items-center gap-1 text-[11px] text-muted">
                            <Clock3 size={12} strokeWidth={1.8} />
                            {humanDuration(row.sla_minutes_remaining, i18n.language)}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </Card>

      {/* thread */}
      <Card padded={false} className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {detail.isPending ? (
          <StateBlock kind="loading" />
        ) : detail.isError ? (
          <StateBlock kind="error" title={t("common.notFoundTitle")} body={t("common.errorBody")} />
        ) : detail.data ? (
          <>
            <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-[14px]">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="truncate text-[16px] font-bold text-ink">{detail.data.subject}</h3>
                  <span className="text-[13px] font-semibold text-primary">#{detail.data.number}</span>
                </div>
                <p className="mt-1 text-[12px] text-muted">
                  {detail.data.client_name} · {detail.data.employee_name}
                  {detail.data.mobile ? <span dir="ltr"> · {detail.data.mobile}</span> : null}
                </p>
              </div>
              <div className="relative">
                <select
                  className="field w-[190px] py-[7px] pe-8 text-[13px]"
                  value={detail.data.status}
                  onChange={(event) => setStatus.mutate(event.target.value)}
                  aria-label={t("inbox.updateStatus")}
                >
                  {statuses.map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={15} className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted" style={{ insetInlineEnd: 10 }} />
              </div>
            </header>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-[#FAFAFE] px-5 py-[18px]">
              {detail.data.description ? (
                <p className="rounded-btn bg-[#F2F4FB] px-3 py-2 text-[13px] text-muted">{detail.data.description}</p>
              ) : null}
              {(detail.data.attachments ?? []).length ? (
                <div className="rounded-btn border border-line bg-white px-3 py-2">
                  <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-muted">{t("inbox.attachments")}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {(detail.data.attachments ?? []).map((file) =>
                      file.url ? (
                        <a
                          key={file.id}
                          href={file.url}
                          download={file.filename}
                          className="inline-flex items-center gap-1.5 rounded-pill border border-line px-2.5 py-1 text-[12px] font-semibold text-ink"
                        >
                          <Paperclip size={12} strokeWidth={1.8} />
                          <span className="max-w-[180px] truncate">{file.filename}</span>
                          <Download size={12} strokeWidth={1.8} className="text-muted" />
                        </a>
                      ) : null,
                    )}
                  </div>
                </div>
              ) : null}
              {(detail.data?.messages ?? []).map((message) => (
                <Bubble key={message.id} message={message} />
              ))}
              {(detail.data?.notes ?? []).map((note) => (
                <div key={note.id} className="mx-auto max-w-[560px] rounded-btn border border-dashed border-[#E7C778] bg-[var(--a-bg)] px-3 py-2">
                  <div className="mb-1 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-[var(--a-ink)]">
                    <StickyNote size={13} strokeWidth={1.8} />
                    {t("inbox.internalNote")}
                  </div>
                  <p className="text-[13px] text-[var(--a-ink)]">{note.body}</p>
                </div>
              ))}
              <div ref={threadEndRef} />
            </div>

            <footer className="border-t border-line px-5 py-3">
              <div className="flex items-end gap-2">
                <textarea
                  className="field min-h-[44px] flex-1 resize-none py-[11px]"
                  placeholder={t("inbox.reply")}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) send.mutate();
                  }}
                  aria-label={t("inbox.reply")}
                />
                <button
                  type="button"
                  className="btn-secondary !px-3"
                  title={t("inbox.attach")}
                  onClick={() => document.getElementById("bh-attach")?.click()}
                >
                  <Paperclip size={18} strokeWidth={1.8} />
                </button>
                <input
                  id="bh-attach"
                  type="file"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (!file || !selected) return;
                    const form = new FormData();
                    form.append("file", file);
                    void api.upload(`/requests/${selected}/attachments`, form).then(() => queryClient.invalidateQueries({ queryKey: ["requests"] }));
                    event.target.value = "";
                  }}
                />
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => send.mutate()}
                  disabled={send.isPending || draft.trim().length === 0}
                >
                  {send.isPending ? <Clock3 size={17} className="animate-spin" /> : <Send size={17} strokeWidth={1.8} />}
                  {t("inbox.send")}
                </button>
              </div>
              {send.isError ? (
                <p className="mt-2 text-[12px] font-semibold text-[var(--r-ink)]">
                  {send.error instanceof Error ? send.error.message : "error"}
                </p>
              ) : null}
              <div className="mt-2 flex items-center gap-2">
                <input
                  className="field py-[6px] text-[12px]"
                  placeholder={t("inbox.internalNote")}
                  value={noteDraft}
                  onChange={(event) => setNoteDraft(event.target.value)}
                  aria-label={t("inbox.internalNote")}
                />
                <button type="button" className="btn-ghost !py-1.5 text-[13px]" disabled={addNote.isPending || noteDraft.trim() === ""} onClick={() => addNote.mutate()}>
                  {addNote.isPending ? "…" : t("common.save")}
                </button>
              </div>
              {aiStatus.data?.enabled ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn-secondary !py-1.5 text-[13px]"
                    onClick={() => summarize.mutate()}
                    disabled={summarize.isPending}
                    title={t("inbox.aiProvider", { provider: aiStatus.data.driver })}
                  >
                    {summarize.isPending ? <Clock3 size={13} className="animate-spin" /> : <Sparkles size={13} strokeWidth={1.8} />}
                    {t("inbox.aiSummarize")}
                  </button>
                  <span className="text-[11px] text-muted">{t("inbox.aiNeedsReview")}</span>
                  {summarize.isError ? (
                    <span className="text-[12px] font-semibold text-[var(--r-ink)]">
                      {summarize.error instanceof Error ? summarize.error.message : "error"}
                    </span>
                  ) : null}
                </div>
              ) : null}
            </footer>
          </>
        ) : (
          <StateBlock kind="empty" title={t("inbox.noSelection")} />
        )}
      </Card>

      {/* details */}
      <Card className="w-[270px] shrink-0 overflow-y-auto" padded={false}>
        <div className="px-4 py-[14px]">
          <h3 className="text-[13px] font-bold uppercase tracking-wide text-muted">{t("inbox.details")}</h3>
          {detail.data ? (
            <dl className="mt-3 space-y-3">
              <Field label={t("inbox.employee")}>{detail.data.employee_name}</Field>
              <Field label={t("inbox.company")}>{detail.data.client_name}</Field>
              <Field label={t("inbox.coverageType")}>{detail.data.insurance_type ?? "—"}</Field>
              <Field label={t("inbox.insurer")}>{detail.data.insurer ?? "—"}</Field>
              <Field label={t("inbox.assignee")}>{detail.data.assignee_name ?? "—"}</Field>
              <Field label={t("inbox.slaLeft")}>
                {detail.data.sla_hours_late ? (
                  <span className="font-bold text-[var(--r-ink)]">{t("inbox.slaLate", { n: detail.data.sla_hours_late })}</span>
                ) : detail.data.sla_minutes_remaining ? (
                  <span className="text-[var(--g-ink)]">{formatNumber(detail.data.sla_minutes_remaining)} {t("dashboard.minutes")}</span>
                ) : (
                  "—"
                )}
              </Field>

              {(detail.data?.calls ?? []).length > 0 ? (
                <div className="border-t border-line pt-3">
                  <dt className="mb-2 text-[11px] font-bold uppercase tracking-wide text-muted">{t("clients.file.tabs.calls")}</dt>
                  <dd className="space-y-2">
                    {(detail.data?.calls ?? []).map((call) => (
                      <CallRowView key={call.id} call={call} canListen={can("calls.listen")} />
                    ))}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <StateBlock kind="loading" />
          )}
        </div>
      </Card>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-[13px] font-medium text-ink">{children}</dd>
    </div>
  );
}

function Bubble({ message }: { message: MessageRow }) {
  const { t } = useTranslation();
  const outbound = message.direction === "out";
  return (
    <div className={`flex items-end gap-2 ${outbound ? "flex-row-reverse" : ""}`}>
      <Avatar name={outbound ? t("common.appName") : message.channel} size={26} color={outbound ? "var(--primary)" : "#C7D2FE"} />
      <div
        className="max-w-[68%] rounded-[14px] px-3.5 py-2.5 text-[13.5px] leading-[21px]"
        style={{
          background: outbound ? "var(--primary)" : "#fff",
          color: outbound ? "#fff" : "var(--ink)",
          border: outbound ? "none" : "1px solid var(--line-2)",
          borderBottomRightRadius: outbound ? 4 : 14,
          borderBottomLeftRadius: outbound ? 14 : 4,
        }}
      >
        {message.media_kind ? (
          <VoicePlayer
            seconds={message.duration_s ?? 0}
            waveform={message.waveform ?? null}
            src={message.attachments?.find((file) => file.mime.startsWith("audio/"))?.url ?? null}
          />
        ) : null}
        {message.body ? <p>{message.body}</p> : null}
        {(message.attachments ?? [])
          .filter((file) => !file.mime.startsWith("audio/"))
          .map((file) =>
            file.mime.startsWith("image/") && file.url ? (
              <a key={file.id} href={file.url} target="_blank" rel="noreferrer" className="mt-2 block">
                <img src={file.url} alt={file.filename} className="max-h-[220px] rounded-btn border border-line object-contain" />
              </a>
            ) : file.mime.startsWith("video/") && file.url ? (
              <video key={file.id} src={file.url} controls className="mt-2 max-h-[240px] w-full rounded-btn border border-line" />
            ) : file.url ? (
              <a
                key={file.id}
                href={file.url}
                download={file.filename}
                className="mt-2 flex items-center gap-2 rounded-btn border border-line bg-white px-2.5 py-2 text-[12px] font-semibold text-ink"
              >
                <Paperclip size={14} strokeWidth={1.8} />
                <span className="min-w-0 flex-1 truncate">{file.filename}</span>
                <Download size={14} strokeWidth={1.8} className="text-muted" />
              </a>
            ) : null,
          )}
        <div className={`mt-1 flex items-center gap-1 text-[10.5px] ${outbound ? "text-white/70" : "text-muted"}`}>
          {relativeTime(message.created_at)}
          {outbound ? (message.delivery_status === "delivered" ? <Check size={12} strokeWidth={2.4} /> : <Clock3 size={11} strokeWidth={1.8} />) : null}
        </div>
      </div>
    </div>
  );
}

/** 20 bars, 3px wide, 2–16px tall, 4px apart (spec §9.2); plays the signed media URL. */
function VoicePlayer({ seconds, waveform, src }: { seconds: number; waveform: number[] | null; src: string | null }) {
  const { t } = useTranslation();
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const audio = useRef<HTMLAudioElement | null>(null);

  const bars = useMemo(() => {
    const source = waveform && waveform.length >= 20 ? waveform.slice(0, 20) : null;
    if (source) return source.map((value) => 2 + Math.round(Math.abs(value) * 14));
    return Array.from({ length: 20 }, (_, index) => 2 + Math.round(Math.abs(Math.sin(index * 1.7)) * 14));
  }, [waveform]);

  // Without a playable object (no media stored) the player stays a static waveform.
  useEffect(() => {
    if (!src) return;
    const node = audio.current;
    if (!node) return;
    const onTime = () => setProgress(node.duration ? (node.currentTime / node.duration) * 100 : 0);
    const onEnd = () => {
      setPlaying(false);
      setProgress(0);
    };
    node.addEventListener("timeupdate", onTime);
    node.addEventListener("ended", onEnd);
    return () => {
      node.removeEventListener("timeupdate", onTime);
      node.removeEventListener("ended", onEnd);
    };
  }, [src]);

  useEffect(() => {
    if (!src || playing) return;
    const node = audio.current;
    if (node && node.paused === false) {
      node.pause();
      if (node.currentTime === 0) node.load();
    }
  }, [playing, src]);

  async function toggle() {
    const node = audio.current;
    if (!src || !node) {
      setPlaying((value) => !value);
      return;
    }
    if (node.paused) {
      try {
        await node.play();
        setPlaying(true);
      } catch {
        // autoplay blocked or the URL expired: keep the visual state honest
        setPlaying(false);
      }
    } else {
      node.pause();
      setPlaying(false);
    }
  }

  const elapsed = src ? (audio.current?.currentTime ?? 0) : (progress / 100) * seconds;

  return (
    <div className="flex items-center gap-2.5 py-0.5">
      <button
        type="button"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-full"
        style={{ background: playing ? "var(--primary)" : "var(--primary-soft)", color: playing ? "#fff" : "var(--primary-ink)" }}
        onClick={() => void toggle()}
        aria-label={playing ? t("inbox.pause") : t("inbox.play")}
        title={src ? undefined : t("common.noMedia")}
      >
        {playing ? <Pause size={14} strokeWidth={2} /> : <Play size={14} strokeWidth={2} />}
      </button>
      <span className="flex h-[18px] items-center gap-[4px]" aria-hidden="true">
        {bars.map((height, index) => (
          <span
            key={index}
            className="w-[3px] rounded-full transition-all duration-150"
            style={{
              height,
              background: (index / bars.length) * 100 <= progress ? "var(--primary)" : "color-mix(in srgb, var(--muted) 45%, transparent)",
            }}
          />
        ))}
      </span>
      <span className="text-[11px] font-semibold tabular-nums" style={{ color: "var(--muted)" }}>
        {formatDuration(elapsed)} / {formatDuration(seconds)}
      </span>
      {src ? (
        <audio
          ref={(node) => {
            audio.current = node;
          }}
          src={src}
          preload="none"
          className="hidden"
        />
      ) : null}
    </div>
  );
}

function CallRowView({ call, canListen }: { call: CallRow; canListen: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [src, setSrc] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  async function listen() {
    const issued = await api.get<{ url: string }>(`/calls/${call.id}/recording-url`);
    setSrc(issued.url);
    // the server audits each listen, so refetch the audit-backed views once playback starts
    queryClient.invalidateQueries({ queryKey: ["audit"] });
  }

  useEffect(() => {
    if (src) void audioRef.current?.play().catch(() => undefined);
  }, [src]);

  return (
    <div className="rounded-btn border border-line px-2.5 py-2">
      <div className="flex items-center justify-between text-[12px]">
        <span className="font-semibold text-ink">{call.caller_name ?? t("inbox.voiceNote")}</span>
        <span className="text-muted tabular-nums">{formatDuration(call.duration_s)}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        {call.has_recording ? (
          canListen ? (
            <button type="button" className="btn-secondary !px-2 !py-1 text-[12px]" onClick={() => void listen()}>
              <Headphones size={14} strokeWidth={1.8} />
              {t("inbox.play")}
            </button>
          ) : (
            <span className="text-[11px] font-semibold text-muted">{t("common.forbiddenTitle")}</span>
          )
        ) : (
          <span className="text-[11px] text-muted">{t("common.none")}</span>
        )}
        {call.has_recording ? <Download size={14} strokeWidth={1.8} className="text-muted" /> : null}
      </div>
      {src ? <audio ref={audioRef} src={src} controls className="mt-2 w-full" /> : null}
    </div>
  );
}
