/*
 * A chat command the operator asked to open in a terminal (SPEC.md §3.3). It lives only in page memory,
 * never in the URL or browser history, so no link, reload, Back or Forward brings it back; the terminal
 * view for its project and task takes it once, and a newer request replaces an untaken one.
 */

let pending: { key: string; command: string } | null = null;
const listeners = new Set<() => void>();

const keyOf = (project: string, task?: string) => `${project}\n${task ?? ""}`;

export function requestCommand(project: string, task: string | undefined, command: string) {
  pending = { key: keyOf(project, task), command };
  for (const listener of listeners) listener();
}

/** The waiting command for this terminal, removed as it is returned. */
export function takeCommand(project: string, task?: string): string | null {
  if (pending?.key !== keyOf(project, task)) return null;
  const { command } = pending;
  pending = null;
  return command;
}

export function subscribeCommands(listener: () => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}
