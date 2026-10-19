import { Fragment, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, sendImprovement, useSaveSuggestion, useSuggestionChanges, useSuggestionRefusals } from "../data/api";
import type { ImprovementRun, ImprovementSuggestion, SuggestionChange, TaskRow } from "../data/api";
import { clockText, dayText, stampText } from "../data/observed";
import { Icon } from "./Choice";
import { Stamp } from "./Stamp";
import { TaskCards } from "./TaskCard";

/*
 * Improvement suggestions (design/wireframes/improvement-review/PROPOSAL.md, "Where suggestions appear"): a running
 * review is a system line with Stop, an ending is a system line with Show, and a posted review is one card, in the
 * project's chat and, while it waits for a decision or a task, under Suggestions in Needs you. Every state is read
 * from the record; a press saves, then waits for the fresh record before its spinner leaves. Dismiss and Undo are the
 * operator's own choice, so the suggestion folds or unfolds at the press, in chat and Needs you alike (SPEC.md §1.1).
 */

type Action = "stop" | "accept" | "retry";

/** One press at a time per view: what is saving, and the one that failed. */
function usePress(project: string) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const press = async (key: string, action: Action, ref?: string) => {
    setFailed(null);
    setBusy(key);
    try {
      await sendImprovement(project, action, crypto.randomUUID().replaceAll("-", ""), ref);
    } catch (error) {
      // A press the record no longer allows is refused: the refreshed card shows where it stands instead.
      if (!(error instanceof ApiError && error.status === 409)) setFailed(key);
    }
    await Promise.all([
      queryClient.refetchQueries({ queryKey: ["project", project] }),
      queryClient.refetchQueries({ queryKey: ["overview"] }),
      queryClient.invalidateQueries({ queryKey: ["chat", project] }),
    ]).catch(() => undefined);
    setBusy(null);
  };
  return { busy, failed, press };
}

/** "Oct 12, 09:38–09:40": when the review ran, its end without the day when it is the same. */
function ranText(run: ImprovementRun): string {
  const end = run.ended ?? run.posted;
  if (!end) return stampText(run.started);
  return `${stampText(run.started)}–${dayText(end) === dayText(run.started) ? clockText(end) : stampText(end)}`;
}

