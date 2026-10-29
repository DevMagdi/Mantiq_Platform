/** Shared primitives. Every token here is the CSS variable from the design sheet (spec §4). */
import { AlertTriangle, Inbox, Lock, Search, TriangleAlert } from "lucide-react";
import type { ButtonHTMLAttributes, CSSProperties, MouseEvent, ReactNode } from "react";

import { initials as initialsOf } from "@/lib/format";
import { t } from "@/lib/translate";

export type Tone = "g" | "a" | "r" | "b" | "t";

const TONE_STYLE: Record<Tone, CSSProperties> = {
  g: { background: "var(--g-bg)", color: "var(--g-ink)" },
  a: { background: "var(--a-bg)", color: "var(--a-ink)" },
  r: { background: "var(--r-bg)", color: "var(--r-ink)" },
  b: { background: "var(--b-bg)", color: "var(--b-ink)" },
  t: { background: "var(--t-bg)", color: "var(--t-ink)" },
};

export function Card({
  children,
  className = "",
  padded = true,
  style,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
  style?: CSSProperties;
}) {
  return (
    <section className={`${padded ? "card" : "rounded-card border border-line bg-card shadow-card"} ${className}`.trim()} style={style}>
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "secondary",
  type = "button",
  icon,
  busy,
  disabled,
  full,
  size = "md",
  title,
  ...rest
}: {
  children?: ReactNode;
  // the handler receives the event so a button inside a clickable table row can stop the row's own
  // action (opening the detail dialog) without a wrapper div swallowing the click
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  type?: "button" | "submit";
  icon?: ReactNode;
  busy?: boolean;
  disabled?: boolean;
  full?: boolean;
  size?: "sm" | "md";
  title?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "type" | "disabled" | "title" | "children">) {
  const cls =
    variant === "primary"
      ? "btn-primary"
      : variant === "danger"
        ? "btn inline-flex bg-[var(--r-bg)] text-[var(--r-ink)] hover:brightness-95"
        : variant === "ghost"
          ? "btn-ghost"
          : "btn-secondary";
  return (
    <button
      {...rest}
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || busy}
      className={`${cls} ${size === "sm" ? "!px-3 !py-1.5 !text-[13px]" : ""} ${full ? "w-full" : ""}`.trim()}
    >
      {busy ? <Spinner /> : icon}
      {children}
    </button>
  );
}

export function Spinner() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 animate-spin" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <header className="mb-[14px] flex items-center justify-between gap-4">
      <h2 className="text-card font-semibold text-ink">{children}</h2>
      {action}
    </header>
  );
}

export function Badge({ tone = "b", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-pill px-[10px] py-[3px] text-xs font-semibold ${className}`.trim()}
      style={TONE_STYLE[tone]}
    >
      {children}
    </span>
  );
}

export function Chip({ children, tone = "b" }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-pill border px-3 py-1 text-xs font-semibold"
      style={{ ...TONE_STYLE[tone], border: "none", boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${TONE_STYLE[tone].color} 18%, transparent)` }}
    >
      {children}
    </span>
  );
}

export function Avatar({
  name,
  size = 38,
  photo,
  color = "#C7D2FE",
  className = "",
}: {
  name: string | null | undefined;
  size?: number;
  photo?: string | null;
  color?: string;
  className?: string;
}) {
  const style: CSSProperties = { width: size, height: size, background: photo ? undefined : color, fontSize: size * 0.38 };
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold text-sidebar ${className}`.trim()}
      style={style}
      aria-hidden="true"
    >
      {photo ? <img src={photo} alt="" className="h-full w-full object-cover" /> : initialsOf(name)}
    </span>
  );
}

export function Kpi({
  label,
  value,
  delta,
  tone = "b",
  icon,
}: {
  label: string;
  value: ReactNode;
  delta?: ReactNode;
  tone?: Tone;
  icon?: ReactNode;
}) {
  return (
    <Card className="flex items-start justify-between gap-3">
      <div>
        <div className="text-[13px] text-muted">{label}</div>
        <div className="mt-1.5 text-kpi font-bold text-ink tabular-nums">{value}</div>
        {delta ? <div className="mt-1 text-[12px] text-muted">{delta}</div> : null}
      </div>
      <span className="grid h-9 w-9 place-items-center rounded-[10px]" style={TONE_STYLE[tone]}>
        {icon}
      </span>
    </Card>
  );
}

export function SearchInput({
  value,
  onChange,
  width = 260,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  width?: number;
  placeholder?: string;
}) {
  return (
    <div className="relative" style={{ width }}>
      <Search
        size={16}
        strokeWidth={1.8}
        className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted"
        style={{ insetInlineStart: 10 }}
      />
      <input
        className="field py-[8px] ps-[30px] text-[13px]"
        value={value}
        placeholder={placeholder ?? t("common.search")}
        onChange={(event) => onChange(event.target.value)}
        aria-label={placeholder ?? t("common.search")}
      />
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: readonly { value: T; label: ReactNode }[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="inline-flex items-center gap-1 rounded-pill bg-kanban p-1">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={`rounded-pill px-3 py-1 text-[13px] font-semibold transition ${active ? "bg-primary text-white" : "text-muted hover:text-ink"}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-3 rounded-[10px] px-2 py-1.5 text-start text-[13px] text-ink transition hover:bg-[#F7F8FC] disabled:opacity-60"
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span
        className="relative inline-block h-[22px] w-[40px] shrink-0 rounded-pill transition"
        style={{ background: checked ? "var(--primary)" : "var(--line-2)" }}
      >
        <span
          className="absolute top-[3px] block h-4 w-4 rounded-full bg-white shadow transition-all"
          style={{ insetInlineStart: checked ? 20 : 3 }}
        />
      </span>
    </button>
  );
}

