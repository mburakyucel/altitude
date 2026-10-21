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

/** "14:05" on the local day of `day`, else "Sep 16, 14:05", with the year when not that day's year. */
function stamp(ms: number, day: number): string {
  const date = new Date(ms);
  const on = new Date(day);
  if (date.toDateString() === on.toDateString()) return clock(ms);
  const year = date.getFullYear() === on.getFullYear() ? undefined : "numeric";
  return `${date.toLocaleDateString([], { month: "short", day: "numeric", year })}, ${clock(ms)}`;
}

/** A recorded time as the activity views show it (SPEC.md §3.10): "14:05", dated when not today, with the year when not this year. */
export function stampText(value: unknown): string {
  const then = when(value);
  return then == null ? "" : stamp(then, Date.now());
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const CLOCK = String.raw`(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?`;
const UTC = String.raw`\s?(?:Z|UTC|GMT)`;
/**
 * A written UTC time in prose: an ISO 8601 instant with Z or an offset; a date, clock and UTC; a month
 * day, clock and UTC; a UTC clock, or two joined by "–", "-" or "/". A clock glued to a word ("at04:10Z")
 * still counts. A clock inside a longer number (a fingerprint, host:port, file:12:34) or a path, without a zone,
 * or ending a longer list of clocks that share the zone ("18:43, 19:05 UTC") does not, so a phrase is
 * never shown half converted.
 */
const TIME = new RegExp(
  String.raw`(?<![\d:./\\])(?:(\d{4})-(\d{2})-(\d{2})[T ]${CLOCK}(?:(Z|[+-]\d{2}:?\d{2})(?:\s?UTC)?|${UTC})` +
  String.raw`|\b(${MONTHS.join("|")})[a-z]*\.? (\d{1,2}),? ${CLOCK}${UTC}` +
  String.raw`|(?<!\dT)${CLOCK}(?:(\s?[–\-/]\s?)${CLOCK})?${UTC})(?![\w:]|[+-]\d|\.\w)`,
  "gi",
);

/** A bare clock and a list separator just before a UTC clock: the zone belongs to the whole list. */
const LISTED = /(?:^|[^\d:.+\-])\d{1,2}:\d{2}(?::\d{2})?(?:\s*,\s*|\s*[–\-/]\s*|\s+(?:and|or|to)\s+)$/;

/**
 * A written time: where it is, its `<time datetime>` value, and how it reads in the reader's zone. A clock
 * written without a date stays a clock (its value is the local "HH:MM"), so no screen states a day the
 * writer did not.
 */
export type TimeToken = { index: number; text: string; at: string; shown: string; exact: string };

const DAY = 86_400_000;
const zoned = (ms: number) => new Date(ms).toLocaleTimeString([], { timeStyle: "long" });
const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

/**
 * Written UTC times in `text` (SPEC.md §4.5). `anchor` is the text's own recorded time, else now. A dated
 * time reads as a clock on the anchor's local day and dated otherwise; a month day takes the year nearest
 * the anchor. A clock without a date converts at its occurrence nearest the anchor (which decides only the
 * offset across daylight saving) and stays undated; a range ends at its first end clock after its start.
 */
export function timeTokens(text: string, anchor = Date.now()): TimeToken[] {
  const tokens: TimeToken[] = [];
  const valid = (hour?: string, minute?: string) => +hour! < 24 && +minute! < 60;
  const nearest = (values: number[]) => values.reduce((best, value) => Math.abs(value - anchor) < Math.abs(best - anchor) ? value : best);
  for (const m of text.matchAll(TIME)) {
    if (m[1] || m[8]) {
      let at: number;
      if (m[1]) {
        const offset = m[7] && m[7].toUpperCase() !== "Z" ? m[7].replace(/^([+-]\d{2}):?(\d{2})$/, "$1:$2") : "Z";
        at = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]!.padStart(2, "0")}:${m[5]}:${m[6] ?? "00"}${/:\d{2}:\d{2}(\.\d+)/.exec(m[0])?.[1] ?? ""}${offset}`);
      } else {
        if (!valid(m[10], m[11])) continue;
        const month = MONTHS.indexOf(m[8]!.slice(0, 3).toLowerCase());
        const year = new Date(anchor).getUTCFullYear();
        at = nearest([year - 1, year, year + 1].map((y) => Date.UTC(y, month, +m[9]!, +m[10]!, +m[11]!, +(m[12] ?? 0))));
      }
      if (Number.isNaN(at)) continue;
      tokens.push({ index: m.index, text: m[0], at: new Date(at).toISOString(), shown: stamp(at, anchor), exact: exactTime(at / 1000) });
      continue;
    }
    if (!valid(m[13], m[14]) || (m[16] && !valid(m[17], m[18])) || LISTED.test(text.slice(Math.max(0, m.index - 24), m.index))) continue;
    const day = new Date(anchor);
    const at = nearest([-DAY, 0, DAY].map((shift) => shift + Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), +m[13]!, +m[14]!, +(m[15] ?? 0))));
    const until = m[16] ? at + ((Date.UTC(1970, 0, 1, +m[17]!, +m[18]!, +(m[19] ?? 0)) - at) % DAY + DAY) % DAY : null;
    tokens.push({
      index: m.index,
      text: m[0],
      at: hhmm(at),
      shown: until == null ? clock(at) : `${clock(at)}${m[16]}${clock(until)}`,
      exact: until == null ? zoned(at) : `${zoned(at)} – ${zoned(until)}`,
    });
  }
  return tokens;
}

/** Plain text with its written UTC times in the reader's zone, for a line that shows a string rather than prose. */
export function localTimes(text: string, anchor?: number): string {
  let shown = "";
  let end = 0;
  for (const token of timeTokens(text, anchor)) {
    shown += text.slice(end, token.index) + token.shown;
    end = token.index + token.text.length;
  }
  return shown + text.slice(end);
}