function Details({ run }: { run: ImprovementRun }) {
  return (
    <dl className="irc-details">
      {[...run.details, ["Ran", ranText(run)] as const].map(([label, text], index) => (
        <div key={`${label}-${index}`}>
          <dt>{label}</dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A running review's line, in chat or in settings, with Stop until it ends. */
export function ImprovementRunning({ project, run }: { project: string; run: Pick<ImprovementRun, "id" | "line"> }) {
  const { busy, failed, press } = usePress(project);
  return (
    <div className="sys-line" data-progress data-improvement={run.id}>
      <span className="sys-dot" aria-hidden />
      <span className="sys-text">{run.line}</span>
      {busy ? <span className="spinner" role="status" aria-label="Stopping" /> : (
        <button type="button" className="link" onClick={() => void press("stop", "stop", run.id)}>Stop</button>
      )}
      {failed ? <span className="text-danger" role="alert">Not stopped. Try again.</span> : null}
    </div>
  );
}

/** A review that ended without a card: nothing to suggest, failed or stopped. */
export function ImprovementEnding({ run }: { run: ImprovementRun }) {
  const [open, setOpen] = useState(false);
  const text = run.state === "nothing" ? `Improvement suggestions · ${run.tasks} tasks from ${run.window} · nothing to suggest`
    : run.state === "failed" ? `Improvement suggestions failed · ${run.reason ?? "it did not finish"}`
      : "Improvement suggestions stopped by you";
  return (
    <div className="irc-ending" data-improvement={run.id}>
      <div className="sys-line">
        <span className="sys-dot" data-tone={run.state === "failed" ? "danger" : undefined} aria-hidden />
        <Stamp at={run.ended} className="sys-time" />
        <span className="sys-text">{text}</span>
        {run.state === "stopped" ? null : (
          <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>
        )}
      </div>
      {open ? <Details run={run} /> : null}
    </div>
  );
}

/** Weekly turned off by a change of terms: the line opens the settings section where the new terms are agreed. */
export function ImprovementTermsLine({ project, at }: { project: string; at?: string | null }) {
  return (
    <div className="sys-line">
      <span className="sys-dot" aria-hidden />
      <Stamp at={at} className="sys-time" />
      <span className="sys-text">
        Improvement suggestions are off until you agree to the new terms in{" "}
        <Link to={`/settings/projects/${encodeURIComponent(project)}#improvement`}>settings</Link>.
      </span>
    </div>
  );
}

/** Up to three cited tasks, then "<n> more" opening the rest in place. */
function SeenIn({ project, seen }: { project: string; seen: ImprovementSuggestion["seen"] }) {
  const [all, setAll] = useState(false);
  if (!seen.length) return null;
  const shown = all ? seen : seen.slice(0, 3);
  return (
    <p className="irc-meta">
      Seen in{" "}
      {shown.map((place, index) => (
        <Fragment key={place.slug ?? "chat"}>
          {index ? " · " : null}
          {place.slug ? <Link to={`/projects/${project}/tasks/${place.slug}`}>{place.title}</Link> : <span>{place.title}</span>}
        </Fragment>
      ))}
      {all || seen.length <= 3 ? null : <> · <button type="button" className="link" onClick={() => setAll(true)}>{seen.length - 3} more</button></>}
    </p>
  );
}

function Suggestion({ project, suggestion, tasks, busy, failed, press, choosing, refused, choose }: {
  project: string;
  suggestion: ImprovementSuggestion;
  tasks: Map<string, TaskRow>;
  /** Dismiss or Undo on its way, and the latest one refused, with its reason. */
  choosing: boolean;
  refused?: { change: SuggestionChange; error: Error };
  choose: (action: SuggestionChange["action"]) => void;
} & ReturnType<typeof usePress>) {
  const { ref, state } = suggestion;
  const notSaved = refused ? <p className="irc-error text-danger" role="alert">Not saved: {refused.error.message}{" "}
    <button type="button" className="link" onClick={() => choose(refused.change.action)}>Retry</button></p> : null;
  const chip = <span className="chip" data-tone={suggestion.target === "How agents work" ? "held" : undefined}>{suggestion.target}</span>;
  const title = `${suggestion.n}. ${suggestion.title}`;
  if (state === "dismissed" || state === "not decided") {
    return (
      <section className="irc-sg" data-folded aria-label={title}>
        {chip}
        <span className="irc-title">{title}</span>
        <span className="irc-meta">
          {state === "dismissed" ? "Dismissed" : "Not decided"}
          {suggestion.undo ? <> · {choosing ? <span className="spinner" role="status" aria-label="Saving" />
            : <button type="button" className="link" disabled={Boolean(busy)} onClick={() => choose("undo")}>Undo</button>}</> : null}
        </span>
        {notSaved}
      </section>
    );
  }
  const saving = busy === `${ref}:accept`;
  return (
    <section className="irc-sg" aria-label={title}>
      <div>{chip}</div>
      <h4 className="irc-title">{title}</h4>
      <p className="irc-text">{suggestion.explanation}</p>
      <SeenIn project={project} seen={suggestion.seen} />
      {suggestion.count ? <p className="irc-meta">{suggestion.count}</p> : null}
      {state === "undecided" ? (
        <>
          <div className="irc-actions">
            <button type="button" className="btn irc-make" disabled={Boolean(busy) || choosing} aria-busy={busy === `${ref}:accept` || undefined}
              onClick={() => void press(`${ref}:accept`, "accept", ref)}>
              {busy === `${ref}:accept` ? <span className="spinner" role="status" aria-label="Saving" /> : null}
              <span className="irc-label">Make a task</span>
            </button>
            <button type="button" className="btn irc-dismiss" disabled={Boolean(busy) || choosing} data-dim={saving || undefined} onClick={() => choose("dismiss")}>
              <span className="irc-label">Dismiss</span>
            </button>
            {choosing ? <span className="spinner" role="status" aria-label="Saving" /> : null}
          </div>
          {failed === `${ref}:accept` ? <p className="irc-error text-danger" role="alert">Not saved. Try again.</p> : notSaved}
        </>
      ) : suggestion.task ? (
        <TaskCards project={project} slugs={[suggestion.task]} tasks={tasks} />
      ) : suggestion.opening ? (
        <p className="irc-accepted"><Icon name="check" />Accepted · L3 is opening a task</p>
      ) : (
        <p className="irc-meta irc-retry">
          Accepted · no task yet ·{" "}
          {busy === `${ref}:retry` ? <span className="spinner" role="status" aria-label="Saving" />
            : <button type="button" className="link" disabled={Boolean(busy)} onClick={() => void press(`${ref}:retry`, "retry", ref)}>Try again</button>}
          {failed === `${ref}:retry` ? <span className="text-danger" role="alert"> Not saved. Try again.</span> : null}
        </p>
      )}
    </section>
  );
}

/** A posted review's card. In Needs you its header links to the card in the project's chat, without Details. */
export function ImprovementCard({ project, run, tasks, linked = false }: {
  project: string;
  run: ImprovementRun;
  tasks: Map<string, TaskRow>;
  linked?: boolean;
}) {
  const press = usePress(project);
  const choice = useSaveSuggestion();
  const choosing = useSuggestionChanges();
  const refusals = useSuggestionRefusals();
  const [details, setDetails] = useState(false);
  return (
    <article className="irc" id={linked ? undefined : `improvement-${run.id}`} data-improvement={run.id} aria-label={`Improvement suggestions, ${run.tasks} tasks from ${run.window}`}>
      <header className="irc-head">
        {linked ? <Link className="irc-name" to={`/projects/${project}#improvement-${run.id}`}>Improvement suggestions</Link>
          : <h3 className="irc-name">Improvement suggestions</h3>}
        <Stamp at={run.posted} className="irc-time" />
      </header>
      <p className="irc-window">{run.tasks} tasks from {run.window}</p>
      {run.suggestions.map((suggestion) => (
        <Suggestion key={suggestion.ref} project={project} suggestion={suggestion} tasks={tasks} {...press}
          choosing={choosing.some((change) => change.project === project && change.ref === suggestion.ref)}
          refused={refusals.get(`${project}/${suggestion.ref}`)} choose={(action) => choice.mutate({ project, ref: suggestion.ref, action })} />
      ))}
      {linked ? null : (
        <footer className="irc-foot">
          <button type="button" className="link" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Hide details" : "Details"}</button>
          {details ? <Details run={run} /> : null}
        </footer>
      )}
    </article>
  );
}
