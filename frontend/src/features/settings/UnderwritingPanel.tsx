/**
 * Underwriting defaults (spec §8): the three numbers `POST /policies` seeds a new policy with. They are
 * kept deliberately small — every field on this panel is a field some code reads. The screen says so in
 * its own subtitle, because the alternative (a settings block nobody consumes) is how products rot.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge, Button, Card, StateBlock } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { asText, upper } from "@/lib/text";
import type { components } from "@/lib/api/schema";
import { useState } from "react";

import { INSTALLMENTS_BY_PLAN, PAYMENT_PLANS as PLANS } from "./model";

type Defaults = components["schemas"]["UnderwritingDefaultsOut"];

export function UnderwritingPanel() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["settings", "policies"],
    queryFn: () => api.get<Defaults>("/settings/policies"),
    refetchOnWindowFocus: false,
  });
  const [draft, setDraft] = useState<Defaults | null>(null);
  const values = draft ?? query.data ?? null;

  const save = useMutation({
    mutationFn: () => api.put<Defaults>("/settings/policies", values ?? {}),
    onSuccess: async (next) => {
      setDraft(null);
      queryClient.setQueryData(["settings", "policies"], next);
      toast(t("settings.policiesSaved"));
    },
    onError: (error: Error) => toast(error.message, "error"),
  });

  if (query.isPending) return <StateBlock kind="loading" />;
  if (query.isError || !values) return <StateBlock kind="error" />;

  const dirty = JSON.stringify(values) !== JSON.stringify(query.data);
  // the number the preview shows is the number the service builds; `tests/settingsView.test.tsx` pins it
  const count = INSTALLMENTS_BY_PLAN[values.default_payment_plan as (typeof PLANS)[number]] ?? 1;

  return (
    <Card className="max-w-[720px]">
      <h3 className="text-[15px] font-bold text-ink">{t("settings.underwritingTitle")}</h3>
      <p className="mt-1 text-[12px] text-muted">{t("settings.underwritingHint")}</p>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1 block text-[12px] font-semibold text-muted">{t("settings.underwriting.commission")}</span>
          <input
            className="w-full rounded-btn border border-line bg-white px-3 py-2 text-[13px] text-ink"
            type="number"
            min={0}
            max={100}
            step={0.25}
            value={values.default_commission_pct}
            onChange={(event) => setDraft({ ...values, default_commission_pct: Number(event.target.value) })}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-semibold text-muted">{t("settings.underwriting.plan")}</span>
          <select
            className="w-full rounded-btn border border-line bg-white px-3 py-2 text-[13px] text-ink"
            value={values.default_payment_plan}
            onChange={(event) => setDraft({ ...values, default_payment_plan: event.target.value })}
          >
            {PLANS.map((plan) => (
              <option key={plan} value={plan}>
                {t(`settings.plan.${plan}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-semibold text-muted">{t("settings.underwriting.currency")}</span>
          <input
            className="w-full rounded-btn border border-line bg-white px-3 py-2 text-[13px] uppercase text-ink"
            value={values.default_currency}
            maxLength={8}
            onChange={(event) => setDraft({ ...values, default_currency: upper(asText(event.target.value)) })}
          />
        </label>
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-muted">
        {t("settings.underwriting.appliesTo")}
        <Badge tone="b">{count}</Badge>
      </p>

      <footer className="mt-4 flex items-center justify-end gap-2 border-t border-line pt-3">
        <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(null)}>
          {t("common.cancel")}
        </Button>
        <Button variant="primary" busy={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>
          {t("common.save")}
        </Button>
      </footer>
    </Card>
  );
}
