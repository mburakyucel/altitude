function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * The only rendering of subagent launches: "launches 0 · cap 3".
 * Never "0/3" — that was read as a plan to launch three agents — and never "agents".
 * Task, Project and Monitor all render the counter through this one function.
 */
export function launchLabel(used: unknown, cap: unknown): string {
  const n = num(used);
  const m = num(cap);
  return `launches ${n ?? 0} · cap ${m ?? "?"}`;
}
