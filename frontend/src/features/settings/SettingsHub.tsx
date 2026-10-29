/**
 * The settings hub: integrations, underwriting defaults, and the users & roles tab (spec §8, ADR-016).
 * The route is gated on `settings.manage`; the users tab is *additionally* gated on `users.manage`,
 * because grouping screens in one place must not quietly widen what a role can do.
 */
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { Button, Card, PageHeader, Segmented, StateBlock, TableSkeleton } from "@/components/ui";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { relativeTime } from "@/lib/format";

import { ProviderCard } from "./ProviderCard";
import { UnderwritingPanel } from "./UnderwritingPanel";
import { visibleTabs, type EventRow, type ProviderState, type SettingsTab, type WebhookUrls } from "./model";

// the users screen keeps its own bundle: it is a heavy page and most visits to this hub never open it
const UsersPage = lazy(() => import("@/features/users/UsersPage"));

export default function SettingsHub() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();

  const tabs = visibleTabs(user?.permissions ?? []);
  const requested = params.get("tab") as SettingsTab["key"] | null;
  const active = tabs.find((tab) => tab.key === requested) ?? tabs[0] ?? null;

  const providers = useQuery({
    queryKey: ["settings", "integrations"],
    queryFn: () => api.get<ProviderState[]>("/settings/integrations"),
    staleTime: 15_000,
    refetchOnWindowFocus: false,
    enabled: active?.key === "integrations",
  });
  const webhookUrls = useQuery({
    queryKey: ["settings", "webhook-urls"],
    queryFn: () => api.get<WebhookUrls>("/settings/integrations/webhook-urls"),
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    enabled: active?.key === "integrations",
  });

  if (!active) return <StateBlock kind="forbidden" />;

  return (
    <>
      <PageHeader title={t("settings.title")} subtitle={t("settings.subtitle")} />

      <div className="mb-[18px]">
        <Segmented
          ariaLabel={t("settings.tabsAria")}
          value={active.key}
          onChange={(next) => setParams(next === "integrations" ? {} : { tab: next })}
          options={tabs.map((tab) => ({ value: tab.key, label: t(tab.labelKey) }))}
        />
      </div>

      {active.key === "integrations" ? (
        providers.isPending ? (
          <Card>
            <TableSkeleton rows={6} cols={3} />
          </Card>
        ) : providers.isError ? (
          <Card>
            <StateBlock kind="error" action={<Button variant="ghost" onClick={() => void providers.refetch()}>{t("common.retry")}</Button>} />
          </Card>
        ) : (
          <div className="grid gap-[18px]">
            {(providers.data ?? []).map((state) => {
              const found = webhookUrls.data?.items.find((item) => item.provider === state.provider);
              return <ProviderCard key={state.provider} state={state} webhookUrl={found ? { url: found.url, note: found.note } : undefined} />;
            })}
            <EventsCard />
          </div>
        )
      ) : null}

      {active.key === "policies" ? <UnderwritingPanel /> : null}
      {active.key === "users" ? (
        <Suspense fallback={<Card><TableSkeleton rows={6} cols={3} /></Card>}>
          <UsersPage />
        </Suspense>
      ) : null}
    </>
  );
}

/** The last inbound deliveries, straight from `webhook_events` — the answer to «هل وصل شيء؟». */
function EventsCard() {
  const { t } = useTranslation();
  const events = useQuery({
    queryKey: ["settings", "events"],
    queryFn: () => api.get<EventRow[]>("/settings/integrations/events"),
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-[15px] font-bold text-ink">{t("settings.events")}</h3>
        <span className="text-[12px] text-muted">{t("settings.eventsHint")}</span>
      </div>
      {events.isPending ? (
        <TableSkeleton rows={3} cols={3} />
      ) : !events.data?.length ? (
        <p className="py-3 text-center text-[13px] text-muted">{t("settings.eventsEmpty")}</p>
      ) : (
        <ul className="divide-y divide-line">
          {events.data.slice(0, 8).map((event) => (
            <li key={event.id} className="flex items-center gap-3 py-2 text-[13px]">
              <span className="w-[92px] shrink-0 font-semibold text-ink">{t(`settings.provider.${event.provider}`, { defaultValue: event.provider })}</span>
              <span className="min-w-0 flex-1 truncate text-muted" dir="ltr">
                {event.event_type}
                {event.external_id ? ` · ${event.external_id}` : ""}
              </span>
              <span className="shrink-0 text-[12px] text-muted">{event.occurred_at ? relativeTime(event.occurred_at) : ""}</span>
              <span
                className="shrink-0 rounded-pill px-2 py-[2px] text-[11px] font-bold"
                style={event.status === "error" ? { background: "var(--r-bg)", color: "var(--r-ink)" } : { background: "var(--t-bg)", color: "var(--t-ink)" }}
              >
                {event.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
