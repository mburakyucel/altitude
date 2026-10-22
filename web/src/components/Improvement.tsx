import { Fragment, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, sendImprovement, useSaveSuggestion, useSuggestionChanges, useSuggestionRefusals } from "../data/api";
import type { AgentSuggestions, ImprovementRun, ImprovementSuggestion, SuggestionCard, SuggestionChange, TaskRow } from "../data/api";
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
 * An agent run's suggested tasks (project-agents/PROPOSAL.md, "Suggestions") use the same suggestion part, on the
 * same card in Needs you and as Suggested tasks on the run page.
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
      queryClient.invalidateQueries({ queryKey: ["project", project, "agents"] }),
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

/** A running review's line in chat, with Stop until it ends. */
export function ImprovementRunning({ project, run }: { project: string; run: Pick<ImprovementRun, "id" | "line"> }) {
  const { busy, failed, press } = usePress(project);
  return (
    <div className="sys-line" data-progress data-improvement={run.id}>
      <span className="sys-dot" aria-hidden />
      <span className="sys-text">{run.line}</span>
      {busy ? <span className="spinner" role="status" aria-label="Stopping" /> : (
        <button type="button" className="link" onClick={() => void press("stop", "stop", run.id)}>Stop</button>
      )}
      {failed ? <span className="text-danger" role="alert">Not stopped.</span> : null}
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
    <div className="irc-ending" id={`improvement-${run.id}`} data-improvement={run.id}>
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

/** A change of terms paused the Weekly improvement review: Open goes to its page, where the new terms are agreed. */
export function ImprovementTermsLine({ project, at }: { project: string; at?: string | null }) {
  return (
    <div className="sys-line">
      <span className="sys-dot" aria-hidden />
      <Stamp at={at} className="sys-time" />
      <span className="sys-text">
        <strong>Weekly improvement review</strong> paused: its terms changed.{" "}
        <Link to={`/projects/${encodeURIComponent(project)}/agents/weekly`}>Open</Link>
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
  // A refusal shows while the record, read again after it, still lacks the change: one saved despite a lost reply needs no word.
  const unsaved = refused && (refused.change.action === "dismiss" ? state === "undecided" : state === "dismissed" && suggestion.undo);
  const notSaved = unsaved ? <p className="irc-error text-danger" role="alert">Not saved: {refused.error.message}{" "}
    <button type="button" className="link" onClick={() => choose(refused.change.action)}>Retry</button></p> : null;
  // Only the review's suggestions name a target; an agent run's are for its own project.
  const chip = suggestion.target ? <span className="chip" data-tone={suggestion.target === "How agents work" ? "held" : undefined}>{suggestion.target}</span> : null;
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
      {chip ? <div>{chip}</div> : null}
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
          {failed === `${ref}:accept` ? <p className="irc-error text-danger" role="alert">Not saved.</p> : notSaved}
        </>
      ) : suggestion.task ? (
        <TaskCards project={project} slugs={[suggestion.task]} tasks={tasks} />
      ) : suggestion.opening ? (
        <p className="irc-accepted"><Icon name="check" />Accepted · L3 is opening a task</p>
      ) : (
        <p className="irc-meta irc-retry">
          Accepted · no task yet ·{" "}
          {busy === `${ref}:retry` ? <span className="spinner" role="status" aria-label="Saving" />
            : <button type="button" className="link" disabled={Boolean(busy)} onClick={() => void press(`${ref}:retry`, "retry", ref)}>Make a task</button>}
          {failed === `${ref}:retry` ? <span className="text-danger" role="alert"> Not saved.</span> : null}
        </p>
      )}
    </section>
  );
}

