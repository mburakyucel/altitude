/**
 * How old an observation is, in the words a person uses, plus the product names for engines and
 * models. Every quota and session figure in the app carries the time it was observed; the Monitor
 * and task pages read that through here so "as of", "stale" and "Opus 5" mean one thing everywhere.
 */

/** A session snapshot older than this, while its worker is live, is stale. */
export const SESSION_STALE_MS = 5 * 60_000;

/** Milliseconds for an instant stamped as epoch seconds (quota) or as an ISO string. */
export function when(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value * 1000;
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return null;
}

/** "<1m", "5m", "3h", "2d" — empty when the timestamp is missing, unparseable, or in the future. */
export function age(value: unknown): string {
  const then = when(value);
  if (then == null) return "";
  const seconds = (Date.now() - then) / 1000;
  if (seconds < 0) return "";
  if (seconds < 60) return "<1m";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}

export function asOf(value: unknown): string {
  const text = age(value);
  return text ? `as of ${text} ago` : "";
}

export function older(value: unknown, limit: number): boolean {
  const then = when(value);
  return then != null && Date.now() - then > limit;
}

export function engineName(engine: unknown): string {
  const value = typeof engine === "string" ? engine : "";
  return value === "claude" ? "Claude" : value === "codex" ? "Codex" : value;
}

/** Product names, the way the model reports itself: "opus" and "claude-opus-5" are both "Opus 5". */
const MODEL_NAMES: Record<string, string> = {
  opus: "Opus 5",
  sonnet: "Sonnet 5",
  haiku: "Haiku 4.5",
  fable: "Fable 5.1",
};

export function modelName(model: string): string {
  const family = model.replace(/^claude-/, "").replace(/-\d.*$/, "");
  return MODEL_NAMES[model] ?? MODEL_NAMES[family] ?? model;
}

/** The reserve line every quota meter draws: past this share of the window the meter turns danger. */
export const RESERVE_PERCENT = 70;

/** An age the way the shell says it (SPEC.md §4.5): "just now", "5 min ago", "2h", "yesterday", "3d". */
export function ageText(value: unknown): string {
  const then = when(value);
  if (then == null) return "";
  const seconds = (Date.now() - then) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  if (seconds < 2 * 86_400) return "yesterday";
  return `${Math.floor(seconds / 86_400)}d`;
}

/** ageText phrased after a verb ("L3 answered 2h ago"): hours and days gain "ago". */
export function agoText(value: unknown): string {
  const age = ageText(value);
  return /^\d+[hd]$/.test(age) ? `${age} ago` : age;
}

/**
 * Every time a person reads (SPEC.md §4.5) is shown in the reader's own zone and locale through the
 * functions below; records and JSON keep ISO 8601 with an offset, and no screen shows one.
 */

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** "16:58": a local clock time. */
export function clockText(value: unknown): string {
  const then = when(value);
  return then == null ? "" : clock(then);
}

/** "Sep 24, 2036": a local date, for an instant that is about a day rather than a moment. */
export function dateText(value: unknown): string {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

/** A day divider: "Today", "Yesterday", "Thu, Oct 8". */
export function dayText(value: unknown): string {
  const then = when(value);
  if (then == null) return "";
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).valueOf();
  const diff = Math.round((startOf(new Date()) - startOf(new Date(then))) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return new Date(then).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/** The exact time for a hover title, "Oct 10, 2026, 4:58:42 PM PDT"; empty when there is none. */
export function exactTime(value: unknown): string {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleString([], { dateStyle: "medium", timeStyle: "long" });
}

/** A recorded time as the activity views show it (SPEC.md §3.10): "14:05", dated when not today, with the year when not this year. */
export function stampText(value: unknown): string {
  const then = when(value);
  if (then == null) return "";
  const date = new Date(then);
  const time = clock(then);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return time;
  const year = date.getFullYear() === today.getFullYear() ? undefined : "numeric";
  return `${date.toLocaleDateString([], { month: "short", day: "numeric", year })}, ${time}`;
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const CLOCK = String.raw`(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?`;
const UTC = String.raw`\s?(?:Z|UTC|GMT)`;
/**
 * A written UTC time in prose: an ISO 8601 instant with Z or an offset; a date, clock and UTC; a month
 * day, clock and UTC; a UTC clock or clock range. A clock glued to a word ("at04:10Z") still counts;
 * one inside a longer number (a fingerprint, host:port, file:12:34) or without a zone does not.
 */
const TIME = new RegExp(
  String.raw`(?<![\d:.])(?:(\d{4})-(\d{2})-(\d{2})[T ]${CLOCK}(?:(Z|[+-]\d{2}:?\d{2})(?:\s?UTC)?|${UTC})` +
  String.raw`|\b(${MONTHS.join("|")})[a-z]*\.? (\d{1,2}),? ${CLOCK}${UTC}` +
  String.raw`|${CLOCK}(?:(\s?[–-]\s?)${CLOCK})?${UTC})(?![\w:]|[+-]\d)`,
  "gi",
);

/** A written time and its instants as ISO strings; `dated` when the writer gave a date, `until` for a range's end. */
export type TimeToken = { index: number; text: string; at: string; dated: boolean; separator?: string; until?: string };

const iso = (ms: number) => new Date(ms).toISOString();
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** The UTC clock's instant on the day that puts it last at or before `anchor` plus three hours. */
function onDay(anchor: number, hour: string, minute: string, second = "0"): number {
  const day = new Date(anchor);
  let at = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), +hour, +minute, +second);
  while (at > anchor + 3 * HOUR) at -= DAY;
  while (at + DAY <= anchor + 3 * HOUR) at += DAY;
  return at;
}

/**
 * Written UTC times in `text`, each with its instant. A clock without a date takes its day from
 * `anchor` (the text's own recorded time, else now), a month day its year; out-of-range parts are
 * left as text.
 */
export function timeTokens(text: string, anchor = Date.now()): TimeToken[] {
  const tokens: TimeToken[] = [];
  for (const m of text.matchAll(TIME)) {
    const [whole] = m;
    const index = m.index;
    let at: number | null = null;
    let dated = true;
    if (m[1]) {
      const offset = m[7] && m[7].toUpperCase() !== "Z" ? m[7].replace(/^([+-]\d{2}):?(\d{2})$/, "$1:$2") : "Z";
      at = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]!.padStart(2, "0")}:${m[5]}:${m[6] ?? "00"}${offset}`);
    } else if (m[8]) {
      const month = MONTHS.indexOf(m[8].slice(0, 3).toLowerCase());
      const year = new Date(anchor).getUTCFullYear();
      const near = [year - 1, year, year + 1].map((y) => Date.UTC(y, month, +m[9]!, +m[10]!, +m[11]!, +(m[12] ?? 0)));
      at = near.reduce((best, value) => Math.abs(value - anchor) < Math.abs(best - anchor) ? value : best);
    } else if (m[16]) {
      const from = onDay(anchor, m[13]!, m[14]!, m[15]);
      const to = onDay(anchor, m[17]!, m[18]!, m[19]);
      if ([m[13], m[17]].every((h) => +h! < 24) && [m[14], m[18]].every((n) => +n! < 60)) {
        tokens.push({ index, text: whole, at: iso(from), dated: false, separator: m[16], until: iso(to) });
      }
      continue;
    } else {
      if (+m[13]! > 23 || +m[14]! > 59) continue;
      at = onDay(anchor, m[13]!, m[14]!, m[15]);
      dated = false;
    }
    if (at != null && !Number.isNaN(at)) tokens.push({ index, text: whole, at: iso(at), dated });
  }
  return tokens;
}
