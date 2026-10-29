/** Screen 16: split login (380px form panel, dark brand panel with the 40px headline). */
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { Check, Loader2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { z } from "zod";

import { landingPath } from "@/app/landing";
import { MantiqLockup } from "@/components/BrandMark";
import { ApiError_ } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

const schema = z.object({
  email: z.string().min(1, "required"),
  password: z.string().min(1, "required"),
  remember: z.boolean().optional(),
});

type FormValues = z.infer<typeof schema>;

export default function LoginPage() {
  const { t } = useTranslation();
  const { user, ready, login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [needsSecondFactor, setNeedsSecondFactor] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "", remember: true },
  });

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      login({ email: values.email, password: values.password, remember_me: Boolean(values.remember) }),
    onSuccess: (next) => {
      setError(null);
      navigate(landingPath(next?.role, next?.permissions), { replace: true });
    },
    onError: (cause: unknown) => {
      if (cause instanceof ApiError_ && cause.code === "mfa_required") {
        setNeedsSecondFactor(true);
        return;
      }
      // A refused login has exactly one meaning, and the card already has a word for it. The raw envelope is
      // English on an Arabic screen; anything else (rate limit, no network) is a different fact and is shown
      // as the API told it, because inventing «wrong password» for a 429 would be a lie.
      if (cause instanceof ApiError_ && cause.status === 401) {
        setError(t("auth.invalid"));
        return;
      }
      setError(cause instanceof Error ? cause.message : t("auth.invalid"));
    },
  });

  if (ready && user) return <Navigate to={landingPath(user.role, user.permissions)} replace />;

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[minmax(0,1fr)_460px]">
      <BrandPanel />
      <div className="flex flex-col items-center justify-center gap-5 bg-bg px-6 py-10">
        {/* below `lg` the navy brand panel is not rendered (it is the desktop half of the split), so the
            mark has to appear on the white side or the phone has no logo at all */}
        <div className="w-full max-w-[380px] lg:hidden">
          <MantiqLockup wordmark={t("common.appName")} legal={t("common.legalName")} size={30} tone="light" />
        </div>
        <form
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          className="card w-full max-w-[380px]"
          noValidate
        >
          <h1 className="text-[22px] font-bold text-ink">{t("auth.welcome")}</h1>
          <p className="mt-1 text-sub text-muted">{t("auth.subtitle")}</p>

          <label className="label mt-[22px]" htmlFor="email">
            {t("auth.identifier")}
          </label>
          {/* one field, two handles: staff sign in with their e-mail, a client owner with the username on
              their onboarding note (ADR-022). The API resolves either against the same column. */}
          <input
            id="email"
            className="field"
            autoComplete="username"
            dir="ltr"
            placeholder="sara.elsayed@mantiq.test"
            {...form.register("email")}
          />
          {form.formState.errors.email ? (
            <p className="mt-1 text-[12px] text-[var(--r-ink)]">{t("auth.identifier")} *</p>
          ) : null}

          <label className="label mt-4" htmlFor="password">
            {t("auth.password")}
          </label>
          <input
            id="password"
            className="field"
            type="password"
            autoComplete="current-password"
            dir="ltr"
            {...form.register("password")}
          />
          {form.formState.errors.password ? (
            <p className="mt-1 text-[12px] text-[var(--r-ink)]">{t("auth.password")} *</p>
          ) : null}

          {needsSecondFactor ? (
            <label className="mt-4 block">
              <span className="label flex items-center gap-1.5">
                <ShieldCheck size={16} strokeWidth={1.8} className="text-accent-ink" />
                {t("auth.twofa")}
              </span>
              <input className="field font-mono tracking-[6px]" inputMode="numeric" maxLength={6} dir="ltr" />
            </label>
          ) : null}

          <div className="mt-4 flex items-center justify-between">
            <label className="flex items-center gap-2 text-[13px] text-muted">
              <input type="checkbox" className="h-4 w-4 accent-[var(--color-accent)]" {...form.register("remember")} />
              {t("auth.remember")}
            </label>
            <button type="button" className="text-[13px] font-semibold text-accent-ink hover:underline">
              {t("auth.forgot")}
            </button>
          </div>

          {error ? (
            <div
              role="alert"
              className="mt-4 rounded-btn px-3 py-2 text-[13px] font-semibold"
              style={{ background: "var(--r-bg)", color: "var(--r-ink)" }}
            >
              {error}
            </div>
          ) : null}

          <button type="submit" className="btn-primary mt-5 w-full" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 size={18} className="animate-spin" /> : null}
            {mutation.isPending ? t("auth.signingIn") : t("auth.submit")}
          </button>

          <DemoAccounts onSelect={(email) => void form.setValue("email", email)} />
        </form>
      </div>
    </div>
  );
}

function BrandPanel() {
  const { t } = useTranslation();
  return (
    <div className="relative hidden flex-col justify-between bg-sidebar px-[54px] py-[46px] lg:flex">
      <div>
        {/* R13 (ADR-034): mark + wordmark, identical to the sidebar lockup — the old letter tile is gone */}
        <MantiqLockup wordmark={t("common.appName")} legal={t("common.legalName")} size={36} tone="dark" />
        <h2 className="mt-[46px] max-w-[520px] text-[40px] font-bold leading-[52px] text-sidebar-text">
          {t("auth.heroTitle1")}
          <span className="block">{t("auth.heroTitle2")}</span>
        </h2>
        <ul className="mt-6 space-y-2.5">
          {[t("auth.heroLine1"), t("auth.heroLine2"), t("auth.heroLine3")].map((line) => (
            <li key={line} className="flex items-start gap-2 text-[14px] text-[var(--color-sidebar-text)]">
              <Check size={18} strokeWidth={2.2} className="mt-[2px] shrink-0" style={{ color: "var(--color-accent)" }} />
              {line}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex items-center gap-3 rounded-card bg-white/[.06] p-4">
        <span className="grid h-10 w-10 place-items-center rounded-full" style={{ background: "var(--color-sidebar-active-bg)" }}>
          <Check size={20} strokeWidth={2.2} style={{ color: "var(--color-accent)" }} />
        </span>
        <div>
          <div className="text-[14px] font-semibold text-sidebar-text">{t("auth.heroSla")}</div>
          <div className="text-[12px] text-[var(--color-sidebar-text)]">{t("auth.heroSlaBody")}</div>
        </div>
      </div>
    </div>
  );
}

function DemoAccounts({ onSelect }: { onSelect: (email: string) => void }) {
  const accounts = [
    { email: "sara.elsayed@mantiq.test", label: "سارة — بياع" },
    { email: "manager@mantiq.test", label: "مدير المبيعات" },
    { email: "cs@mantiq.test", label: "خدمة العملاء" },
    { email: "owner.mantiq@company.test", label: "صاحب شركة — بوابة العميل" },
  ];
  return (
    <div className="mt-6 border-t border-line pt-3">
      <div className="mb-1.5 text-[12px] font-semibold text-muted">حسابات التجربة (كلمة المرور: Mantiq!2026)</div>
      <div className="flex flex-wrap gap-1.5">
        {accounts.map((account) => (
          <button
            key={account.email}
            type="button"
            className="rounded-pill bg-accent-muted px-2.5 py-1 text-[11px] font-semibold text-muted transition hover:bg-accent-muted hover:text-accent-ink"
            onClick={() => onSelect(account.email)}
          >
            {account.label}
          </button>
        ))}
      </div>
    </div>
  );
}


