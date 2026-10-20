import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { sendProjectAgent, useProjectAgent, useProjectAgents } from "../data/api";
import type { AgentClock, ProjectAgentAction, ProjectAgentDetail, ProjectAgentRun } from "../data/api";
import { refusal, runCost, runWhen, zoned } from "../data/projectAgents";
import { AgentFrame, Chevron, ClampedBrief, PauseMark, RunDot } from "../components/ProjectAgentParts";
import { InlineProse, ProseScope } from "../components/Prose";
import { BusyLabel, StatusMark } from "../components/StatusMark";
import { Overlay } from "../shell/Overlay";

/*
 * One agent (design/wireframes/project-agents/PROPOSAL.md, "One agent"): its status with the actions the server
 * offers, recent runs, what it does with Edit, and Retire. A retired agent's page keeps its runs and definition as
 * text. Every press holds its button until a read of the agent shows the result.
 */

const LABELS: Record<Exclude<ProjectAgentAction, "open">, string> = { run: "Run now", pause: "Pause", resume: "Resume", turn_on: "Turn on", stop: "Stop" };

type Pending = { action: ProjectAgentAction; done: (agent: ProjectAgentDetail) => boolean };

/** The status card: Pause, Resume and Turn on answer at once; Run now says Starting… and Stop Stopping… until read. */
function StatusCard({ project, agent, clock, onRead }: { project: string; agent: ProjectAgentDetail; clock?: AgentClock; onRead: () => void }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<{ action: ProjectAgentAction; message: string } | null>(null);
  useEffect(() => {
    if (pending && pending.done(agent)) setPending(null);
  }, [agent, pending]);
  const { status } = agent;
  const press = async (action: ProjectAgentAction) => {
    const run = status.run;
    setError(null);
    setPending({ action, done: () => false });
    try {
      const result = await sendProjectAgent<{ n?: number }>(project, action, { agent: agent.id, ...(action === "stop" ? { n: run } : {}) });
      const done = {
        run: (read: ProjectAgentDetail) => read.status.run === result.n || read.runs.some((row) => row.n === result.n),
        stop: (read: ProjectAgentDetail) => read.status.run !== run,
        pause: (read: ProjectAgentDetail) => read.state === "paused",
        resume: (read: ProjectAgentDetail) => read.state === "on",
        turn_on: (read: ProjectAgentDetail) => read.state === "on",
        open: () => true,
      }[action];
      setPending({ action, done });
      onRead();
    } catch (failure) {
      setPending(null);
      setError({ action, message: (failure as Error).message });
    }
  };
  // Pause, Resume and Turn on show their result at once; the server's line replaces it once read.
  // Pausing during a run lets it finish: its line, Open and Stop stay.
  const shown = pending?.action === "pause" && status.run != null ? { line: zoned(status.line, clock), actions: status.actions.filter((action) => action !== "pause"), mark: status.spinning ? <span className="spinner" aria-hidden /> : null }
    : pending?.action === "pause" ? { line: "Paused", actions: ["resume", "run"] as ProjectAgentAction[], mark: <PauseMark /> }
    : pending?.action === "resume" || pending?.action === "turn_on" ? { line: pending.action === "resume" ? "Resuming…" : "Turning on…", actions: ["run", "pause"] as ProjectAgentAction[], mark: <span className="spinner" aria-hidden /> }
      : { line: zoned(status.line, clock), actions: status.actions, mark: status.spinning ? <span className="spinner" aria-hidden /> : agent.state === "paused" ? <PauseMark /> : null };
  const busy = (action: ProjectAgentAction) => pending?.action === action && (action === "run" || action === "stop");
  return <div className="settings-card agent-status">
    <div className="agent-status-row">
      <p className="agent-status-line" aria-live="polite">{shown.mark}<span>{shown.line}
        {shown.actions.includes("open") && status.run != null ? <>{" · "}<Link className="link" to={`/projects/${project}/agents/${agent.id}/runs/${status.run}`}>Open</Link></> : null}
      </span></p>
      <div className="agent-status-actions">
        {shown.actions.filter((action): action is Exclude<ProjectAgentAction, "open"> => action !== "open").map((action) =>
          <button key={action} type="button" className="btn" disabled={Boolean(pending) && !(action === "pause" && pending?.action === "stop")}
            onClick={() => void press(action)}>
            {busy(action) ? <><span className="spinner" aria-hidden />{action === "run" ? "Starting…" : "Stopping…"}</> : LABELS[action]}
          </button>)}
      </div>
    </div>
    {error ? <p role="alert" className="text-meta text-danger">
      {error.action === "run" || error.action === "stop" ? error.message : <>{refusal("Not saved", error.message)}{" "}
        <button type="button" className="link" onClick={() => void press(error.action)}>Retry</button></>}
    </p> : null}
  </div>;
}

