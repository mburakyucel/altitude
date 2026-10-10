import { Link, useLocation } from "react-router";
import { useOverview } from "../data/api";
import type { Decision, Overview, TaskRow } from "../data/api";
import { agoText, when } from "../data/observed";
import { clock } from "./Bubbles";
import type { DotState } from "../shell/projects";
import { questionPath, turnLabel } from "../data/decisions";
import { setSelectedProject } from "../shell/scope";
import { statusExcerpt, stoppedByCoordinator, taskExplanation } from "../data/taskStatus";

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

/** The queued meta from the queue's own hold text (§3.5): the WIP limit, an engine hold, a pending
 * activation, a resume checkpoint, or plain dispatch. Overlapping file leases do not hold dispatch. */
export function holdText(hold: string | null | undefined, why: string | null | undefined): string {
  if (!hold || hold === "ready for dispatch") return why === "resume" ? "waits for resume" : "waits for dispatch";
  const checkpoint = /^resume checkpoint (\S+)/.exec(hold);
  if (checkpoint) {
    const at = when(checkpoint[1]);
    return at != null ? `waits for resume at ${clock(at)}` : "waits for resume";
  }
  if (hold.startsWith("restart in progress")) return "waits for Altitude to restart";
  if (hold.startsWith("engine hold: ")) return "waits for an available coding engine";
  if (hold.startsWith("WIP limit")) return "waits for a free task slot";
  return `waits · ${statusExcerpt(hold) || "the start condition is unavailable"}`;
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
}

/** Everything the card shows besides the title, from the task row and the overview. */
export function taskCardFacts(task: TaskRow, overview: Overview | undefined, project: string, decision?: Decision): TaskCardFacts {
  const state = task.state ?? "";
  const held = state === "blocked" && Boolean(task.resume_after);
  const fault = str(task["fault"]);
  const explanation = taskExplanation(task, decision, true);
  const waitsOnL3 = str(task["waiting_on"]) === "l3";
  const steering = task["steering"] as { state?: string } | undefined;
  const stopped = decision?.kind === "stopped" || ["stopped", "stopping", "stop_unconfirmed"].includes(steering?.state ?? "") || (Boolean(task["stop_id"]) && !held);
  const wait = overview?.wip.waiting.find((w) => w.project === project && w.slug === task.slug);
  const engine = l2Label(task, overview);
  const prs = (Array.isArray(task["prs"]) ? task["prs"] : []).filter((n): n is number => typeof n === "number");
  const pr = prs[prs.length - 1];
  const replying = Boolean(task["handed_back"]) && (state === "running" || held);

  if (state === "done") return { dot: "idle", meta: pr != null ? `Done · PR #${pr} merged` : "Done" };
  if (state === "rejected") return { dot: "idle", meta: "Rejected" };
  if (fault && explanation) return { dot: "danger", meta: explanation };
  // L3's Stop is Altitude's wait, like any block waiting on L3 (§3.5); the operator's own Stop stays red.
  if (stopped && explanation) return { dot: stoppedByCoordinator(task) ? "running" : "danger", meta: explanation };
  if (replying) return { dot: "running", meta: "L2 replying to you" };
  if (state === "queued" && task.planned_wait) return { dot: "idle", meta: `Planned · ${explanation}` };
  if (state === "queued" || held) {
    return { dot: "idle", meta: `${held ? "Waiting to resume" : "Queued"} · ${holdText(wait?.hold, wait?.why ?? (held ? "resume" : "dispatch"))}` };
  }
  if (state === "running") {
    const started = agoText(task["dispatched"]);
    return { dot: "running", meta: ["L2 working", engine, started ? `started ${started}` : ""].filter(Boolean).join(" · ") };
  }
  if (state === "blocked") {
    // A block waiting on L3 is Altitude's wait: the running dot, not amber (§3.5).
    if (waitsOnL3 && !decision) return { dot: "running", meta: explanation! };
    return { dot: "idle", meta: explanation! };
  }
  if (state === "reported") return { dot: "running", meta: explanation! };
  return { dot: "idle", meta: state ? sentence(state) : "Status unavailable" };
}

export function TaskCard({
  project,
  task,
  variant = "card",
}: {
  project: string;
  task: TaskRow;
  /** The bordered card under a reply, or the hairline row in the work panel. */
  variant?: "card" | "row";
}) {
  // The same cache entry every page reads: no request of the card's own.
  const overview = useOverview();
  const location = useLocation();
  const decisions = task.state === "done" || task.state === "rejected" ? [] : (overview.data?.queue ?? [])
    .filter((d) => d.project === project && d.slug === task.slug && d.status !== "resolved" && d.audience !== "l3");
  const questions = decisions.filter((d) => d.id);
  const facts = taskCardFacts(task, overview.data, project, decisions[0]);
  const turn = turnLabel(decisions);
  const attention = turn ? `${turn}${overview.isError ? " · saved" : ""}` : null;
  const tab = variant === "row" || new URLSearchParams(location.search).get("tab") === "work" ? "work" : "chat";
  const title = task.title || task.slug;
  return (
    <Link
      className="task-card"
      data-variant={variant}
      to={questions[0] ? questionPath(questions[0]) : `/projects/${project}/tasks/${task.slug}`}
      state={{ from: "project", tab }}
      onClick={() => setSelectedProject(project)}
      aria-label={[title, attention, facts.meta].filter(Boolean).join(" · ")}
    >
      <span className="dot task-card-dot" data-state={facts.dot === "danger" ? "danger" : attention ? "waiting" : facts.dot} aria-hidden />
      <span className="task-card-body">
        <span className="task-card-title">{title}</span>
        {attention ? <span className="task-card-attention">{attention}</span> : null}
        <span className="task-card-meta">{facts.meta}</span>
      </span>
      <svg className="task-card-go" aria-hidden viewBox="0 0 20 20" width="16" height="16">
        <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </Link>
  );
}
