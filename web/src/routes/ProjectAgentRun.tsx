import { useMemo, useState } from "react";
import { useParams } from "react-router";
import { sendProjectAgent, taskMap, useOverview, useProject, useProjectAgentRun, useSuggestionChanges, withSuggestions } from "../data/api";
import type { ProjectAgentRunDetail, TaskView } from "../data/api";
import { middle, refusal, runCost, runTime } from "../data/projectAgents";
import { clockText, dayText } from "../data/observed";
import { AgentFrame, RunDot } from "../components/ProjectAgentParts";
import { SuggestedTasks } from "../components/Improvement";
import { InlineProse, Prose, ProseScope } from "../components/Prose";
import { BusyLabel, StatusMark } from "../components/StatusMark";
import LiveSession from "./LiveSession";

/*
 * One run (design/wireframes/project-agents/PROPOSAL.md, "One run"): what fired it, then its live session while it
 * waits or runs, else its outcome and report with the brief it ran and its session folded. A run has no message box,
 * review boxes, terminal or questions: its session is the task transcript component, read only. A run that suggested
 * tasks shows them under its report ("Suggestions"); one that asked Altitude to change something lists each request
 * under What it changed (ACTING.md, "The run page"), with Undo while it can be undone.
 */

/** A change's target cut in the middle at 40 characters, the full name on hover and for screen readers. */
function Name({ name }: { name: string }) {
  const cut = middle(name);
  return cut === name ? <>{name}</> : <span title={name}><span aria-hidden>{cut}</span><span className="visually-hidden">{name}</span></span>;
}

/** What it changed: one row per request in order; Undo puts one back and Undo all every one that can be, each row
 * spinning until the run reads again and then saying on its own whether it was put back. */
function Changes({ project, agent, run, onRead }: { project: string; agent: string; run: ProjectAgentRunDetail; onRead: () => Promise<unknown> }) {
  const [undoing, setUndoing] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  if (!run.changes.length) return null;
  const undo = async (change: number | "all") => {
    setUndoing(change === "all" ? run.changes.filter((row) => row.undo).map((row) => row.n) : [change]);
    setError(null);
    try {
      await sendProjectAgent(project, "undo", { agent, n: run.n, change });
      await onRead();
    } catch (failure) {
      setError((failure as Error).message);
    }
    setUndoing([]);
  };
  const busy = undoing.length > 0;
  return <section className="settings-section" aria-labelledby="agent-changes">
    <div className="agent-changes-head"><h2 id="agent-changes">What it changed</h2>
      {run.undo_all ? <button type="button" className="btn" aria-label="Undo all" disabled={busy} onClick={() => void undo("all")}>
        <BusyLabel busy={busy && undoing.length > 1} label="Undo all" working="Undoing all" /></button> : null}</div>
    <div className="settings-card agent-changes">
      {run.changes.map((row) => <div key={row.n} className="agent-change">
        <span className="agent-change-words">
          <strong>{row.head} <Name name={row.name} />{row.undone ? " · Undone" : ""}</strong>
          {row.refused ? <small className="text-danger">{refusal("Not undone", row.refused)}</small>
            : row.note ? <small className="text-muted">{row.note}</small> : null}
        </span>
        {row.undo ? <button type="button" className="btn" disabled={busy} aria-label={`Undo: ${row.head} ${row.name}`} onClick={() => void undo(row.n)}>
          <BusyLabel busy={undoing.includes(row.n)} label="Undo" working="Undoing" /></button> : null}
      </div>)}
    </div>
    {error ? <p role="alert" className="text-meta text-danger">{refusal("Not undone", error)}</p> : null}
  </section>;
}

function Session({ project, agent, run, active }: { project: string; agent: string; run: ProjectAgentRunDetail; active: boolean }) {
  const engines = useOverview(false).data?.engines ?? [];
  const session = run.session!;
  // LiveSession reads a task's session fields; a run carries only its own.
  const task = { slug: `${agent}/${run.n}`, state: run.state === "running" ? "running" : "done",
    l2_engine: session.engine, session_id: session.session_id, attempt: session.attempt } as TaskView;
  return <div className="agent-run-session">
    <LiveSession project={project} task={task} run={{ agent, run: run.n }} readOnly active={active}
      engineLabel={engines.find((e) => e.engine === session.engine)?.label ?? session.engine} />
  </div>;
}

