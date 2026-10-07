import type { Decision, TaskRow } from "./api";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function sentence(value: string): string {
  return /[.!?…]$/.test(value) ? value : `${value}.`;
}

/** Display prose only. Full authored reasons and diagnostic evidence stay in Task details.
 * #658: raw worker output and identifiers must never become the primary explanation. */
export function statusExcerpt(value: unknown): string {
  const prose = text(value).trim().replace(/^L[23]:\s*/i, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  const first = prose.split(/\n|\\[nrt"]|[{}]|\s*\([^)]*\b[a-f0-9]{8,}\b|\b[a-f0-9]{8,}\b|(?<=[.!?])\s/)[0]?.trim() ?? "";
  if (!first || /^(system fault|L2 worker|worker state=)/i.test(first)) return "";
  const short = first.length > 120 ? `${first.slice(0, 117).replace(/\s+\S*$/, "")}…` : first;
  return short.replace(/[.!;:]$/, "");
}

function currentQuestions(task: TaskRow): Record<string, unknown>[] {
  const group = record(task["question_group"]);
  const rows = group["questions"] ?? task["questions"];
  return (Array.isArray(rows) ? rows : []).map(record).filter((q) => q["status"] === "open"
    && (q["audience"] === "l3" || !task["handed_back"] || text(q["asked"]) > text(task["handed_back"])));
}

/** Read-only wording shared by task rows and pages. It creates no action, recovery or timer. */
export function taskExplanation(task: TaskRow, decision?: Decision): string | null {
  const state = task.state;
  if (state === "done" || state === "rejected") return null;
  const steering = record(task["steering"])["state"];
  if (state === "running" && !task["stop_id"] && steering !== "stopping" && steering !== "stop_unconfirmed") return null;
  const questions = currentQuestions(task);
  const coordinator = questions.find((q) => q["audience"] === "l3" && !q["response"]);
  const question = statusExcerpt(coordinator?.["detail"] ?? coordinator?.["question"]);
  const fault = text(task["fault"]);
  if (fault && state !== "running") {
    const cause = fault === "l2-died" ? "The task session ended before completion."
      : "A system problem paused work.";
    return `${cause} ${question ? sentence(`Coordinator needed: ${question}`) : "Waiting for the coordinator to check the blocker."}`;
  }
  if (steering === "stopping") return "Stopping at your request; waiting for the session to end.";
  if (steering === "stop_unconfirmed") return "Stop is unconfirmed; the session may still be running.";
  if (steering === "stopped") return "Stopped by you; continue when you’re ready.";
  if ((task["stop_id"] && !task.resume_after && steering !== "resuming") || decision?.kind === "stopped") return "You requested a stop; confirmation is in the task.";
  if (state === "running") return null;
  if (state === "queued" && task.planned_wait) {
    if (task.planned_wait.after) {
      const title = statusExcerpt(task.planned_wait.after_title);
      return `Waiting for ${title ? `“${title}”` : `prerequisite task “${task.planned_wait.after}”`} to finish.`;
    }
    return `Waiting for ${statusExcerpt(task.planned_wait.reason) || "a recorded prerequisite"}.`;
  }
  if (task.resume_after || steering === "resuming") return "Waiting for Altitude to resume the task.";
  if (state === "queued") return "Waiting for Altitude to start the task.";
  if (state === "blocked" || state === "reported") {
    if (decision?.kind === "review") return "Waiting for your review before merge.";
    if (questions.some((q) => q["audience"] === "operator" && !q["response"]) || decision?.id) {
      return "Waiting for your answer to the task’s question.";
    }
    if (coordinator || task["waiting_on"] === "l3") {
      const reason = question || statusExcerpt(task["blocked_reason"]);
      if (coordinator || !questions.some((q) => q["response"])) {
        return reason ? sentence(`Waiting for the coordinator: ${reason}`) : "Waiting for the coordinator to resolve a blocker.";
      }
    }
    if (questions.some((q) => q["response"])) return "Waiting for the task owner to continue after the reply.";
    if (task["waiting_on"] === "operator") return "Waiting for your input; see the task conversation.";
    if (state === "reported") return "Waiting for the coordinator to check the task’s report.";
    const reason = statusExcerpt(task["blocked_reason"]);
    return reason ? sentence(`Paused: ${reason}`) : task["blocked_reason"] ? "Work is paused; see details for the recorded reason." : "Work is paused; no reason is recorded.";
  }
  return null;
}
