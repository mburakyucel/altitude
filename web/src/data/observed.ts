/**
 * How old an observation is, in the words a person uses, plus the product names for engines and
 * models. Every quota and session figure in the app carries the time it was observed; the Monitor
 * and task pages read that through here so "as of", "stale" and "Opus 5" mean one thing everywhere.
 */

/** A session snapshot older than this, while its worker is live, is stale. */
export const SESSION_STALE_MS = 5 * 60_000;

/** Milliseconds for an instant stamped as epoch seconds (statusline, quota) or as an ISO string. */
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

/** The exact time for a hover title, in the browser's locale; empty when there is none. */
export function exactTime(value: unknown): string {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleString();
}

/** A recorded time as the activity views show it (SPEC.md §3.10): "14:05", dated when not today. */
export function stampText(value: unknown): string {
  const then = when(value);
  if (then == null) return "";
  const date = new Date(then);
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === new Date().toDateString()) return time;
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}
