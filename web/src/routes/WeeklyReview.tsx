import { useEffect, useId, useState } from "react";
import { Link, useParams } from "react-router";
import { sendImprovement, sendWeekly, useWeeklyReview } from "../data/api";
import type { WeeklyAction, WeeklyReview, WeeklyTerms } from "../data/api";
import { refusal, runTime } from "../data/projectAgents";
import { AgentFrame, Chevron, PauseMark, RunDot, TermsSheet } from "../components/ProjectAgentParts";
import { StatusMark } from "../components/StatusMark";

/*
 * The Weekly improvement review's page in Altitude's own project (design/wireframes/project-agents/PROPOSAL.md,
 * "The Weekly improvement review"): the step-1 routine page's status card, recent runs and what it does, with the
 * terms sheet as one gate. Turn on, Resume and Run now show the sheet first while the operator's agreement is not
 * to the current terms; its Turn on agrees and completes the press. There is no Edit or Retire.
 */

const LABELS: Record<WeeklyAction, string> = { run: "Run now", pause: "Pause", resume: "Resume", turn_on: "Turn on", stop: "Stop" };

type Pending = { action: WeeklyAction; done: (review: WeeklyReview) => boolean };

/** One press on the review: the switch for Turn on, Pause and Resume, Run now, or Stop on the running review. */
async function send(project: string, action: WeeklyAction, run: string | null, terms?: string) {
  if (action === "stop") return sendImprovement(project, "stop", crypto.randomUUID().replaceAll("-", ""), run ?? undefined);
  return sendWeekly(project, action === "run" ? { action: "run" } : { action: "weekly", on: action !== "pause" }, terms);
}

/** The status card: Pause and Resume answer at once; Run now says Starting… and Stop Stopping… until read. A Run now
 * that cannot run looks unavailable and says why when pressed. */
function StatusCard({ project, review, onRead }: { project: string; review: WeeklyReview; onRead: () => void }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<{ action: WeeklyAction; message: string } | null>(null);
  const [why, setWhy] = useState(false);
  // The open sheet keeps the terms it showed: a read behind it never changes its text or the version Turn on sends.
  const [sheet, setSheet] = useState<{ action: WeeklyAction; terms: WeeklyTerms } | null>(null);
  const reason = useId();
  useEffect(() => {
    if (pending && pending.done(review)) setPending(null);
  }, [review, pending]);
  const { status } = review;
  const unavailable = status.now?.unavailable ?? null;
  const press = async (action: WeeklyAction, terms?: string) => {
    const run = status.run;
    setSheet(null);
    setError(null);
    setWhy(false);
    setPending({ action, done: () => false });
    try {
      const result = await send(project, action, run, terms);
      const started = "run" in result ? result.run : undefined;
      const done = {
        run: (read: WeeklyReview) => read.status.run === started || read.runs.some((row) => row.id === started),
        stop: (read: WeeklyReview) => read.status.run !== run,
        // A run the Monday claim or L3 started meanwhile shows instead of the switch; Pause lets it finish.
        pause: (read: WeeklyReview) => read.status.paused || read.status.run !== null,
        resume: (read: WeeklyReview) => read.agreed && !read.status.paused,
        turn_on: (read: WeeklyReview) => read.agreed && !read.status.paused,
      }[action];
      setPending({ action, done });
      onRead();
    } catch (failure) {
      setPending(null);
      setError({ action, message: (failure as Error).message });
      // A refusal can mean the terms or the run moved on: the next sheet or Retry works from a fresh read.
      onRead();
    }
  };
  const ask = (action: WeeklyAction) => {
    if (action === "run" && unavailable) return setWhy(!why);
    if (action !== "pause" && action !== "stop" && !review.agreed) return setSheet({ action, terms: review.terms });
    void press(action);
  };
  // Pause and Resume show their result at once; the server's line replaces it once read.
  const shown = pending?.action === "pause" ? { line: "Paused", actions: ["resume", "run"] as WeeklyAction[], mark: <PauseMark /> }
    : pending?.action === "resume" || pending?.action === "turn_on" ? { line: pending.action === "resume" ? "Resuming…" : "Turning on…", actions: ["run", "pause"] as WeeklyAction[], mark: <span className="spinner" aria-hidden /> }
      : { line: status.line, actions: status.actions, mark: status.spinning ? <span className="spinner" aria-hidden /> : status.paused ? <PauseMark /> : null };
  const busy = (action: WeeklyAction) => pending?.action === action && (action === "run" || action === "stop");
  const idle = !shown.actions.includes("stop");
  return <div className="settings-card agent-status">
    <div className="agent-status-row">
      <p className="agent-status-line" aria-live="polite">{shown.mark}<span>{shown.line}</span></p>
      <div className="agent-status-actions">
        {shown.actions.map((action) => {
          const off = action === "run" && unavailable !== null;
          return <button key={action} type="button" className="btn" disabled={Boolean(pending)} aria-disabled={off || undefined}
            aria-describedby={off ? reason : undefined} aria-haspopup={!off && action !== "pause" && action !== "stop" && !review.agreed ? "dialog" : undefined}
            onClick={() => ask(action)}>
            {busy(action) ? <><span className="spinner" aria-hidden />{action === "run" ? "Starting…" : "Stopping…"}</> : LABELS[action]}
          </button>;
        })}
      </div>
    </div>
    {idle && unavailable ? <p id={reason} className={why ? "text-meta text-muted" : "sr-only"}>{unavailable}</p>
      : idle && status.now?.line && !error ? <p className="text-meta text-muted">{status.now.line}</p> : null}
    {error ? <p role="alert" className="text-meta text-danger">
      {error.action === "run" ? error.message : error.action === "stop" ? "Not stopped." : <>{refusal("Not saved", error.message)}{" "}
        <button type="button" className="link" onClick={() => ask(error.action)}>Retry</button></>}
    </p> : null}
    {sheet ? <TermsSheet title={sheet.terms.title} body={sheet.terms.body} onAgree={() => void press(sheet.action, sheet.terms.version)} onClose={() => setSheet(null)} /> : null}
  </div>;
}

