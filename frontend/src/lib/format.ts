/** Egyptian-locale formatting (spec §11) — one implementation, used by every screen. */

const LOCALE = "ar-EG-u-nu-latn"; // Arabic UI with Western digits: what the mockups use

export function formatNumber(value: number | null | undefined, opts: Intl.NumberFormatOptions = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(LOCALE, opts).format(value);
}

export function formatMoney(value: number | string | null | undefined, currency = "EGP"): string {
  const numeric = typeof value === "string" ? Number(value) : value;
  if (numeric === null || numeric === undefined || Number.isNaN(numeric)) return "—";
  return `${formatNumber(numeric, { maximumFractionDigits: 0 })} ${currency === "EGP" ? "ج.م" : currency}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${formatNumber(value, { maximumFractionDigits: 1 })}٪`;
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(value: string | Date | null | undefined): string {
  const date = toDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "long", year: "numeric" }).format(date);
}

export function formatDateTime(value: string | Date | null | undefined): string {
  const date = toDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

/** "منذ 3 س" style relative label used in the inbox list and timelines. */
export function relativeTime(value: string | Date | null | undefined, now: Date = new Date()): string {
  const date = toDate(value);
  if (!date) return "—";
  const minutes = Math.round((now.getTime() - date.getTime()) / 60000);
  if (minutes < 1) return "الآن";
  if (minutes < 60) return `منذ ${formatNumber(minutes)} د`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `منذ ${formatNumber(hours)} س`;
  const days = Math.round(hours / 24);
  if (days < 30) return `منذ ${formatNumber(days)} يوم`;
  return formatDate(date);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "؟";
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
}

export function daysUntil(value: string | Date | null | undefined, now: Date = new Date()): number | null {
  const date = toDate(value);
  if (!date) return null;
  return Math.ceil((date.getTime() - now.getTime()) / 86_400_000);
}

/**
 * The design masks numbers inside tables (01xxxxxxxx1) while keeping enough to disambiguate.
 * Input is E.164; output keeps the local prefix plus the last `keepLast` digits.
 */
export function maskMobile(e164: string | null | undefined, keepLast = 0): string {
  if (!e164) return "—";
  const local = e164.startsWith("+20") ? `0${e164.slice(3)}` : e164.replace(/\D/g, "");
  if (local.length < 4) return local;
  const tail = keepLast > 0 ? local.slice(-keepLast) : "";
  const head = local.slice(0, 2);
  return `${head}${"x".repeat(Math.max(0, local.length - head.length - tail.length))}${tail}`;
}

/** "ساعتان و10 دقائق" / "2 h 10 min" — the SLA chip in the inbox reads as prose, not numbers. */
export function humanDuration(totalMinutes: number, lang: string): string {
  const sign = totalMinutes < 0 ? -1 : 1;
  const minutes = Math.abs(Math.round(totalMinutes));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const rtl = lang.startsWith("ar");
  const hourWord = (n: number): string => {
    if (!rtl) return `${n} h`;
    if (n === 1) return "ساعة";
    if (n === 2) return "ساعتان";
    if (n >= 3 && n <= 10) return `${n} ساعات`;
    return `${n} ساعة`;
  };
  const minuteWord = (n: number): string => {
    if (!rtl) return `${n} min`;
    if (n === 1) return "دقيقة";
    if (n === 2) return "دقيقتان";
    if (n >= 3 && n <= 10) return `${n} دقائق`;
    return `${n} دقيقة`;
  };
  const parts: string[] = [];
  if (hours > 0) parts.push(hourWord(hours));
  if (rest > 0 || hours === 0) parts.push(minuteWord(rest));
  const joined = parts.join(rtl ? " و " : " ");
  return sign < 0 ? `-${joined}` : joined;
}

/** Kanban follow-up chip: "متابعة اليوم" / "متابعة غدًا" / weekday / "تأخرت المتابعة". */
export function followupLabel(iso: string | null | undefined, labels: { today: string; tomorrow: string; late: string; next: string }, now = new Date()): { text: string; tone: "g" | "a" | "r" | "b" } {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return { text: labels.next, tone: "b" };
  const day = (d: Date) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const diff = Math.round((day(date) - day(now)) / 86_400_000);
  if (diff < 0) return { text: labels.late, tone: "r" };
  if (diff === 0) return { text: labels.today, tone: "a" };
  if (diff === 1) return { text: labels.tomorrow, tone: "b" };
  const weekday = new Intl.DateTimeFormat(document.documentElement.lang || "ar-EG", { weekday: "long" }).format(date);
  const time = new Intl.DateTimeFormat(document.documentElement.lang || "ar-EG", { hour: "2-digit", minute: "2-digit" }).format(date);
  return { text: `${weekday} ${time}`, tone: "b" };
}

/**
 * The portal's response-quality label (screen 7 shows «ممتاز»). It must be derived from the measured
 * average first response, never hardcoded — a praise label that survives a 3-hour average is worse
 * than no label at all.
 */
export function responseRating(minutes: number | null | undefined): "excellent" | "good" | "slow" | null {
  if (minutes == null || !Number.isFinite(minutes)) return null;
  if (minutes <= 30) return "excellent";
  if (minutes <= 60) return "good";
  return "slow";
}
