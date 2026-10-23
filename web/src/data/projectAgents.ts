import type { AgentClock, ProjectAgentDefinition, ProjectAgentRun } from "./api";
import { agoText, clockText, dayText, stampText } from "./observed";

/*
 * The words the routine pages add to what the server writes (design/wireframes/project-agents/PROPOSAL.md): run times,
 * minutes, tokens and the machine's zone after a schedule's time when the browser's clock differs.
 */

/** "30 min", "1 h", "1 h 10 min". */
export function durationText(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [hours ? `${hours} h` : "", rest || !hours ? `${rest} min` : ""].filter(Boolean).join(" ");
}

/** Schedule times are the machine's: "07:00 Istanbul" when this browser's UTC offset differs from its offset now. */
export function zoned(text: string, clock: AgentClock | undefined, offset = -new Date().getTimezoneOffset()): string {
  if (!clock || clock.offset === offset) return text;
  return text.replace(/\b(\d{2}:\d{2})\b/g, `$1 ${clock.zone}`);
}

/** "2 h ago", "5 min ago", "yesterday". */
export function newsAgo(at: string | null): string {
  return agoText(at).replace(/^(\d+)([hd]) ago$/, "$1 $2 ago");
}

/** "Today 07:00", "Yesterday 07:00", "Oct 16, 11:20". */
export function runTime(at: string | null | undefined): string {
  const day = dayText(at);
  return day === "Today" || day === "Yesterday" ? `${day} ${clockText(at)}` : stampText(at);
}

/** When a run was due, and when it ran if that was late: "Oct 13, 07:00, ran at 09:14". */
export function runWhen(run: Pick<ProjectAgentRun, "at" | "started" | "late">): string {
  return run.late && run.started ? `${runTime(run.at)}, ran at ${clockText(run.started)}` : runTime(run.at);
}

/** "4.1M", "820k", "950". */
export function tokensText(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}k`;
  return String(tokens);
}

/** "6 min · 4.1M tokens", empty before a run has used anything. */
export function runCost(run: Pick<ProjectAgentRun, "minutes" | "tokens">): string {
  return [run.minutes != null ? `${run.minutes} min` : "", run.tokens != null ? `${tokensText(run.tokens)} tokens` : ""].filter(Boolean).join(" · ");
}

/** A refusal under its verb ("Not saved: …"), unless the server's sentence already says so. */
export function refusal(verb: string, message: string): string {
  return /^Not [\w ]+:/.test(message) ? message : `${verb}: ${message}`;
}

export const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const LIMITS = [{ minutes: 10, label: "10 min" }, { minutes: 20, label: "20 min" }, { minutes: 60, label: "1 hour" }];
export const TELL = [{ value: "found", label: "When it finds something" }, { value: "every", label: "Every run" }, { value: "never", label: "Never" }] as const;

/** A new routine: weekdays at 07:00, telling you when it finds something, on the default model, stopping after 20 min. */
export const NEW_SCHEDULE = { days: [0, 1, 2, 3, 4], time: "07:00" };
export const NEW_DEFINITION: ProjectAgentDefinition = { name: "", brief: "", schedules: [NEW_SCHEDULE], events: false, tell: "found", model: null, limit: 20 };
