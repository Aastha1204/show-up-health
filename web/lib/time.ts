/** Everything in the UI is shown and reasoned about in Asia/Kolkata. India has no DST, so IST = UTC+5:30 year-round. */
export const TZ = "Asia/Kolkata";
const IST_OFFSET_MIN = 330;
const WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"] as const;

/** YYYY-MM-DD of an instant, in IST. */
export const istDate = (d: Date | string = new Date()) => new Date(d).toLocaleDateString("en-CA", { timeZone: TZ });

const hm = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** Minutes since IST midnight for an instant. */
export function istMinutes(iso: string | Date): number {
  const parts = hm.formatToParts(new Date(iso));
  const h = Number(parts.find((p) => p.type === "hour")!.value);
  const m = Number(parts.find((p) => p.type === "minute")!.value);
  return h * 60 + m;
}

export const minLabel = (min: number) => {
  const h = Math.floor(min / 60), m = min % 60;
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};
export const fmtTime = (iso: string | Date) => minLabel(istMinutes(iso));

export function parseDate(dateStr: string) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return { y, m, d };
}
export function addDays(dateStr: string, n: number): string {
  const { y, m, d } = parseDate(dateStr);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
export const weekdayKey = (dateStr: string) => WEEKDAYS[new Date(`${dateStr}T00:00:00Z`).getUTCDay()];

/** "Mon 9 Nov" */
export const fmtDay = (dateStr: string) =>
  new Date(`${dateStr}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" })
    .replace("Sept", "Sep");     // newer ICU says "Sept"; the WhatsApp messages (and everything else) say "Sep"
export const fmtDayLong = (dateStr: string) =>
  new Date(`${dateStr}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric" });
export const fmtStamp = (iso: string) => `${fmtDay(istDate(iso))}, ${fmtTime(iso)}`;

/** UTC ISO instant for an IST wall-clock date + minutes-since-midnight. */
export function isoFromIst(dateStr: string, minutes: number): string {
  const { y, m, d } = parseDate(dateStr);
  return new Date(Date.UTC(y, m - 1, d) + (minutes - IST_OFFSET_MIN) * 60_000).toISOString();
}

export const hhmmToMin = (s: string) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };

export function relTime(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return fmtStamp(iso);
}
