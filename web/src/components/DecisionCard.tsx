import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useDecide } from "../data/api";
import type { Decision } from "../data/api";
import { DEFAULT_OPTIONS, decisionKind } from "../data/decisions";
import { ageText, exactTime } from "../data/observed";
import { setSelectedProject } from "../shell/scope";

/** How long the card takes to collapse after a decision lands. */
const LEAVE_MS = 200;

/**
 * The compact decision card (SPEC.md §3.8): kind row, question, why, the two options with the
 * recommended one primary, More context. Waiting, Deciding, Decided, and Failed live here; Stale is
 * the poll dropping the row.
 */
export function DecisionCard({ decision, chip = false }: { decision: Decision; chip?: boolean }) {
  const decide = useDecide();
  const [chosen, setChosen] = useState<number | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);
  const kind = decisionKind(decision);
  const options = decision.options?.length ? decision.options : DEFAULT_OPTIONS;
  const title = decision.title || decision.slug;
  const to = `/projects/${decision.project}/tasks/${decision.slug}`;

  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => setGone(true), LEAVE_MS);
    return () => clearTimeout(timer);
  }, [leaving]);

  if (gone) return null;

  const choose = (option: number) => {
    setChosen(option);
    decide.mutate(
      { project: decision.project, slug: decision.slug, option },
      { onSuccess: () => setLeaving(true), onError: () => setChosen(null) },
    );
  };
  const deciding = decide.isPending || leaving;

  return (
    <article className="decision" data-leaving={leaving || undefined} aria-label={title}>
      <div className="decision-kind" data-tone={kind.tone}>
        <span className="kind-label">{kind.label}</span>
        {chip ? <span className="chip">{decision.project}</span> : null}
        <span className="ml-auto text-muted" title={exactTime(decision.asked)}>
          {ageText(decision.asked)}
        </span>
      </div>
      <p className="decision-question">{kind.question || title}</p>
      {decision.detail && decision.detail !== kind.question ? (
        <p className="decision-why">{decision.detail}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {options.map((label, index) => (
          <button
            key={label}
            type="button"
            className={index === 0 ? "btn btn-primary" : "btn"}
            disabled={deciding}
            onClick={() => choose(index)}
          >
            {chosen === index && deciding ? <span className="spinner" aria-hidden /> : null}
            {label}
          </button>
        ))}
        <Link
          className="ml-auto text-meta"
          to={to}
          state={{ tab: "needs" }}
          onClick={() => setSelectedProject(decision.project)}
        >
          More context
        </Link>
      </div>
      {decide.isError && !deciding ? (
        <p className="text-meta text-danger" role="alert">
          Could not record the decision.{" "}
          <button type="button" className="link" onClick={() => choose(chosen ?? 0)}>
            Retry
          </button>
        </p>
      ) : null}
    </article>
  );
}
