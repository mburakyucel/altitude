import { useEffect, useRef, useState } from "react";
import { useOverview } from "../data/api";
import type { Attention, Decision, Overview } from "../data/api";
import { DecisionCard, StepCard } from "../components/DecisionCard";
import { ImprovementCard } from "../components/Improvement";
import type { QuestionOrigin } from "../components/DecisionCard";
import { useVisitReturn } from "../components/visitMemory";
import { AlertStatus } from "../data/alerts";
import { attentionSummary, decisionGroups, isStep } from "../data/decisions";
import { useSelectedProject } from "../shell/scope";

/** The subtitle: how much waits, across how many projects. */
export function needsSummary(queue: Attention[]): string {
  const projects = new Set(queue.map((d) => d.project));
  const where = projects.size === 1 ? `in ${[...projects][0]}` : `across ${projects.size} projects`;
  return `${attentionSummary(queue)} ${where}`;
}

/** Section order (SPEC.md §3.8): the selected project first when it has items, then first appearance. */
export function projectOrder(queue: Attention[], selected: string | null): string[] {
  const projects = [...new Set(queue.map((decision) => decision.project))];
  return selected && projects.includes(selected) ? [selected, ...projects.filter((project) => project !== selected)] : projects;
}

const NO_TASKS = new Map();

/**
 * Improvement cards waiting for a decision or a task, after everything that needs you and grouped by project. They
 * are suggestions, not questions: never counted in the summary or badge and never alerting a device.
 */
function Suggestions({ suggestions, selected }: { suggestions: Overview["suggestions"]; selected: string | null }) {
  if (!suggestions.length) return null;
  const projects = [...new Set(suggestions.map((run) => run.project))];
  const order = selected && projects.includes(selected) ? [selected, ...projects.filter((project) => project !== selected)] : projects;
  return (
    <section className="needs-suggestions" aria-label="Suggestions">
      <h2 className="needs-suggestions-label">Suggestions</h2>
      {order.map((project) => (
        <section className="needs-project" key={project} aria-label={`Suggestions for ${project}`}>
          <h3 className="text-card-title font-semibold">{project}</h3>
          <div className="needs-project-cards">
            {suggestions.filter((run) => run.project === project).map((run) => (
              <ImprovementCard key={run.id} project={project} run={run} tasks={NO_TASKS} linked />
            ))}
          </div>
        </section>
      ))}
    </section>
  );
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
  // Back from a preview or review opened here brings its card, at its current revision, into view once.
  const origin = useVisitReturn<QuestionOrigin>("origin");
  const list = useRef<HTMLDivElement>(null);
  const returned = useRef(false);
  useEffect(() => {
    if (!origin || returned.current) return;
    const card = list.current?.querySelector(origin.id ? `[data-question-id="${CSS.escape(origin.id)}"]`
      : `[data-review-task="${CSS.escape(`${origin.project}/${origin.slug}`)}"]`);
    if (!card) return;
    card.scrollIntoView({ block: "center" });
    // The saved queue shows the card at once; the fresh read can move a question revised meanwhile.
    if (overview.isFetchedAfterMount) returned.current = true;
  }, [origin, overview.data, overview.isFetchedAfterMount]);

  return (
    <div className="page needs-page">
      <div className="needs-head">
        <div className="needs-title">
          <h1 className="text-page-title font-semibold">Needs you</h1>
          <AlertStatus />
        </div>
        {overview.isSuccess && overview.data.queue.length > 0 ? (
          <p className="needs-sub text-muted">{needsSummary(overview.data.queue)}</p>
        ) : null}
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
        <>
          <p className="text-muted">Nothing needs you.</p>
          <Suggestions suggestions={overview.data!.suggestions} selected={selected} />
        </>
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
                    {decisionGroups(items).map((group) => {
                      const first = group[0]!;
                      if (isStep(first)) return <StepCard key={`step:${first.id}`} step={first} disabled={overview.isError || (!overview.isFetchedAfterMount && overview.isFetching)} />;
                      const decisions = group.filter((row): row is Decision => !isStep(row));
                      return <DecisionCard key={`${first.slug}:${first.group_id || first.id}`} decision={first} decisions={decisions} from="needs" disabled={overview.isError || (!overview.isFetchedAfterMount && overview.isFetching)} />;
                    })}
                  </div>
                </section>
              );
            })}
          </div>
          <Suggestions suggestions={overview.data!.suggestions} selected={selected} />
        </>
      )}
    </div>
  );
}
