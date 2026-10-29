/**
 * Screen 13 — the issuing pipeline: agreement → documents → sent → issued → delivered,
 * with the insurer's required-document checklist on each card (Phase 2, flag-gated).
 */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, FileCheck2, Send } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge, Button, Card, PageHeader, SectionTitle, StateBlock } from "@/components/ui";
import { api } from "@/lib/api/client";
import { formatMoney, formatNumber } from "@/lib/format";
import type { IssuingCase } from "@/lib/api/views";
import { Phase2Screen } from "./phase2Gate";

const STAGES = [
  { key: "agreement", label: "phase2.steps.agreement", icon: FileCheck2 },
  { key: "docs_review", label: "phase2.steps.documents", icon: CircleDashed },
  { key: "sent_to_insurer", label: "phase2.steps.send", icon: Send },
  { key: "issued", label: "phase2.steps.issue", icon: CheckCircle2 },
  { key: "delivered", label: "phase2.steps.deliver", icon: CheckCircle2 },
] as const;

export default function IssuingPage() {
  return (
    <Phase2Screen>
      <IssuingContent />
    </Phase2Screen>
  );
}

function IssuingContent() {
  const { t } = useTranslation();
  const cases = useQuery({ queryKey: ["issuing-cases"], queryFn: () => api.get<IssuingCase[]>("/issuing-cases") });

  if (cases.isPending) return <StateBlock kind="loading" />;
  if (cases.isError) {
    return <StateBlock kind="error" action={<Button onClick={() => void cases.refetch()}>{t("common.retry")}</Button>} />;
  }

  const rows = cases.data ?? [];

  return (
    <>
      <PageHeader
        title={t("phase2.issuingTitle")}
        subtitle={t("phase2.policiesSubtitle")}
        actions={
          <div className="flex items-center gap-2">
            {STAGES.map((stage) => (
              <Badge key={stage.key} tone={rows.filter((row) => row.stage === stage.key).length > 0 ? "b" : "t"}>
                {t(stage.label)} {formatNumber(rows.filter((row) => row.stage === stage.key).length)}
              </Badge>
            ))}
          </div>
        }
      />

      {rows.length === 0 ? (
        <Card>
          <StateBlock kind="empty" title={t("phase2.issuing")} body={t("phase2.autoInstallments")} />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {rows.map((row) => {
            const index = STAGES.findIndex((stage) => stage.key === row.stage);
            const uploaded = (row.documents ?? []).filter((doc) => doc.status === "uploaded").length;
            return (
              <Card key={String(row.id)}>
                <SectionTitle
                  action={<Badge tone={index >= STAGES.length - 2 ? "g" : "a"}>{t(`phase2.steps.${row.stage}`, { defaultValue: row.stage })}</Badge>}
                >
                  <span className="block truncate">{row.client ?? "—"}</span>
                </SectionTitle>

                <ol className="mb-4 flex items-center gap-1">
                  {STAGES.map((stage, stageIndex) => (
                    <li key={stage.key} className="flex-1">
                      <span
                        className="block h-[5px] rounded-full transition-all duration-150"
                        style={{ background: stageIndex <= index ? "var(--primary)" : "var(--line-2)" }}
                        title={t(stage.label)}
                      />
                    </li>
                  ))}
                </ol>

                <dl className="grid grid-cols-2 gap-3 text-[12.5px]">
                  <div>
                    <dt className="text-muted">{t("dashboard.colCoverage")}</dt>
                    <dd className="mt-0.5 font-semibold text-ink">{row.insurer ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">{t("phase2.premium")}</dt>
                    <dd className="mt-0.5 font-semibold text-ink tabular-nums">{row.premium_amount != null ? formatMoney(row.premium_amount) : "—"}</dd>
                  </div>
                </dl>

                <p className="mt-4 text-[12px] font-semibold text-muted">{t("phase2.docsRequired")}</p>
                <ul className="mt-2 space-y-1.5">
                  {(row.documents ?? []).map((doc) => (
                    <li key={doc.name} className="flex items-center justify-between gap-2 text-[12.5px]">
                      <span className="min-w-0 truncate text-ink">{doc.name}</span>
                      <span
                        className="flex shrink-0 items-center gap-1 font-semibold"
                        style={{ color: doc.status === "uploaded" ? "var(--g-ink)" : "var(--a-ink)" }}
                      >
                        {doc.status === "uploaded" ? <CheckCircle2 size={13} strokeWidth={2} /> : <CircleDashed size={13} strokeWidth={2} />}
                        {doc.status === "uploaded" ? t("phase2.uploaded") : t("phase2.missing")}
                      </span>
                    </li>
                  ))}
                </ul>

                <p className="mt-3 text-[11.5px] text-muted">
                  {formatNumber(uploaded)} / {formatNumber((row.documents ?? []).length)} · {t("phase2.docsRequired")}
                </p>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
