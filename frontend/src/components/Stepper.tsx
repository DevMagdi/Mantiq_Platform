/** 34px circles, 6px gap, 2px connectors; current node gets a 5px primary halo (spec §9.9). */
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";

export type StepState = "done" | "current" | "todo";

const STEPS = [
  { key: "new", labelKey: "leadStatus.new" },
  { key: "contacted", labelKey: "leadStatus.contacted" },
  { key: "meeting", labelKey: "leads.colMeeting" },
  { key: "quote", labelKey: "leads.colQuote" },
  { key: "won", labelKey: "leads.colWon" },
] as const;

export const STATUS_TO_STEP: Record<string, number> = {
  new: 0,
  contacted: 1,
  interested: 1,
  meeting_scheduled: 2,
  meeting_done: 2,
  quote_sent: 3,
  negotiation: 3,
  won: 4,
  lost: 0,
  postponed: 1,
};

export function Stepper({ status }: { status: string }) {
  const { t } = useTranslation();
  const reached = STATUS_TO_STEP[status] ?? 0;
  return (
    <ol className="flex items-start gap-[6px]" aria-label={t("leads.leadTitle")}>
      {STEPS.map((step, index) => {
        const state: StepState = index < reached ? "done" : index === reached ? "current" : "todo";
        return (
          <li key={step.key} className="flex flex-1 flex-col items-center gap-1.5">
            <span className="flex w-full items-center gap-1.5">
              <span
                className="h-[2px] flex-1 rounded-full"
                style={{ background: index === 0 ? "transparent" : "var(--line-2)" }}
              />
              <span
                className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full text-[12px] font-bold"
                style={
                  state === "done"
                    ? { background: "var(--g-ink)", color: "#fff" }
                    : state === "current"
                      ? { background: "var(--primary)", color: "#fff", boxShadow: "0 0 0 5px var(--primary-soft)" }
                      : { background: "#fff", color: "var(--muted)", border: "1.5px solid var(--line-2)" }
                }
                aria-current={state === "current" ? "step" : undefined}
              >
                {state === "done" ? <Check size={16} strokeWidth={2.4} /> : index + 1}
              </span>
              <span
                className="h-[2px] flex-1 rounded-full"
                style={{ background: index === STEPS.length - 1 ? "transparent" : "var(--line-2)" }}
              />
            </span>
            <span className={`text-[12px] font-semibold ${state === "todo" ? "text-muted" : "text-ink"}`}>
              {t(step.labelKey)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
