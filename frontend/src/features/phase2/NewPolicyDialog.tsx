/**
 * «وثيقة جديدة» — the draft a manager can actually create from the screen (R7, spec §2.1).
 *
 * Before this dialog the policies screen had exactly one action, and it navigated to the issuing *pipeline*:
 * a manager who had just closed a deal could add a case but not a policy, so screen 12 depended on the API
 * being called by something else. This is that something else.
 *
 * Three deliberate behaviours:
 *
 * - **the fields the API requires are the fields on the form** (client, start date, premium) and the submit
 *   button stays disabled until they are there — a 422 that names a field the user was never shown is a bug
 *   dressed as validation;
 * - **a 422 is rendered next to its field**, from the API's own `details[].field`, in Arabic; anything else
 *   becomes a toast. The screen never guesses what went wrong;
 * - **the client is fixed when the dialog is opened from a client file**, so the two entry points cannot
 *   drift into “create a policy for somebody else by mis-clicking a select”.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui";
import { ApiError_, api } from "@/lib/api/client";
import type { AnyPage } from "@/lib/api/views";

type CreatePolicy = {
  id: string;
  policy_no: string | null;
  status: string;
  installments: unknown[];
};

type ClientRow = { id: string; name: string };
type Ref = { id: string; name: string };

/** The API's field name → the Arabic label the person sees next to the input. */
const FIELD_LABELS: Record<string, string> = {
  client_id: "phase2.form.client",
  start_date: "phase2.form.start",
  end_date: "phase2.form.end",
  premium_amount: "phase2.form.premium",
  insurance_type_id: "phase2.form.type",
  insurer_id: "phase2.form.insurer",
  payment_plan: "phase2.form.plan",
  commission_rate: "phase2.form.commission",
};

type FieldErrors = Record<string, string>;

/** `details` is either `{field, message}` objects (our validation) or FastAPI's `{loc, msg}` list. */
function errorsFrom(error: unknown, t: (key: string, options?: Record<string, unknown>) => string): FieldErrors {
  const out: FieldErrors = {};
  if (!(error instanceof ApiError_)) return out;
  const details = Array.isArray(error.details) ? (error.details as Record<string, unknown>[]) : [];
  for (const detail of details) {
    const field = String(detail.field ?? (Array.isArray(detail.loc) ? (detail.loc as unknown[]).at(-1) : "") ?? "");
    if (!field) continue;
    // the API's message when it has one in the reader's language, the label map otherwise: the field name
    // alone («premium_amount») is not something to show a sales manager
    const label = FIELD_LABELS[field] ? t(FIELD_LABELS[field]) : field;
    out[field] = String(detail.message ?? detail.msg ?? t("phase2.form.invalid", { field: label }));
  }
  return out;
}

