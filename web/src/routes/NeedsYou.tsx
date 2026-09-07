import { useOverview } from "../data/api";
import type { Decision } from "../data/api";
import { DecisionCard } from "../components/DecisionCard";

/** Cards grouped by project, projects in the order the overview lists them. */
export function groupByProject(queue: Decision[], order: string[]): Array<[string, Decision[]]> {
  const groups = new Map<string, Decision[]>();
  for (const name of order) groups.set(name, []);
  for (const d of queue) groups.set(d.project, [...(groups.get(d.project) ?? []), d]);
  return [...groups].filter(([, rows]) => rows.length > 0);
}

/** Needs you (SPEC.md §2.1): every decision across projects as compact cards. */
export default function NeedsYou() {
  const overview = useOverview();

  return (
    <div className="page">
      <h1 className="text-page-title font-semibold">Needs you</h1>
      {overview.isPending ? (
        <div className="flex flex-col gap-3" aria-label="Loading">
          <div className="skeleton h-28" />
          <div className="skeleton h-28" />
        </div>
      ) : overview.isError ? (
        <p className="text-danger">
          Could not read what needs you.{" "}
          <button type="button" className="link" onClick={() => overview.refetch()}>
            Retry
          </button>
        </p>
      ) : overview.data.queue.length === 0 ? (
        <p className="text-muted">Nothing needs you.</p>
      ) : (
        groupByProject(
          overview.data.queue,
          overview.data.projects.map((p) => p.name),
        ).map(([project, rows]) => (
          <section key={project} className="flex flex-col gap-3" aria-label={project}>
            <h2 className="label">{project}</h2>
            {rows.map((d) => (
              <DecisionCard key={`${d.project}:${d.slug}`} decision={d} chip />
            ))}
          </section>
        ))
      )}
    </div>
  );
}
