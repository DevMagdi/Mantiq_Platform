/**
 * Screen 4 — the rep's phone view (390×844): one-handed lead list, quick call/WhatsApp,
 * 84px bottom bar and a compose FAB. Same scoped endpoint as the desktop Kanban, so isolation
 * is not a mobile-only convention (spec §6).
 */
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronLeft, Inbox, PhoneCall, Plus, Search, UserRound } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Avatar, Badge, Chip, Segmented, Spinner, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { asText, includesText, lower } from "@/lib/text";
import { followupLabel, formatNumber } from "@/lib/format";
import type { Lead, LeadPage } from "@/lib/api/views";

const FILTERS = [
  { value: "all", label: "leads.filters.all" },
  { value: "new", label: "leads.filters.new" },
  { value: "meeting_scheduled", label: "leads.filters.meeting" },
  { value: "quote_sent", label: "leads.filters.quote" },
] as const;

type Filter = (typeof FILTERS)[number]["value"];

export default function MobileLeadsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const leads = useQuery({
    queryKey: ["leads", "mobile", filter],
    queryFn: () => api.get<LeadPage>("/leads", { status: filter === "all" ? undefined : [filter], page_size: 50 }),
  });

  const rows = useMemo(() => {
    const items = leads.data?.items ?? [];
    const needle = lower(asText(query)).trim();
    return needle ? items.filter((lead) => includesText(`${asText(lead.name)} ${asText(lead.mobile)}`, needle)) : items;
  }, [leads.data, query]);

  async function refresh() {
    setRefreshing(true);
    await leads.refetch();
    setRefreshing(false);
  }

  return (
    <div className="min-h-screen bg-[#E9ECF6] py-0 sm:py-6">
      <div className="relative mx-auto flex min-h-screen w-full flex-col bg-bg sm:min-h-[844px] sm:max-w-[390px] sm:rounded-card sm:border sm:border-line sm:shadow-card">
        <header className="sticky top-0 z-10 bg-bg/95 px-4 pb-3 pt-4 backdrop-blur">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <Avatar name={user?.full_name} size={34} />
              <div className="min-w-0">
                <p className="truncate text-[14.5px] font-bold text-ink">{t("leads.mobile.mine")}</p>
                <p className="text-[11.5px] text-muted">
                  {t("leads.mobile.active", { n: formatNumber(leads.data?.total ?? 0) })}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              aria-busy={refreshing}
              className="grid h-9 w-9 place-items-center rounded-full bg-[var(--primary-soft)] text-[var(--primary-ink)] transition active:scale-95"
              aria-label={t("common.retry")}
            >
              {refreshing ? <Spinner /> : <CalendarClock size={17} strokeWidth={1.8} />}
            </button>
          </div>

          <div className="relative mt-3">
            <Search size={16} strokeWidth={1.8} className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted" style={{ insetInlineStart: 11 }} />
            <input
              className="field py-2.5 ps-[32px] text-[13px]"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("leads.search")}
              aria-label={t("leads.search")}
            />
          </div>

          <div className="-mx-4 mt-3 overflow-x-auto px-4 pb-0.5">
            <Segmented
              ariaLabel={t("leads.filters.all")}
              value={filter}
              onChange={(next) => setFilter(next as Filter)}
              options={FILTERS.map((option) => ({ value: option.value, label: t(option.label) }))}
            />
          </div>
        </header>

        <p className="px-4 pb-1 pt-2 text-[11.5px] font-semibold text-muted">{t("leads.mobile.seeOnlyYours")}</p>

        <main className="flex-1 space-y-2.5 px-4 pb-[110px] pt-1">
          {leads.isPending ? (
            <StateBlock kind="loading" />
          ) : leads.isError ? (
            <StateBlock kind="error" action={<button type="button" className="text-primary" onClick={() => void leads.refetch()}>{t("common.retry")}</button>} />
          ) : rows.length === 0 ? (
            <StateBlock kind="empty" title={t("leads.empty")} body={query ? t("leads.noMatches", { q: query }) : undefined} />
          ) : (
            rows.map((lead) => <MobileLeadCard key={lead.id} lead={lead} onOpen={() => navigate(`/leads/${lead.id}`)} />)
          )}
        </main>

        <button
          type="button"
          onClick={() => navigate("/leads/new")}
          className="fixed bottom-[100px] grid h-14 w-14 place-items-center rounded-full text-white shadow-card transition active:scale-95"
          style={{ insetInlineEnd: "max(20px, calc(50% - 175px))", background: "var(--primary)" }}
          aria-label={t("leads.newLead")}
        >
          <Plus size={24} strokeWidth={2.2} />
        </button>

        <nav
          className="sticky bottom-0 z-10 grid h-[84px] grid-cols-4 items-center border-t border-line bg-card"
          style={{ height: 84, paddingBottom: "env(safe-area-inset-bottom)" }}
        >
          <TabButton icon={<UserRound size={19} strokeWidth={1.8} />} label={t("leads.tabbar.leads")} active onClick={() => navigate("/mobile/leads")} />
          <TabButton icon={<Inbox size={19} strokeWidth={1.8} />} label={t("leads.tabbar.requests")} onClick={() => navigate("/inbox")} />
          <TabButton icon={<Search size={19} strokeWidth={1.8} />} label={t("leads.tabbar.clients")} onClick={() => navigate("/clients")} />
          <TabButton icon={<PhoneCall size={19} strokeWidth={1.8} />} label={t("leads.tabbar.account")} onClick={() => navigate("/users")} />
        </nav>
      </div>
    </div>
  );
}

