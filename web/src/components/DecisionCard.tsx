import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useChat, useDecide, useTask } from "../data/api";
import type { Decision, DecisionOption } from "../data/api";
import { askerLabel, decisionKind, decisionOptions, followUpsOf, recommendedOption } from "../data/decisions";
import type { FollowUp } from "../data/decisions";
import { ageText, exactTime } from "../data/observed";
import { setSelectedProject } from "../shell/scope";
import { Prose } from "./Prose";

/** How long the card takes to collapse after a decision lands. */
export const LEAVE_MS = 200;

/** The two option buttons, the recommended one primary (SPEC.md §3.8); shared by the card and the page. */
export function DecisionOptions({
  options,
  recommended,
  chosen,
  deciding,
  size,
  onChoose,
}: {
  options: DecisionOption[];
  recommended: DecisionOption;
  chosen: string | null;
  deciding: boolean;
  size?: "lg";
  onChoose: (option: DecisionOption) => void;
}) {
  return (
    <>
      {options.map((option) => (
        <button
          key={option.key ?? option.label}
          type="button"
          className={`btn${option === recommended ? " btn-primary" : ""}${size === "lg" ? " btn-lg" : ""}`}
          disabled={deciding}
          onClick={() => onChoose(option)}
        >
          {chosen === option.label && deciding ? <span className="spinner" aria-hidden /> : null}
          {option.label}
        </button>
      ))}
    </>
  );
}

/** The follow-ups mirrored under the why (SPEC.md §3.8 Follow-up sent, Answer arrived). */
export function FollowUpThread({ items, compact = true }: { items: FollowUp[]; compact?: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="decision-thread" aria-label="Follow-ups">
      {items.map((item) => {
        const who = askerLabel(item.to);
        return (
          <div key={item.id} className="decision-fu" data-to={item.to} data-wait={item.answer == null || undefined}>
            <p className="decision-fu-q">
              <b>You asked:</b> {item.question}
              {item.answer == null ? (
                <span className="text-muted">
                  {" "}
                  · {item.failed ? `${who} could not answer` : item.queued ? "queued for L3" : `waiting for ${who}`}
                </span>
              ) : null}
            </p>
            {item.answer != null ? (
              <div className="decision-fu-a">
                <b>{item.to === "l2" ? "The L2:" : "L3:"}</b>{" "}
                {compact ? <span>{item.answer}</span> : <Prose text={item.answer} />}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The compact decision card (SPEC.md §3.8): kind row, question, why, the follow-ups mirrored from
 * the rows that carry the slug, the two options with the recommended one primary, More context.
 * Waiting, Follow-up sent, Answer arrived, Deciding, Decided, and Failed live here; Stale is the
 * poll dropping the row.
 */
export function DecisionCard({
  decision,
  chip = false,
  selected = false,
  from = "project",
}: {
  decision: Decision;
  chip?: boolean;
  /** Its decision page is open beside it (§3.7): the accent border. */
  selected?: boolean;
  /** Where More context is opened from; the page's crumb leads back there. */
  from?: "needs" | "project";
}) {
  const decide = useDecide();
  const [chosen, setChosen] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);
  // The follow-ups: the project's chat rows and the task's messages that carry this decision (§4.3).
  const chat = useChat(decision.project);
  const task = useTask(decision.project, decision.slug);
  const kind = decisionKind(decision);
  const options = decisionOptions(decision);
  const recommended = recommendedOption(decision);
  const title = decision.title || decision.slug;
  const why = decision.recommendation?.why?.trim() || "";
  const followUps = followUpsOf(decision, chat.data?.history ?? [], chat.data?.queued ?? [], task.data?.messages ?? []);
  const to = `/projects/${decision.project}/decisions/${decision.slug}`;

  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => setGone(true), LEAVE_MS);
    return () => clearTimeout(timer);
  }, [leaving]);

  if (gone) return null;

  const choose = (option: DecisionOption) => {
    setChosen(option.label);
    decide.mutate(
      { project: decision.project, slug: decision.slug, option: option.key ?? option.label },
      { onSuccess: () => setLeaving(true), onError: () => setChosen(null) },
    );
  };
  const deciding = decide.isPending || leaving;
  const retry = () => choose(options.find((o) => o.label === chosen) ?? recommended);

  return (
    <article className="decision" data-leaving={leaving || undefined} data-selected={selected || undefined} aria-label={title}>
      <div className="decision-kind" data-tone={kind.tone}>
        <span className="kind-label">{kind.label}</span>
        {chip ? <span className="chip">{decision.project}</span> : null}
        <span className="decision-task truncate text-muted">{title}</span>
        <span className="ml-auto text-muted" title={exactTime(decision.asked)}>
          {ageText(decision.asked)}
        </span>
      </div>
      <p className="decision-question">{decision.question || title}</p>
      {why ? <p className="decision-why">{why}</p> : null}
      <FollowUpThread items={followUps} />
      <div className="decision-options">
        <DecisionOptions options={options} recommended={recommended} chosen={chosen} deciding={deciding} onChoose={choose} />
        <Link
          className="ml-auto text-meta"
          to={to}
          state={{ from, tab: from === "needs" ? "needs" : "work" }}
          onClick={() => setSelectedProject(decision.project)}
        >
          More context
        </Link>
      </div>
      {decide.isError && !deciding ? (
        <p className="text-meta text-danger" role="alert">
          Could not record the decision.{" "}
          <button type="button" className="link" onClick={retry}>
            Retry
          </button>
        </p>
      ) : null}
    </article>
  );
}
