import { useEffect, useRef, useState } from "react";
import { useOverview } from "../data/api";
import type { Decision } from "../data/api";
import { DecisionCard } from "../components/DecisionCard";
import type { PreviewOrigin } from "../components/DecisionCard";
import { useVisitReturn } from "../components/visitMemory";
import { DecisionAlertToggle } from "../data/alerts";
import { attentionSummary, decisionGroups } from "../data/decisions";
import { useSelectedProject } from "../shell/scope";

/** The subtitle: how much waits, across how many projects. */
export function needsSummary(queue: Decision[]): string {
  const projects = new Set(queue.map((d) => d.project));
  const where = projects.size === 1 ? `in ${[...projects][0]}` : `across ${projects.size} projects`;
  return `${attentionSummary(queue)} ${where}`;
}

/** Section order (SPEC.md §3.8): the selected project first when it has items, then first appearance. */
export function projectOrder(queue: Decision[], selected: string | null): string[] {
  const projects = [...new Set(queue.map((decision) => decision.project))];
  return selected && projects.includes(selected) ? [selected, ...projects.filter((project) => project !== selected)] : projects;
}

/** Needs you (SPEC.md §2.1): contiguous project sections, each collapsible from its heading. */
export default function NeedsYou() {
  const overview = useOverview();
  const selected = useSelectedProject();
  // Page state only: cards stay mounted while collapsed, so staged answers survive.
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const toggle = (project: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (!next.delete(project)) next.add(project);
    return next;
  });
  // Back from a preview opened here brings its card into view once.
  const origin = useVisitReturn<PreviewOrigin>("preview");
  const list = useRef<HTMLDivElement>(null);
  const returned = useRef(false);
  useEffect(() => {
    if (!origin || returned.current) return;
    const card = list.current?.querySelector(`[data-question-id="${CSS.escape(origin.id ?? "")}"][data-question-revision="${origin.revision}"]`);
    if (!card) return;
    card.scrollIntoView({ block: "center" });
    returned.current = true;
  }, [origin, overview.data]);

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
          <div className="needs-list" aria-label="Decisions" ref={list}>
            {projectOrder(overview.data!.queue, selected).map((project) => {
              const items = overview.data!.queue.filter((decision) => decision.project === project);
              const open = !collapsed.has(project);
              return (
                <section className="needs-project" key={project} aria-label={`Project ${project}`}>
                  <h2 className="text-card-title font-semibold">
                    <button type="button" className="needs-project-toggle" aria-expanded={open} onClick={() => toggle(project)}>
                      {project}
                      {open ? null : <span className="needs-project-count text-muted">{attentionSummary(items)}</span>}
                    </button>
                  </h2>
                  <div className="needs-project-cards" hidden={!open}>
                    {decisionGroups(items).map((group) => (
                      <DecisionCard key={`${group[0]!.slug}:${group[0]!.group_id || group[0]!.id}`} decision={group[0]!} decisions={group} from="needs" disabled={overview.isError || (!overview.isFetchedAfterMount && overview.isFetching)} />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
          <p className="calm text-muted">
            That is everything. Running work stays in each project. FYIs from L3 appear in that project&apos;s chat.
          </p>
        </>
      )}
    </div>
  );
}