function MobileLeadCard({ lead, onOpen }: { lead: Lead; onOpen: () => void }) {
  const { t } = useTranslation();
  const follow = followupLabel(lead.next_followup_at, {
    today: t("leads.followupToday"),
    tomorrow: t("leads.followupTomorrow"),
    late: t("leads.followupLate"),
    next: t("leads.followupNext"),
  });
  return (
    <article data-lead-card={lead.id} data-lead-status={lead.status} className="rounded-card border border-line bg-card px-3.5 py-3 shadow-card">
      <button type="button" className="flex w-full items-start gap-2.5 text-start" onClick={onOpen}>
        <Avatar name={lead.name} size={34} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[14px] font-bold text-ink">{lead.name}</h3>
          <p className="mt-0.5 truncate text-[12px] text-muted">
            {lead.contact_name ?? "—"} · {formatNumber(lead.headcount ?? 0)} {t("leads.insured_count")}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Chip tone={follow.tone}>{follow.text}</Chip>
            <Badge tone={lead.status === "won" ? "g" : "b"}>{t(`leadStatus.${lead.status}`)}</Badge>
          </div>
        </div>
        <ChevronLeft size={17} strokeWidth={1.8} className="mt-1 shrink-0 text-line-2" />
      </button>
      <div className="mt-2.5 flex items-center gap-2 border-t border-line pt-2.5">
        <a href={`tel:${lead.mobile ?? ""}`} className="flex flex-1 items-center justify-center gap-1.5 rounded-btn py-2 text-[12.5px] font-semibold" style={{ background: "var(--g-bg)", color: "var(--g-ink)" }}>
          <PhoneCall size={14} strokeWidth={1.8} />
          {t("common.call")}
        </a>
        <a
          href={`https://wa.me/${(lead.mobile ?? "").replace(/\D/g, "")}`}
          target="_blank"
          rel="noreferrer"
          className="flex flex-1 items-center justify-center gap-1.5 rounded-btn py-2 text-[12.5px] font-semibold"
          style={{ background: "var(--primary-soft)", color: "var(--primary-ink)" }}
        >
          {t("channel.whatsapp")}
        </a>
      </div>
    </article>
  );
}

function TabButton({ icon, label, active, onClick }: { icon: ReactNode; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-full flex-col items-center justify-center gap-1 text-[11px] font-semibold transition-colors duration-150"
      style={{ color: active ? "var(--primary)" : "var(--muted)" }}
      aria-current={active ? "page" : undefined}
    >
      {icon}
      {label}
    </button>
  );
}
