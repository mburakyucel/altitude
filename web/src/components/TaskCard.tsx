import { Link } from "react-router";
import { useOverview } from "../data/api";
import type { Decision, Overview, TaskRow } from "../data/api";
import { agoText, when } from "../data/observed";
import { clock } from "./Bubbles";
import type { DotState } from "../shell/projects";

/*
 * The task card (SPEC.md §3.5): state dot, title, meta line, chevron; the link opens the task page. The
 * same anatomy is the bordered card under an L3 reply and the row in the work panel's sections.
 */

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function oneSentence(text: string): string {
  const first = text.split(/(?<=[.!?])\s/)[0] ?? text;
  return first.length > 160 ? `${first.slice(0, 159).trimEnd()}…` : first;
}

/** The queued meta from the queue's own hold text (§3.5): the WIP limit, an engine hold, a pending
 * activation, a resume checkpoint, or plain dispatch. Overlapping file leases do not hold dispatch. */
export function holdText(hold: string | null | undefined, why: string | null | undefined): string {
  if (!hold || hold === "ready for dispatch") return why === "resume" ? "waits for resume" : "waits for dispatch";
  const checkpoint = /^resume checkpoint (\S+)/.exec(hold);
  if (checkpoint) {
    const at = when(checkpoint[1]);
    return at != null ? `waits for resume at ${clock(at)}` : "waits for resume";
  }
  if (hold.startsWith("restart in progress")) return "waits for the restart";
  if (hold.startsWith("engine hold: ")) return `waits for an engine · ${hold.slice("engine hold: ".length)}`;
  if (hold.startsWith("WIP limit")) return `waits for a slot · ${hold}`;
  return `waits · ${hold}`;
}

/** The L2's model and engine, "Opus on Alpha": the engine seam's label, the model as written. */
export function l2Label(task: TaskRow, overview: Overview | undefined): string {
  const engineId = str(task["l2_engine"]) || str(task["engine"]);
  const engineLabel = overview?.engines.find((e) => e.engine === engineId)?.label ?? engineId;
  const model = str(task["engine_model"]) || str(task["model"]);
  return [model ? sentence(model) : "", engineLabel].filter(Boolean).join(" on ");
}

export interface TaskCardFacts {
  dot: DotState;
  meta: string;
  /** The work panel's section (§3.7): needs, active, or done. */
  section: "needs" | "active" | "done";
}

/** Everything the card shows besides the title, from the task row and the overview. */
export function taskCardFacts(task: TaskRow, overview: Overview | undefined, project: string, decision?: Decision): TaskCardFacts {
  const state = task.state ?? "";
  const held = state === "blocked" && Boolean(task.resume_after);
  const fault = str(task["fault"]);
  const reason = str(task["blocked_reason"]);
  const waitsOnL3 = str(task["waiting_on"]) === "l3";
  const wait = overview?.wip.waiting.find((w) => w.project === project && w.slug === task.slug);
  const engine = l2Label(task, overview);
  const prs = (Array.isArray(task["prs"]) ? task["prs"] : []).filter((n): n is number => typeof n === "number");
  const pr = prs[prs.length - 1];

  if (state === "queued" || held) {
    return { dot: "idle", meta: `Queued · ${holdText(wait?.hold, wait?.why ?? (held ? "resume" : "dispatch"))}`, section: "active" };
  }
  if (state === "running") {
    const started = agoText(task["dispatched"]);
    return { dot: "running", meta: ["Running", engine, started ? `started ${started}` : ""].filter(Boolean).join(" · "), section: "active" };
  }
  if (state === "blocked") {
    if (fault) return { dot: "danger", meta: `Blocked: ${oneSentence(reason || `a ${fault} fault stopped the task`)}`, section: "active" };
    // A block waiting on L3 is Altitude's wait: the running dot, not amber (§3.5).
    if (waitsOnL3) return { dot: "running", meta: "Waits for L3", section: "active" };
    return { dot: decision?.kind === "stopped" ? "danger" : "waiting", meta: "Waits for your answer", section: "needs" };
  }
  if (state === "reported") return { dot: "running", meta: "Report landed · waits for L3", section: "active" };
  if (state === "done") return { dot: "idle", meta: pr != null ? `Done · PR #${pr} merged` : "Done", section: "done" };
  if (state === "rejected") return { dot: "idle", meta: "Rejected", section: "done" };
  return { dot: "idle", meta: state ? sentence(state) : "Created", section: "active" };
}

export function TaskCard({
  project,
  task,
  variant = "card",
  decision,
}: {
  project: string;
  task: TaskRow;
  /** The bordered card under a reply, or the hairline row in the work panel. */
  variant?: "card" | "row";
  decision?: Decision;
}) {
  // The same cache entry every page reads: no request of the card's own.
  const overview = useOverview();
  const facts = taskCardFacts(task, overview.data, project, decision);
  const title = task.title || task.slug;
  return (
    <Link
      className="task-card"
      data-variant={variant}
      data-section={facts.section}
      to={`/projects/${project}/tasks/${task.slug}`}
      aria-label={`${title} · ${facts.meta}`}
    >
      <span className="dot task-card-dot" data-state={facts.dot} aria-hidden />
      <span className="task-card-body">
        <span className="task-card-title">{title}</span>
        <span className="task-card-meta">{facts.meta}</span>
      </span>
      <svg className="task-card-go" aria-hidden viewBox="0 0 20 20" width="16" height="16">
        <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </Link>
  );
}
