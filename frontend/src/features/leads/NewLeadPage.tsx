/**
 * Screen 10 — new lead. The mobile field runs a live duplicate probe against the same scoped
 * search the API uses, and a 409 from `POST /leads` is surfaced as a link to the existing lead.
 */
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm, type UseFormRegisterReturn } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { AlertCircle, CheckCircle2, Search } from "lucide-react";

import { Button, Card, PageHeader, SectionTitle } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, duplicateInfo } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

const SOURCES = ["إحالة", "واتساب", "معرض", "زيارة ميدانية", "فيسبوك"] as const;

const schema = z.object({
  type: z.enum(["company", "individual"]),
  name: z.string().min(2).max(200),
  contact_name: z.string().max(160).optional().or(z.literal("")),
  mobile: z.string().regex(/^01[0-9]{9}$/, { message: "mobile" }),
  email: z.string().email().optional().or(z.literal("")),
  source: z.string().optional().or(z.literal("")),
  headcount: z.coerce.number().int().min(1).max(2_000_000).optional(),
  insurance_type_id: z.string().optional().or(z.literal("")),
  current_insurer_id: z.string().optional().or(z.literal("")),
  current_policy_expiry: z.string().optional().or(z.literal("")),
  budget: z.coerce.number().min(0).optional(),
  needs_text: z.string().max(4000).optional().or(z.literal("")),
  assigned_to: z.string().optional().or(z.literal("")),
  next_followup_at: z.string().min(1, { message: "followup" }),
});

type FormValues = z.infer<typeof schema>;