export function Skeleton({ height = 16, width = "100%", className = "" }: { height?: number; width?: number | string; className?: string }) {
  return <div className={`skeleton ${className}`.trim()} style={{ height, width }} aria-hidden="true" />;
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label={t("common.loading")}>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex items-center gap-3">
          {Array.from({ length: cols }, (__, col) => (
            <Skeleton key={col} height={14} width={col === 0 ? 180 : "100%"} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function StateBlock({
  kind,
  title,
  body,
  action,
}: {
  kind: "loading" | "empty" | "error" | "forbidden" | "notfound";
  title?: string;
  body?: string;
  action?: ReactNode;
}) {
  const copy: Record<typeof kind, { title: string; body: string; icon: ReactNode }> = {
    loading: { title: t("common.loading"), body: "", icon: <Skeleton height={64} /> },
    empty: { title: title ?? t("common.emptyTitle"), body: body ?? "", icon: <Inbox size={26} strokeWidth={1.8} className="text-muted" /> },
    error: { title: title ?? t("common.errorTitle"), body: body ?? t("common.errorBody"), icon: <TriangleAlert size={26} strokeWidth={1.8} style={TONE_STYLE.r} /> },
    forbidden: {
      title: title ?? t("common.forbiddenTitle"),
      body: body ?? t("common.forbiddenBody"),
      icon: <Lock size={26} strokeWidth={1.8} style={TONE_STYLE.a} />,
    },
    notfound: { title: title ?? t("common.notFoundTitle"), body: body ?? "", icon: <AlertTriangle size={26} strokeWidth={1.8} className="text-muted" /> },
  };
  const current = copy[kind];
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-[36px] text-center">
      {current.icon}
      <div className="text-[15px] font-semibold text-ink">{current.title}</div>
      {current.body ? <p className="max-w-[420px] text-[13px] text-muted">{current.body}</p> : null}
      {action}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-[18px] flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-page font-bold text-ink">{title}</h1>
        {subtitle ? <p className="mt-1 text-sub text-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <div className="row-line px-1 py-3 text-[13px] text-muted">{children}</div>;
}

export function DataTable<T>({
  columns,
  rows,
  keyOf,
  onRowClick,
  rowTone,
  loading,
  empty,
}: {
  columns: readonly { id: string; head: ReactNode; width?: number; align?: "start" | "center" | "end"; render: (row: T) => ReactNode }[];
  rows: readonly T[];
  keyOf: (row: T) => string;
  onRowClick?: (row: T) => void;
  /** Optional background for the whole row (e.g. `--row-ok` for converted leads). */
  rowTone?: (row: T) => "ok" | "warn" | undefined;
  loading?: boolean;
  empty?: ReactNode;
}) {
  if (loading) return <TableSkeleton rows={6} cols={Math.min(columns.length, 6)} />;
  if (rows.length === 0) return <>{empty ?? <StateBlock kind="empty" />}</>;
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="table-head">
          {columns.map((column) => (
            <th
              key={column.id}
              scope="col"
              className="pb-2 text-start font-semibold"
              style={{ width: column.width, textAlign: column.align ?? "start" }}
            >
              {column.head}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={keyOf(row)}
            className="row-line transition hover:bg-[#FAFBFF]"
            style={
              rowTone
                ? {
                    background:
                      rowTone(row) === "ok" ? "var(--row-ok)" : rowTone(row) === "warn" ? "var(--row-warn)" : undefined,
                  }
                : undefined
            }
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            onKeyDown={
              onRowClick
                ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onRowClick(row);
                    }
                  }
                : undefined
            }
          >
            {columns.map((column) => (
              <td key={column.id} className="px-1 py-3 align-middle" style={{ textAlign: column.align ?? "start" }}>
                {column.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
