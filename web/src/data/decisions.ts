import type { Decision } from "./api";

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
  if (decision.kind === "review") return { label: "Ready for review", tone: "claimed" };
  return { label: decision.asked_by === "l3" ? "L3 brought this to you" : "The L2 asks", tone: "accent" };
}
