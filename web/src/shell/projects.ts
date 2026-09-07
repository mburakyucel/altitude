import type { Decision, Overview, ProjectRow } from "../data/api";
import { decisionKind } from "../data/decisions";

/** What the rail, the switcher, and the tab bar agree on about a project. */

export function managedProjects(overview: Overview | undefined): ProjectRow[] {
  return overview?.projects.filter((p) => p.managed) ?? [];
}

export function unmanagedFolders(overview: Overview | undefined): ProjectRow[] {
  return overview?.projects.filter((p) => !p.managed) ?? [];
}

export function decisionsFor(overview: Overview | undefined, project: string): Decision[] {
  return overview?.queue.filter((d) => d.project === project) ?? [];
}

export type DotState = "running" | "waiting" | "danger" | "idle";

/**
 * The state dot (SPEC.md §3.1): a fault or a stop beats a wait, a wait beats running work. Only a
 * decision for the operator is a wait; a task blocked waiting on L3 is Altitude's own work and keeps
 * the running dot (§3.5), as does a landed report L3 is handling.
 */
export function dotFor(row: ProjectRow, decisions: Decision[]): DotState {
  if ((row.counts?.fault ?? 0) > 0 || decisions.some((d) => decisionKind(d).tone === "danger")) return "danger";
  if (decisions.length > 0) return "waiting";
  const counts = row.counts ?? {};
  if ((counts["running"] ?? 0) > 0 || (counts["waits_l3"] ?? 0) > 0 || (counts["reported"] ?? 0) > 0) return "running";
  return "idle";
}

/** The project the phone tabs open: the selected one while it is still managed, else the first. */
export function scopedProject(overview: Overview | undefined, selected: string | null): string | null {
  const managed = managedProjects(overview);
  if (selected && managed.some((p) => p.name === selected)) return selected;
  return managed[0]?.name ?? null;
}
