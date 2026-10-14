import type { Decision, Overview, TaskMessage } from "./api";

/** Unknown attention never looks like a known empty inbox. */
export function attentionCount(overview: Overview | undefined, failed: boolean) {
  if (!overview) return { text: failed ? "?" : "…", label: failed ? "Attention unavailable" : "Loading attention" };
  const count = overview.queue.length;
  return count || failed ? { text: `${count}${failed ? " · saved" : ""}`, label: `${count} pending${failed ? ", saved count" : ""}` } : null;
}

/** The badge counts questions individually and operational attention items separately. */
export function attentionSummary(rows: Decision[]): string {
  const questions = rows.filter((row) => row.id || !["fault", "stopped", "review"].includes(row.kind ?? "")).length;
  const stopped = rows.filter((row) => !row.id && row.kind === "stopped").length;
  const reviews = rows.filter((row) => !row.id && row.kind === "review").length;
  const faults = rows.length - questions - stopped - reviews;
  return [[questions, "question"], [reviews, "review"], [stopped, "stopped task"], [faults, "fault"]]
    .filter(([count]) => count).map(([count, label]) => `${count} ${label}${count === 1 ? "" : "s"}`).join(" · ");
}

/** A question's first line, as a one-line wait label names it: at most 120 characters, cut at a word, exactly as
 * altitude/tasks.py `headline` cuts it for the CLI. */
export function headline(text: string | null | undefined): string {
  const first = (text ?? "").trim().split("\n")[0]!.trim();
  return first.length <= 120 ? first : `${first.slice(0, 119).replace(/\s+\S*$/, "")}…`;
}

/** One task's turn, as Work rows, the task header and its chat say it (SPEC.md §3.5). A waiting task's row is `named`:
 * a lone freeform question shows its first line, so a wait on the operator's step says the step. The task page,
 * which shows the question itself, and a question with choices keep the count. */
export function turnLabel(rows: Decision[], named = false): string | null {
  const questions = rows.filter((row) => row.id);
  const review = rows.find((row) => row.kind === "review");
  const lone = named && questions.length === 1 && !optionsFor(questions[0]!).length ? headline(questions[0]!.question) : "";
  const count = questions.length ? `${questions.length} question${questions.length === 1 ? "" : "s"}` : "";
  const parts = [lone || count, review ? `review PR #${review.pr}` : ""];
  return parts.some(Boolean) ? `Your turn · ${parts.filter(Boolean).join(" · ")}` : null;
}

/** Independent questions from one ask share a compact task card and a single batch submission. */
export function decisionGroups(rows: Decision[]): Decision[][] {
  const groups = new Map<string, Decision[]>();
  for (const row of rows) {
    const key = `${row.project}:${row.slug}:${row.group_id || row.id || "operational"}`;
    const group = groups.get(key) ?? [];
    group.push(row);
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** A Needs you item and every historical revision open the same owning L2 conversation. */
export function questionPath(decision: Pick<Decision, "project" | "slug" | "id" | "revision">): string {
  const query = new URLSearchParams();
  if (decision.id) query.set("question", decision.id);
  if (decision.revision != null) query.set("revision", String(decision.revision));
  return `/projects/${decision.project}/tasks/${decision.slug}${query.size ? `?${query}` : ""}`;
}

export function decisionKind(decision: Pick<Decision, "kind" | "asked_by">): { label: string; tone: "accent" | "claimed" | "danger" } {
  if (decision.kind === "fault") return { label: "Fault", tone: "danger" };
  if (decision.kind === "stopped") return { label: "Stopped mid-task", tone: "danger" };
  if (decision.kind === "review") return { label: "Review before merge", tone: "claimed" };
  return { label: decision.asked_by === "l3" ? "L3 brought this to you" : "The L2 asks", tone: "accent" };
}

export type Option = { key: string; label: string; text: string };
/** A question's quick answers: its options, or its recommendation as the one accepting choice. */
export function optionsFor(question: Decision): Option[] {
  if (question.options) return question.options;
  const recommended = question.recommendation;
  return recommended?.text ? [{ key: "recommended", label: recommended.label || "Accept", text: recommended.text }] : [];
}

/** The review card's message, exactly as the merge check reads it (altitude/tasks.py `approved_pr`). */
const APPROVAL = /^Approved: merge PR #(\d+)( at [0-9a-f]{7})?\.$/;

/** One answer saved in an operator message: a quick option has its `label`; written text has none. */
export type Selection = { question?: Decision; label?: string; text: string; about: string };

/*
 * What a saved operator message chose (SPEC.md §3.8.2 Decision selections), read from the record alone so a
 * reload or another device shows the same: the answers whose saved response is this message, or the review
 * card's approval. Nothing here changes what was recorded or how the owner cites it.
 */
export function selections(message: Pick<TaskMessage, "id" | "text">, questions: Decision[]): Selection[] {
  const answered = new Map<string, Decision>();
  for (const question of questions) {
    if (question.response?.message_id === message.id) answered.set(question.id ?? question.slug, question);
  }
  if (answered.size) {
    return [...answered.values()].map((question) => {
      const text = question.response!.text;
      return { question, text, label: optionsFor(question).find((option) => option.text === text)?.label,
        about: question.question || question.title || question.slug };
    });
  }
  const approval = APPROVAL.exec(message.text.trim());
  return approval ? [{ label: "Approve merge", text: message.text, about: `PR #${approval[1]}` }] : [];
}