/** Recent runs, newest first: an ended review opens its card or line in the project's chat; a skipped week has none. */
function Runs({ project, runs }: { project: string; runs: WeeklyReview["runs"] }) {
  return <section className="settings-section" aria-labelledby="weekly-runs">
    <h2 id="weekly-runs">Recent runs</h2>
    {runs.length ? <div className="settings-group">{runs.map((run) => {
      const body = <>
        <RunDot tone={run.tone} />
        <span className="agent-run-body">
          <span className="agent-run-head"><strong>{run.outcome}</strong><span>{runTime(run.at)}</span>{run.minutes != null ? <span>{run.minutes} min</span> : null}</span>
          <span className="agent-run-line weekly-run-line">{run.line}</span>
        </span>
      </>;
      return run.id ? <Link key={run.id} className="settings-row agent-run" to={`/projects/${project}#improvement-${run.id}`}>{body}<Chevron /></Link>
        : <div key={`skipped-${run.at}`} className="settings-row agent-run">{body}</div>;
    })}</div> : <div className="settings-card"><p className="text-muted">No runs yet.</p></div>}
  </section>;
}

export default function WeeklyReviewPage() {
  const { name = "" } = useParams();
  const review = useWeeklyReview(name);
  const data = review.data;
  return <AgentFrame back={{ to: `/projects/${name}/agents`, label: name }} title="Weekly improvement review">
    {review.isPending ? <div className="settings-card"><StatusMark label="Loading Weekly improvement review…" /></div>
      : !data ? <div className="settings-card" role="alert"><p>Could not load the Weekly improvement review.</p>
        <button type="button" className="btn agents-retry" onClick={() => void review.refetch()}>Retry</button></div>
        : <>
          <StatusCard project={name} review={data} onRead={() => void review.refetch()} />
          <Runs project={name} runs={data.runs} />
          <section className="settings-section" aria-labelledby="weekly-what">
            <h2 id="weekly-what">What it does</h2>
            <div className="settings-card agent-what">
              <p>{data.what.text}</p>
              <p className="text-meta text-muted">{data.what.sent}</p>
              {data.what.past ? <p className="text-meta text-muted weekly-past">{data.what.past.line}{data.what.past.latest ? <>
                {" · "}<Link to={`/projects/${encodeURIComponent(name)}#improvement-${data.what.past.latest}`}>Latest</Link></> : null}</p> : null}
            </div>
          </section>
        </>}
  </AgentFrame>;
}