export function NewPolicyDialog({ clientId, onClose }: { clientId?: string; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [client, setClient] = useState(clientId ?? "");
  const [insurer, setInsurer] = useState("");
  const [type, setType] = useState("");
  const [start, setStart] = useState(() => new Date().toISOString().slice(0, 10));
  const [end, setEnd] = useState("");
  const [premium, setPremium] = useState("");
  const [plan, setPlan] = useState("monthly");
  const [headcount, setHeadcount] = useState("");
  const [rate, setRate] = useState("");
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});

  const clients = useQuery({
    queryKey: ["clients", "picker"],
    queryFn: () => api.get<AnyPage>("/clients", { page_size: 200 }),
    enabled: !clientId,
  });
  const insurers = useQuery({ queryKey: ["insurers"], queryFn: () => api.get<Ref[]>("/insurers") });
  const types = useQuery({ queryKey: ["insurance-types"], queryFn: () => api.get<Ref[]>("/insurance-types") });

  const create = useMutation({
    mutationFn: () =>
      api.post<CreatePolicy>("/policies", {
        client_id: client,
        insurer_id: insurer || undefined,
        insurance_type_id: type || undefined,
        start_date: start || undefined,
        end_date: end || undefined,
        premium_amount: premium ? Number(premium) : undefined,
        payment_plan: plan,
        headcount: headcount ? Number(headcount) : undefined,
        commission_rate: rate === "" ? undefined : Number(rate),
        notes: notes || undefined,
      }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["policies"] });
      toast(t("phase2.draftCreated", { count: created.installments?.length ?? 0 }));
      onClose();
    },
    onError: (error: unknown) => {
      const fieldErrors = errorsFrom(error, t);
      setErrors(fieldErrors);
      if (Object.keys(fieldErrors).length === 0) toast(error instanceof Error ? error.message : t("common.errorBody"), "error");
    },
  });

  const valid = Boolean(client) && Boolean(start) && Number(premium) > 0;

  return (
    <Modal
      title={t("phase2.newPolicy")}
      onClose={onClose}
      width={620}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={create.isPending} disabled={!valid} onClick={() => create.mutate()}>
            {t("phase2.saveDraft")}
          </Button>
        </>
      }
    >
      <p className="text-[12.5px] text-muted">{t("phase2.newPolicyNote")}</p>

      {clientId ? null : (
        <>
          <label className="label mt-3" htmlFor="np-client">
            {t("phase2.form.client")}
          </label>
          <select id="np-client" className="field" value={client} onChange={(event) => setClient(event.target.value)}>
            <option value="">{t("phase2.form.pick")}</option>
            {((clients.data?.items ?? []) as ClientRow[]).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
          {errors.client_id ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.client_id}</p> : null}
        </>
      )}

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="np-type">
            {t("phase2.form.type")}
          </label>
          <select id="np-type" className="field" value={type} onChange={(event) => setType(event.target.value)}>
            <option value="">{t("phase2.form.pick")}</option>
            {(types.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
          {errors.insurance_type_id ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.insurance_type_id}</p> : null}
        </div>
        <div>
          <label className="label" htmlFor="np-insurer">
            {t("phase2.form.insurer")}
          </label>
          <select id="np-insurer" className="field" value={insurer} onChange={(event) => setInsurer(event.target.value)}>
            <option value="">{t("phase2.form.pick")}</option>
            {(insurers.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
          {errors.insurer_id ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.insurer_id}</p> : null}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="np-start">
            {t("phase2.form.start")}
          </label>
          <input id="np-start" type="date" className="field" value={start} onChange={(event) => setStart(event.target.value)} />
          {errors.start_date ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.start_date}</p> : null}
        </div>
        <div>
          <label className="label" htmlFor="np-end">
            {t("phase2.form.end")}
          </label>
          <input id="np-end" type="date" className="field" value={end} onChange={(event) => setEnd(event.target.value)} />
          <p className="mt-1 text-[11.5px] text-muted">{t("phase2.form.endHint")}</p>
          {errors.end_date ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.end_date}</p> : null}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="np-premium">
            {t("phase2.form.premium")}
          </label>
          <input
            id="np-premium"
            inputMode="decimal"
            dir="ltr"
            className="field"
            value={premium}
            onChange={(event) => setPremium(event.target.value.replace(/[^\d.]/g, ""))}
          />
          {errors.premium_amount ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.premium_amount}</p> : null}
        </div>
        <div>
          <label className="label" htmlFor="np-plan">
            {t("phase2.form.plan")}
          </label>
          <select id="np-plan" className="field" value={plan} onChange={(event) => setPlan(event.target.value)}>
            <option value="monthly">{t("phase2.plan.monthly")}</option>
            <option value="quarterly">{t("phase2.plan.quarterly")}</option>
            <option value="semiannual">{t("phase2.plan.semiannual")}</option>
            <option value="cash">{t("phase2.plan.cash")}</option>
          </select>
          {errors.payment_plan ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.payment_plan}</p> : null}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="np-headcount">
            {t("phase2.form.headcount")}
          </label>
          <input
            id="np-headcount"
            inputMode="numeric"
            dir="ltr"
            className="field"
            value={headcount}
            onChange={(event) => setHeadcount(event.target.value.replace(/\D/g, ""))}
          />
        </div>
        <div>
          <label className="label" htmlFor="np-rate">
            {t("phase2.form.commission")}
          </label>
          <input
            id="np-rate"
            inputMode="decimal"
            dir="ltr"
            className="field"
            placeholder={t("phase2.form.commissionHint")}
            value={rate}
            onChange={(event) => setRate(event.target.value.replace(/[^\d.]/g, ""))}
          />
          {errors.commission_rate ? <p className="mt-1 text-[11.5px] text-[var(--r-ink)]">{errors.commission_rate}</p> : null}
        </div>
      </div>

      <label className="label mt-3" htmlFor="np-notes">
        {t("phase2.form.notes")}
      </label>
      <textarea id="np-notes" className="field min-h-[64px]" value={notes} onChange={(event) => setNotes(event.target.value)} />
    </Modal>
  );
}