export default function NewLeadPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isManager } = useAuth();
  const toast = useToast();
  const [dup, setDup] = useState<{ leadId: string | null; name: string | null; hidden: boolean } | null>(null);
  const [probing, setProbing] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { type: "company", mobile: "", next_followup_at: defaultFollowup() },
  });

  const mobile = watch("mobile");
  const type = watch("type");

  const types = useQuery({ queryKey: ["insurance-types"], queryFn: () => api.get<{ id: string; name: string }[]>("/insurance-types") });
  const insurers = useQuery({ queryKey: ["insurers"], queryFn: () => api.get<{ id: string; name: string }[]>("/insurers") });
  const reps = useQuery({
    queryKey: ["sales", "team"],
    queryFn: () => api.get<{ rep_id: string; name: string }[]>("/sales/team"),
    enabled: isManager,
  });

  // 350 ms debounce; the probe uses the list endpoint so it is scope-aware by construction.
  useEffect(() => {
    const clean = mobile?.replace(/\D/g, "") ?? "";
    if (clean.length < 11) {
      setDup(null);
      setProbing(false);
      return;
    }
    setProbing(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api
        .get<{ items: { id: string; name: string }[] }>("/leads", { q: clean, page_size: 3 })
        .then((page) => {
          const hit = page.items[0];
          setDup(hit ? { leadId: hit.id, name: hit.name, hidden: false } : null);
        })
        .catch(() => setDup(null))
        .finally(() => setProbing(false));
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [mobile]);

  const create = useMutation({
    mutationFn: (values: FormValues) =>
      api.post<{ lead_id: string }>("/leads", {
        ...values,
        mobile: normalize(values.mobile),
        email: values.email || undefined,
        headcount: values.headcount ? Number(values.headcount) : undefined,
        budget: values.budget ? Number(values.budget) : undefined,
        insurance_type_id: values.insurance_type_id || undefined,
        current_insurer_id: values.current_insurer_id || undefined,
        current_policy_expiry: values.current_policy_expiry || undefined,
        assigned_to: isManager ? values.assigned_to || undefined : undefined,
        needs_text: values.needs_text || undefined,
        source: values.source || undefined,
        contact_name: values.contact_name || undefined,
        allow_duplicate: allowDuplicate,
      }),
    onSuccess: (result) => {
      toast(t("newLead.created"));
      navigate(`/leads/${result.lead_id}`);
    },
    onError: (error: unknown) => {
      const info = duplicateInfo(error);
      if (info.leadId) {
        setDup({ leadId: info.leadId, name: info.name, hidden: false });
        setAllowDuplicate(false);
        return;
      }
      if (info.hidden) {
        setDup({ leadId: null, name: null, hidden: true });
        return;
      }
      toast(info.message, "error");
    },
  });

  const banner = useMemo(() => {
    if (probing) return { tone: "muted" as const, text: t("newLead.checking") };
    if (dup?.hidden) return { tone: "warn" as const, text: t("newLead.duplicateHidden") };
    if (dup?.leadId) return { tone: "warn" as const, text: t("newLead.duplicateWith", { name: dup.name ?? "" }) };
    if ((mobile ?? "").replace(/\D/g, "").length >= 11) return { tone: "ok" as const, text: t("newLead.noDuplicate") };
    return null;
  }, [probing, dup, mobile, t]);

  return (
    <>
      <PageHeader title={t("newLead.title")} subtitle={t("newLead.subtitle")} />
      <form onSubmit={handleSubmit((values) => create.mutate(values))} className="max-w-[880px]" noValidate>
        <Card>
          <SectionTitle action={
            <div className="inline-flex items-center gap-1 rounded-pill bg-kanban p-1">
              {(["company", "individual"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setValue("type", option, { shouldDirty: true })}
                  className={`rounded-pill px-3 py-1 text-[13px] font-semibold transition ${type === option ? "bg-primary text-white" : "text-muted"}`}
                >
                  {t(`newLead.${option}`)}
                </button>
              ))}
            </div>
          }>
            {t("newLead.company")}
          </SectionTitle>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Text label={t("newLead.name")} error={errors.name ? t("newLead.required") : undefined} register={register("name")} />
            <Text
              label={t("newLead.mobile")}
              dir="ltr"
              placeholder="01012345678"
              error={errors.mobile ? t("newLead.required") : undefined}
              register={register("mobile")}
            />
            <Text label={t("newLead.contact")} register={register("contact_name")} />
            <Text label={t("newLead.email")} dir="ltr" register={register("email")} />
            <Text
              label={t("newLead.headcount")}
              type="number"
              dir="ltr"
              register={register("headcount", { valueAsNumber: true })}
            />
            <Text label={t("newLead.budget")} type="number" dir="ltr" register={register("budget", { valueAsNumber: true })} />
          </div>

          {banner ? (
            <p
              className="mt-4 flex items-center gap-2 rounded-btn px-3 py-2 text-[12.5px] font-semibold"
              style={{
                background: banner.tone === "warn" ? "var(--a-bg)" : banner.tone === "ok" ? "var(--g-bg)" : "#F3F4F9",
                color: banner.tone === "warn" ? "var(--a-ink)" : banner.tone === "ok" ? "var(--g-ink)" : "var(--muted)",
              }}
              role="status"
            >
              {banner.tone === "warn" ? <AlertCircle size={15} strokeWidth={1.8} /> : banner.tone === "ok" ? <CheckCircle2 size={15} strokeWidth={1.8} /> : <Search size={15} strokeWidth={1.8} />}
              {banner.text}
              {dup?.leadId ? (
                <button type="button" className="underline" onClick={() => navigate(`/leads/${dup.leadId}`)}>
                  {t("common.open")}
                </button>
              ) : null}
            </p>
          ) : null}

          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Pick label={t("newLead.coverage")} loading={types.isPending} options={types.data ?? []} register={register("insurance_type_id")} />
            <Pick label={t("newLead.currentInsurer")} loading={insurers.isPending} options={insurers.data ?? []} register={register("current_insurer_id")} />
            <div>
              <label className="label" htmlFor="lead-source">
                {t("newLead.source")}
              </label>
              <select id="lead-source" className="field" {...register("source")}>
                <option value="">{t("common.none")}</option>
                {SOURCES.map((source) => (
                  <option key={source} value={source}>
                    {source}
                  </option>
                ))}
              </select>
            </div>
            <Text label={t("newLead.expiry")} type="date" dir="ltr" register={register("current_policy_expiry")} />
            <Text label={t("newLead.followup")} type="datetime-local" dir="ltr" error={errors.next_followup_at ? t("newLead.required") : undefined} register={register("next_followup_at")} />
            {isManager ? (
              <Pick
                label={t("newLead.rep")}
                loading={reps.isPending}
                options={(reps.data ?? []).map((rep) => ({ id: rep.rep_id, name: rep.name }))}
                register={register("assigned_to")}
              />
            ) : null}
          </div>

          <label className="label mt-4" htmlFor="lead-needs">
            {t("newLead.needs")}
          </label>
          <textarea id="lead-needs" className="field min-h-[92px]" {...register("needs_text")} />
        </Card>

        <div className="mt-5 flex items-center justify-end gap-3">
          {dup?.leadId ? (
            <label className="flex items-center gap-2 text-[12.5px] text-muted">
              <input type="checkbox" checked={allowDuplicate} onChange={(event) => setAllowDuplicate(event.target.checked)} />
              {t("newLead.duplicate")}
            </label>
          ) : null}
          <Button onClick={() => navigate("/leads")}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" busy={create.isPending}>
            {t("newLead.save")}
          </Button>
        </div>
      </form>
    </>
  );
}

function normalize(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.startsWith("+20")) return digits;
  if (digits.startsWith("0020")) return digits.slice(2);
  if (digits.startsWith("02")) return `+${digits.slice(1)}`;
  if (digits.startsWith("20")) return `+${digits}`;
  return digits.startsWith("+") ? digits : `+${digits}`;
}

function defaultFollowup(): string {
  const date = new Date(Date.now() + 3 * 86_400_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(9)}:00`;
}

type InputProps = UseFormRegisterReturn;

function Text({
  label,
  register,
  error,
  type = "text",
  dir,
  placeholder,
}: {
  label: string;
  register: InputProps;
  error?: string;
  type?: string;
  dir?: "ltr" | "rtl";
  placeholder?: string;
}) {
  return (
    <div>
      <label className="label" htmlFor={register.name}>
        {label}
      </label>
      <input
        id={register.name}
        className="field"
        type={type}
        dir={dir}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        {...register}
      />
      {error ? <p className="mt-1 text-[12px] font-semibold text-[var(--r-ink)]">{error}</p> : null}
    </div>
  );
}

function Pick({
  label,
  register,
  options,
  loading,
}: {
  label: string;
  register: InputProps;
  options: { id: string; name: string }[];
  loading: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <label className="label" htmlFor={register.name}>
        {label}
      </label>
      <select id={register.name} className="field" {...register}>
        <option value="">{loading ? t("common.loading") : t("common.none")}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
    </div>
  );
}