/** A card's suggestions, each with its buttons; one press at a time across them. */
function Suggestions({ project, run, tasks }: { project: string; run: SuggestionCard; tasks: Map<string, TaskRow> }) {
  const press = usePress(project);
  const choice = useSaveSuggestion();
  const choosing = useSuggestionChanges();
  const refusals = useSuggestionRefusals();
  return <>
    {run.suggestions.map((suggestion) => (
      <Suggestion key={suggestion.ref} project={project} suggestion={suggestion} tasks={tasks} {...press}
        choosing={choosing.some((change) => change.project === project && change.ref === suggestion.ref)}
        refused={refusals.get(`${project}/${suggestion.ref}`)} choose={(action) => choice.mutate({ project, ref: suggestion.ref, action })} />
    ))}
  </>;
}

const runPath = (project: string, run: AgentSuggestions) => `/projects/${encodeURIComponent(project)}/agents/${run.agent}/runs/${run.n}`;

/**
 * A card of suggested tasks. A posted review's card: in Needs you its header links to the card in the project's chat,
 * without Details. An agent run's card, in Needs you: the agent and the run's time, what fired it and Open the run.
 */
export function ImprovementCard({ project, run, tasks, linked = false }: {
  project: string;
  run: SuggestionCard;
  tasks: Map<string, TaskRow>;
  linked?: boolean;
}) {
  const [details, setDetails] = useState(false);
  if (run.kind === "agent") {
    return (
      <article className="irc" data-improvement={run.id} aria-label={`Suggested tasks from ${run.name}`}>
        <header className="irc-head">
          <h3 className="irc-name">{run.name}</h3>
          <Stamp at={run.at} className="irc-time" />
        </header>
        {run.line ? <p className="irc-window">{run.line}</p> : null}
        <Suggestions project={project} run={run} tasks={tasks} />
        <footer className="irc-foot"><Link className="irc-open" to={runPath(project, run)}>Open the run</Link></footer>
      </article>
    );
  }
  return (
    <article className="irc" id={linked ? undefined : `improvement-${run.id}`} data-improvement={run.id} aria-label={`Improvement suggestions, ${run.tasks} tasks from ${run.window}`}>
      <header className="irc-head">
        {linked ? <Link className="irc-name" to={`/projects/${project}#improvement-${run.id}`}>Improvement suggestions</Link>
          : <h3 className="irc-name">Improvement suggestions</h3>}
        <Stamp at={run.posted} className="irc-time" />
      </header>
      <p className="irc-window">{run.tasks} tasks from {run.window}</p>
      <Suggestions project={project} run={run} tasks={tasks} />
      {linked ? null : (
        <footer className="irc-foot">
          <button type="button" className="link" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Hide details" : "Details"}</button>
          {details ? <Details run={run} /> : null}
        </footer>
      )}
    </article>
  );
}

/**
 * The run page's Suggested tasks, under the report: "L3 is checking" until L3 posts or its deadline passes, then what it
 * kept with the same buttons and states, and what it set aside folded with each reason. A check that missed its
 * deadline gives its one reason on the fold.
 */
export function SuggestedTasks({ project, run, tasks }: { project: string; run: AgentSuggestions; tasks: Map<string, TaskRow> }) {
  return (
    <section className="irc agent-suggested" aria-label="Suggested tasks">
      <h3 className="irc-name">Suggested tasks</h3>
      {run.checking ? (
        <p className="irc-meta agent-checking"><span className="spinner" aria-hidden />L3 is checking {run.checking} suggestion{run.checking === 1 ? "" : "s"}</p>
      ) : <>
        <Suggestions project={project} run={run} tasks={tasks} />
        {run.set_aside.length ? (
          <details className="agent-fold agent-aside" data-first={run.suggestions.length ? undefined : true}>
            <summary>Not offered · {run.set_aside.length}{run.unfinished ? " · not checked in time" : ""}</summary>
            <div className="agent-aside-body">
              {run.set_aside.map((item) => (
                <p key={item.n}><span className="irc-title">{item.n}. {item.title}</span>
                  {item.reason ? <span className="irc-meta">{item.reason}</span> : null}</p>
              ))}
            </div>
          </details>
        ) : null}
      </>}
    </section>
  );
}