function RunRow({ project, agent, run }: { project: string; agent: string; run: ProjectAgentRun }) {
  const cost = runCost(run);
  const body = <>
    <RunDot tone={run.tone} />
    <span className="agent-run-body">
      <span className="agent-run-head"><strong>{run.outcome}</strong><span>{runWhen(run)}</span>{cost ? <span>{cost}</span> : null}</span>
      {run.line ? <span className="agent-run-line"><InlineProse text={run.line} /></span> : null}
    </span>
  </>;
  return run.page ? <Link className="settings-row agent-run" to={`/projects/${project}/agents/${agent}/runs/${run.n}`}>{body}<Chevron /></Link>
    : <div className="settings-row agent-run">{body}</div>;
}

/** Recent runs, newest first; Show older reads ten more. */
function Runs({ project, agent, more, loadingOlder, onOlder }: { project: string; agent: ProjectAgentDetail; more: boolean; loadingOlder: boolean; onOlder: () => void }) {
  return <section className="settings-section" aria-labelledby="agent-runs">
    <h2 id="agent-runs">Recent runs</h2>
    {agent.runs.length ? <div className="settings-group">{agent.runs.map((run) => <RunRow key={run.n} project={project} agent={agent.id} run={run} />)}</div>
      : <div className="settings-card"><p className="text-muted">No runs yet.</p></div>}
    {loadingOlder ? <StatusMark label="Loading older runs…" />
      : more ? <button type="button" className="link agents-link" onClick={onOlder}>Show older</button> : null}
  </section>;
}

type Retiring = { state: "idle" | "retiring" } | { state: "refused"; message: string };

/** Retire: Cancel first and focused; Retiring… holds both buttons until the agent reads as retired. */
function RetireDialog({ name, retiring, onRetire, onClose }: { name: string; retiring: Retiring; onRetire: () => void; onClose: () => void }) {
  const busy = retiring.state === "retiring";
  return <Overlay label={`Retire ${name}?`} side="center" onClose={() => { if (!busy) onClose(); }}>
    <div className="remove-dialog">
      <h2>Retire {name}?</h2>
      <p>It won’t run again, from its triggers or Run now. Its past runs stay readable under Retired.</p>
      <div className="remove-actions">
        <button type="button" className="btn" data-autofocus disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-danger" disabled={busy} onClick={onRetire}>
          {busy ? <><span className="spinner" aria-hidden />Retiring…</> : "Retire"}
        </button>
      </div>
      {retiring.state === "refused" ? <p role="alert" className="text-danger">{refusal("Not retired", retiring.message)}</p> : null}
    </div>
  </Overlay>;
}

export default function ProjectAgentPage() {
  const { name = "", id = "" } = useParams();
  const [count, setCount] = useState(10);
  const agent = useProjectAgent(name, id, count);
  const clock = useProjectAgents(name).data?.clock;
  const [dialog, setDialog] = useState(false);
  const [retiring, setRetiring] = useState<Retiring>({ state: "idle" });
  const data = agent.data;
  const retired = data?.state === "retired";
  useEffect(() => {
    if (retired && retiring.state === "retiring") { setDialog(false); setRetiring({ state: "idle" }); }
  }, [retired, retiring.state]);
  const retire = async () => {
    setRetiring({ state: "retiring" });
    try {
      await sendProjectAgent(name, "retire", { agent: id });
      void agent.refetch();
    } catch (failure) {
      setRetiring({ state: "refused", message: (failure as Error).message });
    }
  };
  return <AgentFrame back={{ to: `/projects/${name}/agents`, label: name }} title={data?.name ?? ""}>
    {agent.isPending ? <div className="settings-card"><StatusMark label="Loading this agent…" /></div>
      : !data ? <div className="settings-card" role="alert"><p>Could not load this agent.</p>
        <button type="button" className="btn agents-retry" onClick={() => void agent.refetch()}>Retry</button></div>
        : <ProseScope project={name}>
          {retired ? <p className="text-muted agent-retired-line">{data.status.line}</p> : <StatusCard project={name} agent={data} clock={clock} onRead={() => void agent.refetch()} />}
          <Runs project={name} agent={data} more={data.more} loadingOlder={agent.isPlaceholderData} onOlder={() => setCount(count + 10)} />
          <section className="settings-section" aria-labelledby="agent-what">
            <h2 id="agent-what">What it does</h2>
            <div className="settings-card agent-what">
              <ClampedBrief text={data.definition.brief} />
              <p className="text-meta text-muted">{zoned(data.definition_line, clock)}</p>
              {retired ? null : <Link className="btn agent-edit" to={`/projects/${name}/agents/${id}/edit`}>Edit</Link>}
            </div>
          </section>
          {retired ? null : <section className="settings-section" aria-labelledby="agent-retire">
            <h2 id="agent-retire">Retire agent</h2>
            <div className="settings-group"><div className="settings-row agent-retire">
              <span className="text-muted">It won’t run again. Past runs stay readable.</span>
              <button type="button" className="btn btn-outline-danger" aria-haspopup="dialog" onClick={() => { setRetiring({ state: "idle" }); setDialog(true); }}>Retire…</button>
            </div></div>
          </section>}
        </ProseScope>}
    {dialog && data ? <RetireDialog name={data.name} retiring={retiring} onRetire={() => void retire()} onClose={() => setDialog(false)} /> : null}
  </AgentFrame>;
}
