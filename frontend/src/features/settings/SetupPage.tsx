/**
 * First-time setup (B1): the owner's ordered path to a working Pilot. Every step deep-links to the
 * screen that already owns the work — the wizard sequences; it does not duplicate forms.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Circle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge, Card, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { connectionBadge, type ProviderState } from "@/features/settings/model";

const STEPS = [
  { key: "office", to: "/settings?tab=policies" },
  { key: "users", to: "/settings?tab=users" },
  { key: "channels", to: "/settings" },
  { key: "phase2", to: "/issuing" },
  { key: "finance", to: "/finance/cashbox" },
  { key: "privacy", to: "/setup" },
] as const;

export default function SetupPage() {
  const { t } = useTranslation();
  const integrations = useQuery({
    queryKey: ["settings", "integrations"],
    queryFn: () => api.get<ProviderState[]>("/settings/integrations"),
  });

  return (
    <>
      <PageHeader title={t("setup.title")} subtitle={t("setup.subtitle")} />
      <div className="grid gap-3">
        {STEPS.map((step, i) => (
          <Card key={step.key} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Circle size={18} className="shrink-0 text-muted" />
              <div>
                <div className="text-[14px] font-bold text-ink">
                  {i + 1}. {t(`setup.steps.${step.key}`)}
                </div>
                {step.key === "channels" && integrations.data ? (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {integrations.data.map((row) => {
                      const badge = connectionBadge(row);
                      return (
                        <Badge key={row.provider} tone={badge.key === "connected" ? "g" : badge.key === "error" ? "r" : "a"}>
                          {row.provider}: {t(badge.labelKey)}
                        </Badge>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>
            <Link to={step.to} className="inline-flex shrink-0 items-center gap-1 text-[13px] font-bold text-primary">
              {t("setup.open")} <ArrowLeft size={14} />
            </Link>
          </Card>
        ))}
      </div>

      <Card className="mt-4">
        <SectionTitle>{t("setup.privacyTitle")}</SectionTitle>
        {integrations.isPending ? <StateBlock kind="loading" /> : null}
        <p className="mb-2 flex items-start gap-2 text-[13px] leading-6 text-ink">
          <CheckCircle2 size={15} className="mt-1 shrink-0 text-primary" /> {t("setup.privacyBody")}
        </p>
        <p className="mb-2 text-[13px] leading-6 text-ink">{t("setup.consentBody")}</p>
        <p className="text-[12.5px] text-muted">{t("setup.consentNote")}</p>
      </Card>
    </>
  );
}
