import { useId } from "react";

/**
 * The Mantiq mark — an abstract «M» whose vertices are network nodes, drawn with the official gold
 * diagonal gradient (Gold Light → Mantiq Gold → Gold Dark). One geometry, three renderings:
 * this inline SVG (sidebar, login, portal), `public/brand/mantiq-mark.svg` (crisp at any size) and the
 * PNG icon set produced by `scripts/generate-brand-icons.py` — all of them use the path/nodes below.
 *
 * Gradient stops are CSS variables so the mark follows the token file like every other surface
 * (ADR-034: one source of truth). The fallbacks are the canonical hex, used only if the stylesheet is
 * absent — e.g. an export renderer or an e-mail template.
 */
export const MARK_PATH = "M14 49V18.5l18 20.5 18-20.5V49";
export const MARK_NODES = [
  { cx: 14, cy: 18.5, r: 6 },
  { cx: 32, cy: 39, r: 5.8 },
  { cx: 50, cy: 18.5, r: 6 },
  { cx: 14, cy: 49, r: 4.8 },
  { cx: 50, cy: 49, r: 4.8 },
] as const;

export function MantiqMark({ size = 32, title }: { size?: number; title?: string }) {
  const raw = useId();
  const id = `mantiq-mark-${raw.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
      data-brand-mark="mantiq"
    >
      <defs>
        <linearGradient id={id} x1="10" y1="10" x2="54" y2="54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--color-gold-light, #E3D2A8)" />
          <stop offset="0.52" stopColor="var(--color-gold, #BDA77E)" />
          <stop offset="1" stopColor="var(--color-gold-dark, #8F7A52)" />
        </linearGradient>
      </defs>
      <path d={MARK_PATH} stroke={`url(#${id})`} strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" />
      {MARK_NODES.map((node) => (
        <circle key={`${node.cx}-${node.cy}`} cx={node.cx} cy={node.cy} r={node.r} fill={`url(#${id})`} />
      ))}
    </svg>
  );
}

/**
 * Mark + wordmark. `tone="dark"` sits on navy chrome (sidebar, login panel, portal banner) and uses
 * Text-on-dark; `tone="light"` sits on white and uses Mantiq Navy. The wordmark string is passed in by
 * the caller from `t("common.appName")`, so it is «منطق» in Arabic and “Mantiq” in English — never a
 * hardcoded product name and never a stale one.
 */
export function MantiqLockup({
  wordmark,
  size = 34,
  tone = "dark",
  legal,
  className = "",
}: {
  wordmark: string;
  size?: number;
  tone?: "dark" | "light";
  /** the legal line under the wordmark (Mantiq Technologies) — sidebar and login only */
  legal?: string;
  className?: string;
}) {
  const wordColor = tone === "dark" ? "var(--color-sidebar-text)" : "var(--color-brand)";
  const legalColor = tone === "dark" ? "var(--color-sidebar-muted)" : "var(--color-muted)";
  return (
    <span className={`flex min-w-0 items-center gap-2.5 ${className}`.trim()} data-brand-lockup={tone}>
      <MantiqMark size={size} />
      <span className="min-w-0">
        <span className="block truncate text-[15px] font-bold leading-tight" style={{ color: wordColor }} data-brand-wordmark>
          {wordmark}
        </span>
        {legal ? (
          <span
            className="block truncate text-[10px] font-semibold uppercase leading-tight"
            style={{ color: legalColor, letterSpacing: "0.12em" }}
          >
            {legal}
          </span>
        ) : null}
      </span>
    </span>
  );
}