/** Waiting or running: the line with Stop, which says Stopping… until the run reads as ended. */
function Live({ project, agent, run, onRead }: { project: string; agent: string; run: ProjectAgentRunDetail; onRead: () => Promise<unknown> }) {
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stop = async () => {
    setStopping(true);
    setError(null);
    try {
      await sendProjectAgent(project, "stop", { agent, n: run.n });
      void onRead();
    } catch (failure) {
      setStopping(false);
      setError((failure as Error).message);
    }
  };
  const running = run.state === "running";
  return <>
    <div className="settings-card agent-status">
      <div className="agent-status-row">
        <p className="agent-status-line">{running ? <span className="spinner" aria-hidden /> : null}
          <span>{running ? `Running · ${run.minutes ?? 0} of ${run.limit} min` : run.line ?? run.outcome}</span></p>
        <div className="agent-status-actions">
          <button type="button" className="btn" disabled={stopping} onClick={() => void stop()}>
            {stopping ? <><span className="spinner" aria-hidden />Stopping…</> : "Stop"}</button>
        </div>
      </div>
      {error ? <p role="alert" className="text-meta text-danger">{error}</p> : null}
    </div>
    <Changes project={project} agent={agent} run={run} onRead={onRead} />
    {running && run.session ? <Session project={project} agent={agent} run={run} active /> : null}
  </>;
}

/** The run's suggested tasks, with Dismiss and Undo on their way applied, and their tasks' cards from the project. */
function Suggested({ project, run }: { project: string; run: NonNullable<ProjectAgentRunDetail["suggested"]> }) {
  const view = useProject(project);
  const changes = useSuggestionChanges();
  const tasks = useMemo(() => taskMap(view.data), [view.data]);
  return <SuggestedTasks project={project} run={withSuggestions(run, project, changes)} tasks={tasks} />;
}

/** Ended: the outcome with its dot and the report, or the reason in its place; the brief and session folded. */
function Ended({ project, agent, run, onRead }: { project: string; agent: string; run: ProjectAgentRunDetail; onRead: () => Promise<unknown> }) {
  const [session, setSession] = useState(false);
  return <>
    <div className="settings-card agent-report">
      <p className="agent-report-head"><RunDot tone={run.tone} /><strong>{run.outcome}</strong></p>
      {run.report ? <Prose text={run.report} /> : run.line ? <p><InlineProse text={run.line} /></p> : null}
    </div>
    <Changes project={project} agent={agent} run={run} onRead={onRead} />
    {run.suggested ? <Suggested project={project} run={run.suggested} /> : null}
    <details className="agent-fold">
      <summary>Brief it ran</summary>
      <div className="agent-fold-body"><Prose text={run.brief} /></div>
    </details>
    {run.session ? <details className="agent-fold" onToggle={(event) => setSession(event.currentTarget.open)}>
      <summary>Session</summary>
      {session ? <Session project={project} agent={agent} run={run} active /> : null}
    </details> : null}
  </>;
}

export default function ProjectAgentRunPage() {
  const { name = "", id = "", n = "" } = useParams();
  const run = useProjectAgentRun(name, id, Number(n));
  const data = run.data;
  const live = data?.state === "waiting" || data?.state === "running";
  return <AgentFrame back={{ to: `/projects/${name}/agents/${id}`, label: data?.agent.name ?? "Routine" }} title={data ? `Run · ${runTime(data.at)}` : "Run"}>
    {run.isPending ? <div className="settings-card"><StatusMark label="Loading this run…" /></div>
      : !data ? <div className="settings-card" role="alert"><p>Could not load this run.</p>
        <button type="button" className="btn agents-retry" onClick={() => void run.refetch()}>Retry</button></div>
        : <ProseScope project={name}>
          <p className="text-meta text-muted agent-run-meta">{[data.trigger_line, data.model, live ? "" : runCost(data)].filter(Boolean).join(" · ")}</p>
          {data.events.length ? <div className="text-meta text-muted agent-run-events" aria-label="Events">
            {data.events.map((event, index) => <p key={index}>{dayText(event.at) === dayText(data.at) ? clockText(event.at) : runTime(event.at)} · {event.line}</p>)}
          </div> : null}
          {live ? <Live key={data.n} project={name} agent={id} run={data} onRead={run.refetch} />
            : <Ended project={name} agent={id} run={data} onRead={run.refetch} />}
        </ProseScope>}
  </AgentFrame>;
}
