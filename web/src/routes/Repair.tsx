import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import Composer from "../components/Composer";
import { Prose } from "../components/Prose";
import { BusyLabel, StatusMark } from "../components/StatusMark";
import { RepairChip, RepairQuestion, moment } from "../components/Repairs";
import { RepairButton } from "../components/RepairSheet";
import { ApiError, newRequest, repairRequest, useOverview, useRepair, useRepairAction } from "../data/api";
import type { Overview, Repair, RepairItem } from "../data/api";
import { modelName, when } from "../data/observed";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
import "../components/repair.css";

/*
 * One repair's page (SPEC.md §3.15, design/wireframes/repair/PROPOSAL.md): header with its status and Stop, the
 * full-access line, what happened in order, the report with Altitude's own check, and the composer while the session
 * runs. A repair belongs to no project, so no tab is lit. A terminal repair's steps stay in that terminal.
 */

const KEEP_GOING_SECONDS = 15 * 60;

/** "4 min", "1 h 48 min": how long, without "ago". */
function span(seconds: number) {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 1) return "less than a minute";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}` : `${minutes} min`;
}

/** Ticks every 15 seconds, so ages and time left move between reads. */
function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function SysLine({ children, fault = false }: { children: ReactNode; fault?: boolean }) {
  return <div className="sys-line repair-sys">
    <span className="sys-dot" data-tone={fault ? "danger" : undefined} aria-hidden />
    <span className="sys-text">{children}</span>
  </div>;
}

const LEADS = { ran: "Ran:", failed: "Failed", changed: "Changed:", fetched: "Fetched:", running: "Running:", cut: "Cut off by the end of the session:" } as const;

/** One step on one line, cut with an ellipsis; Show opens the whole command and what it printed. */
function Step({ item }: { item: Extract<RepairItem, { kind: "step" }> }) {
  const [open, setOpen] = useState(false);
  const bad = item.verb === "failed" || item.verb === "cut";
  const lead = item.verb === "failed" ? `Failed${item.exit != null ? ` (exit ${item.exit})` : ""}:` : LEADS[item.verb];
  const more = item.detail && item.detail !== item.text || item.output;
  return <div className="repair-step" data-verb={item.verb}>
    <div className="repair-step-line">
      {item.verb === "running" ? <span className="spinner" aria-hidden /> : <span className="repair-step-dot" data-bad={bad || undefined} aria-hidden />}
      <span className="repair-step-text"><span className={bad ? "text-danger" : undefined}>{lead}</span> <code>{item.text}</code></span>
      {more ? <button type="button" className="link repair-show" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button> : null}
    </div>
    {open ? <div className="repair-step-detail">
      {item.detail && item.detail !== item.text ? <pre className="session-code">{item.detail}</pre> : null}
      {item.output ? <pre className="session-code">{item.output}</pre> : null}
    </div> : null}
  </div>;
}

function checkLine(repair: Pick<Repair, "check" | "update" | "report">) {
  const check = repair.check!;
  const parts: ReactNode[] = [];
  if (check.version) parts.push(`version ${check.version.replace(/^v/, "")}`);
  if (check.matches === true) parts.push("matches its release");
  if (check.matches === false) parts.push(<span className="text-danger">its files differ from the release</span>);
  parts.push("service running");
  if (repair.update && repair.report && check.version !== repair.update.version) {
    parts.push(<span className="text-danger">update to {repair.update.version.replace(/^v/, "")} not installed</span>);
  }
  parts.push(check.failing.length ? <span className="text-danger">failing: {check.failing.map((item) => item.name).join(", ")}</span> : "all checks pass");
  return <p className="repair-check">Altitude checked: {parts.map((part, i) => <span key={i}>{i ? " · " : ""}{part}</span>)}</p>;
}

function LeftRunning({ jobs }: { jobs: Repair["left_running"] }) {
  if (jobs == null) return null;
  return <>
    <h3>Left running</h3>
    {jobs.length ? <ul className="repair-left">{jobs.map((job) => <li key={`${job.kind}:${job.name}`}>A {job.kind} <code>{job.name}</code></li>)}</ul>
      : <p>Nothing new</p>}
  </>;
}

const HEADINGS = { repaired: "Repaired", confirm: "Confirm yourself", changed_by_hand: "Confirm yourself · the installation was changed by hand",
  not_repaired: "Not repaired", no_report: "Not repaired" } as const;

/** The report, its outcome from Altitude's check, and what was left running. */
function Report({ repair }: { repair: Pick<Repair, "report" | "check" | "outcome" | "update" | "left_running"> }) {
  const report = repair.report!;
  const outcome = repair.outcome;
  const tone = outcome === "repaired" ? "ok" : outcome === "confirm" || outcome === "changed_by_hand" ? "neutral" : outcome ? "bad" : "pending";
  return <section className="repair-outcome" data-tone={tone} aria-label="Report">
    <h2>{outcome === "repaired" ? <span aria-hidden>✓ </span> : outcome && tone === "bad" ? <span aria-hidden>✕ </span> : null}
      {outcome ? HEADINGS[outcome] : "Report"}</h2>
    <h3>What was wrong</h3><p>{report.wrong}</p>
    <h3>What changed</h3><p>{report.changed}</p>
    <h3>What remains</h3><p>{report.remains}</p>
    {report.try ? <p><b>Try:</b> {report.try}</p> : null}
    {repair.check ? checkLine(repair) : <p className="repair-check repair-busy"><span className="spinner" aria-hidden />Altitude is checking the result…</p>}
    <LeftRunning jobs={repair.left_running} />
  </section>;
}

const EVENTS: Record<string, string> = {
  started: "Started by you · full access for 2 hours", restarting: "Altitude is restarting · the repair continues",
  back: "Altitude is back", kept_going: "Kept going · one more hour",
};

function Item({ item, repair }: { item: RepairItem; repair: Repair }) {
  if (item.kind === "event") {
    if (item.event === "failed") return <SysLine fault>The agent stopped: {item.text}</SysLine>;
    return <SysLine>{EVENTS[item.event]}</SysLine>;
  }
  if (item.kind === "you") return <div className="msg-row" data-mine><div className="bubble repair-you">{item.text}</div></div>;
  if (item.kind === "say") return <div className="repair-say"><Prose text={item.text} /></div>;
  if (item.kind === "step") return <Step item={item} />;
  if (item.kind === "question") return <RepairQuestion repair={repair.id} question={{ ...item, kind: item.type }} ended={Boolean(repair.ended)} />;
  return <Report repair={{ ...item, update: repair.update, left_running: null }} />;
}

/** "Full access · 1 h 48 min left", Keep going from fifteen minutes before the end and after the time ran out. */
function AccessLine({ repair, left }: { repair: Repair; left: number | null }) {
  const keep = useRepairAction(() => repairRequest(`/api/repairs/${repair.id}/keep-going`, {}, newRequest()));
  const button = <button type="button" className="link" disabled={keep.isPending} onClick={() => keep.mutate(undefined)}>
    <BusyLabel busy={keep.isPending} label="Keep going" working="Keeping going…" /></button>;
  const ended = Boolean(repair.ended);
  const low = !ended && left != null && left <= KEEP_GOING_SECONDS;
  const timeUp = ended && repair.end === "time";
  return <p className="repair-access" data-low={low || timeUp || undefined}>
    {timeUp ? <>Time’s up · the session ended · {button}</>
      : ended ? "Session ended"
        : <>Full access · {span(left ?? 0)} left{low ? <> · {button}</> : null}</>}
    {keep.isError ? <span className="text-danger"> {keep.error.message}</span> : null}
  </p>;
}

function StopButton({ repair, onStopping }: { repair: Repair; onStopping: (stopping: boolean) => void }) {
  const { phone } = useViewport();
  const [asking, setAsking] = useState(false);
  const stop = useRepairAction(() => repairRequest(`/api/repairs/${repair.id}/stop`, {}, newRequest()));
  useEffect(() => onStopping(stop.isPending), [stop.isPending, onStopping]);
  const stopping = stop.isPending || Boolean(repair.stopping);
  return <>
    <button type="button" className="btn repair-stop" disabled={stopping} onClick={() => setAsking(true)}>
      <BusyLabel busy={stopping} label="Stop" working="Stopping…" />
    </button>
    {stop.isError ? <span className="text-danger text-meta">{stop.error.message}</span> : null}
    {asking ? <Overlay label="Stop the repair?" side={phone ? "bottom" : "center"} onClose={() => setAsking(false)}>
      <div className="repair-sheet">
        <h2>Stop the repair?</h2>
        <p>A command running now is cut off.</p>
        <div className="repair-sheet-buttons">
          <button type="button" className="btn btn-danger" data-autofocus onClick={() => { setAsking(false); stop.mutate(undefined); }}>Stop repair</button>
          <button type="button" className="btn btn-ghost" onClick={() => setAsking(false)}>Keep running</button>
        </div>
      </div>
    </Overlay> : null}
  </>;
}

/** Stop unconfirmed: the job may still run; Check status asks again. */
function Unconfirmed({ repair }: { repair: Repair }) {
  const check = useRepairAction(() => repairRequest(`/api/repairs/${repair.id}/stop`, {}, newRequest()));
  return <>
    <SysLine fault>Stop unconfirmed · The repair may still be running.</SysLine>
    <div className="repair-center">
      <button type="button" className="btn" disabled={check.isPending} onClick={() => check.mutate(undefined)}>
        <BusyLabel busy={check.isPending} label="Check status" working="Checking…" /></button>
    </div>
  </>;
}

function engineText(repair: Repair, overview: Overview | undefined) {
  const engine = overview?.engines.find((row) => row.engine === repair.engine)?.label ?? repair.engine;
  return repair.model ? `${engine} ${modelName(repair.model)}` : engine;
}

/** The end of the page: the report, how the session ended without one, and what comes next. */
function Ending({ repair }: { repair: Repair }) {
  if (repair.stop_unconfirmed) return <Unconfirmed repair={repair} />;
  if (!repair.ended) return null;
  if (repair.report) return <Report repair={repair} />;
  const stopped = repair.end === "stop";
  return <>
    {stopped ? <SysLine>{repair.stop?.update ? "Stopped by you · the update it started continues; its result appears here"
      : "Stopped by you · the agent and its processes were stopped"}</SysLine>
      : repair.end === "time" ? null : <SysLine fault>The repair ended without a report</SysLine>}
    {repair.left_running?.length ? <section className="repair-outcome" data-tone="neutral" aria-label="Left running"><LeftRunning jobs={repair.left_running} /></section> : null}
    {!stopped && repair.end !== "time" ? <div className="repair-center"><RepairButton label="Repair again" problem={repair.problem ?? ""} /></div> : null}
  </>;
}

function Messages({ repair }: { repair: Repair }) {
  const [draft, setDraft] = useState("");
  const send = useRepairAction((input: { text: string; request: string }) => repairRequest(`/api/repairs/${repair.id}/message`, { text: input.text }, input.request));
  return <div className="convo-dock repair-composer">
    {send.isError ? <p className="text-danger text-meta" role="alert">{send.error.message}</p> : null}
    <Composer conversation={`repair/${repair.id}`} value={draft} onChange={setDraft} placeholder="Message the repair" ariaLabel="Message the repair"
      onSubmit={async (text, onAccepted, _images, copy) => {
        await send.mutateAsync({ text, request: copy ?? newRequest() });
        onAccepted();
      }} />
  </div>;
}

function Terminal({ repair }: { repair: Repair }) {
  return <>
    <SysLine>Started from a terminal · {moment(repair.started)}</SysLine>
    <p className="repair-center text-meta text-muted">Its steps are in that terminal’s history.</p>
    {repair.ended ? <SysLine>Ended · {moment(repair.ended)}</SysLine> : null}
    {repair.report ? <Report repair={repair} />
      : repair.ended ? <>
        <SysLine fault>The repair ended without a report</SysLine>
        {repair.check ? checkLine(repair) : null}
      </> : null}
  </>;
}

export default function RepairRoute() {
  const id = useParams().id ?? "";
  const { phone } = useViewport();
  const navigate = useNavigate();
  const location = useLocation();
  const overview = useOverview();
  const query = useRepair(id);
  const now = useNow();
  const [stopping, setStopping] = useState(false);
  const back = () => location.key !== "default" ? navigate(-1) : navigate("/settings");
  const repair = query.data;
  const title = repair ? `Repair · ${repair.title}` : "Repair";
  const age = repair ? span(((when(repair.ended) ?? now) - (when(repair.started) ?? now)) / 1000) : "";
  const left = repair?.left == null ? null : Math.max(0, repair.left - (now - query.dataUpdatedAt) / 1000);
  const app = repair?.route === "app";
  const head = repair ? <span className="repair-head-meta">
    <RepairChip status={repair.status} />{" "}<span>{engineText(repair, overview.data)} · {age}</span>
  </span> : null;
  const stop = repair && app && !repair.ended ? <StopButton repair={repair} onStopping={setStopping} /> : null;
  let body: ReactNode;
  if (query.isPending) body = <p><StatusMark label="Loading repair…" /></p>;
  else if (query.isError && !repair) {
    body = query.error instanceof ApiError && query.error.status === 404 ? <p className="text-meta text-muted">That repair is not on this computer.</p>
      : <p role="alert" className="text-danger">Could not load this repair. <button type="button" className="link" onClick={() => void query.refetch()}>Retry</button></p>;
  } else if (repair) {
    body = <>
      {app ? <AccessLine repair={repair} left={left} /> : null}
      {query.isError ? <p className="repair-access" data-low role="status">Altitude is not answering · the repair continues and this page catches up when it is back</p> : null}
      <div className="repair-timeline">
        {app ? (repair.timeline ?? []).map((item, i) => <Item key={`${item.kind}:${"id" in item ? item.id : i}`} item={item} repair={repair} />)
          : <Terminal repair={repair} />}
        {app ? <Ending repair={repair} /> : null}
        {repair.brief ? <details className="repair-brief"><summary>Brief</summary><pre>{repair.brief}</pre></details> : null}
      </div>
    </>;
  }
  const foot = repair && app ? !repair.ended && !repair.stop_unconfirmed && !stopping && !repair.stopping ? <Messages repair={repair} />
    : repair.ended && repair.report ? <div className="convo-dock repair-composer repair-center"><RepairButton label="Continue repair…" continues={repair.id} /></div>
      : null : null;
  return <>
    {phone ? <header className="phone-header repair-header">
      <button type="button" className="btn settings-back" aria-label="Back" onClick={back}>‹</button>
      <div className="repair-head-text"><h1 tabIndex={-1}>{title}</h1>{head}</div>
      {stop}
    </header> : null}
    <div className="page repair-page">
      {!phone ? <header className="repair-head">
        <div className="repair-head-text"><h1 tabIndex={-1}>{title}</h1>{head}</div>
        {stop}
      </header> : null}
      {body}
      {repair && !app ? <Link className="link text-meta" to="/settings/repairs">All repairs</Link> : null}
    </div>
    {foot}
  </>;
}

