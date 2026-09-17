import { useOverview } from "../data/api";
import type { Decision } from "../data/api";
import { DecisionCard } from "../components/DecisionCard";
import { DecisionAlertToggle } from "../data/alerts";
import { attentionSummary, decisionGroups } from "../data/decisions";

/** The subtitle: how much waits, across how many projects. */
export function needsSummary(queue: Decision[]): string {
  const projects = new Set(queue.map((d) => d.project));
  const where = projects.size === 1 ? `in ${[...projects][0]}` : `across ${projects.size} projects`;
  return `${attentionSummary(queue)} ${where}`;
}

/** Needs you (SPEC.md §2.1): contiguous project sections in first-appearance order. */
export default function NeedsYou() {
  const overview = useOverview();

  return (
    <div className="page needs-page">
      <div className="needs-head">
        <h1 className="text-page-title font-semibold">Needs you</h1>
        {overview.isSuccess && overview.data.queue.length > 0 ? (
          <p className="needs-sub text-muted">{needsSummary(overview.data.queue)}</p>
        ) : null}
        {/* The switch waits for the queue: enabling it records what is waiting, not a backlog to announce. */}
        {overview.data ? <DecisionAlertToggle pending={overview.data.queue} /> : null}
      </div>
      {overview.isError && overview.data ? <p className="text-danger" role="alert">Showing saved questions. Refresh before deciding. <button className="link" onClick={() => overview.refetch()}>Refresh</button></p> : null}
      {overview.isPending ? (
        <div className="flex flex-col gap-3" aria-label="Loading">
          <div className="skeleton h-28" />
          <div className="skeleton h-28" />
        </div>
      ) : overview.isError && !overview.data ? (
        <p className="text-danger">
          Could not read what needs you.{" "}
          <button type="button" className="link" onClick={() => overview.refetch()}>
            Retry
          </button>
        </p>
      ) : overview.data!.queue.length === 0 ? (
        <p className="text-muted">Nothing needs you.</p>
      ) : (
        <>
          <div className="needs-list" aria-label="Decisions">
            {[...new Set(overview.data!.queue.map((decision) => decision.project))].map((project) => (
              <section className="needs-project" key={project} aria-label={`Project ${project}`}>
                <h2 className="text-card-title font-semibold">{project}</h2>
                {decisionGroups(overview.data!.queue.filter((decision) => decision.project === project)).map((group) => (
                  <DecisionCard key={`${group[0]!.slug}:${group[0]!.group_id || group[0]!.id}`} decision={group[0]!} decisions={group} from="needs" disabled={overview.isError || (!overview.isFetchedAfterMount && overview.isFetching)} />
                ))}
              </section>
            ))}
          </div>
          <p className="calm text-muted">
            That is everything. Running work stays in each project. FYIs from L3 appear in that project&apos;s chat.
          </p>
        </>
      )}
    </div>
  );
}
