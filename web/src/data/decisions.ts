import type { Decision, Overview } from "./api";

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

/** One task's turn, as Work rows, the task header and its chat say it (SPEC.md §3.5). */
export function turnLabel(rows: Decision[]): string | null {
  const questions = rows.filter((row) => row.id).length;
  const review = rows.find((row) => row.kind === "review");
  const parts = [questions ? `${questions} question${questions === 1 ? "" : "s"}` : "", review ? `review PR #${review.pr}` : ""];
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

/** A design preview's owning question, where Back goes with no app history behind the preview. */
export function previewQuestion(project: string, slug: string, questionId: string, revision: string): string {
  return `/projects/${project}/tasks/${slug}?${new URLSearchParams({ question: questionId, revision })}`;
}

export function decisionKind(decision: Pick<Decision, "kind" | "asked_by">): { label: string; tone: "accent" | "claimed" | "danger" } {
  if (decision.kind === "fault") return { label: "Fault", tone: "danger" };
  if (decision.kind === "stopped") return { label: "Stopped mid-task", tone: "danger" };
  if (decision.kind === "review") return { label: "Review before merge", tone: "claimed" };
  return { label: decision.asked_by === "l3" ? "L3 brought this to you" : "The L2 asks", tone: "accent" };
}
