import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import Composer from "../components/Composer";
import { Prose } from "../components/Prose";
import { BusyLabel, StatusMark } from "../components/StatusMark";
import { REPAIR_STATUS, RepairQuestion, moment, repairDot } from "../components/Repairs";
import { RepairButton } from "../components/RepairSheet";
import { StepRow, SysLine } from "../components/Steps";
import { ApiError, newRequest, repairRequest, useOverview, useRepair, useRepairAction } from "../data/api";
import type { Overview, Repair, RepairItem } from "../data/api";
import { dayText, modelName } from "../data/observed";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
import { scopedProject } from "../shell/projects";
import { useSelectedProject } from "../shell/scope";
import "../components/repair.css";

/*
 * One repair's page in a task's form (SPEC.md §3.15, design/wireframes/repair/PROPOSAL.md): the task header with the
 * Work crumb, state dot, title, Stop and chips for its status, model and time left; then one conversation, the brief
 * folded at its top, its steps as Live session's rows, its questions under "Your turn", the report with Altitude's
 * own check, and the composer while the session runs. A terminal repair's steps stay in that terminal.
 */

const KEEP_GOING_SECONDS = 15 * 60;

/** "4 min", "1 h 48 min": how long, without "ago". */
function span(seconds: number) {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 1) return "less than a minute";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}` : `${minutes} min`;
}

/** Ticks every 15 seconds, so time left moves between reads. */
function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, []);
  return now;
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

/** A question: under "Your turn" while it waits for you, then as its answer left it. */
function Question({ item, repair }: { item: Extract<RepairItem, { kind: "question" }>; repair: Repair }) {
  const ended = Boolean(repair.ended);
  const open = item.answer == null && !ended;
  return <div className="conversation-question" data-turn={open ? "operator" : undefined} data-historical={open ? undefined : true}>
    {open ? <p className="conversation-turn">{item.type === "step" ? "Your turn · a step for your terminal" : "Your turn · the repair asks"}</p> : null}
    <RepairQuestion repair={repair.id} question={{ ...item, kind: item.type }} ended={ended} />
  </div>;
}

function Item({ item, repair }: { item: RepairItem; repair: Repair }) {
  if (item.kind === "event") {
    if (item.event === "failed") return <SysLine at={item.at} fault>The agent stopped: {item.text}</SysLine>;
    return <SysLine at={item.at}>{EVENTS[item.event]}</SysLine>;
  }
  if (item.kind === "you") return <div className="msg-row" data-mine><div className="bubble repair-you">{item.text}</div></div>;
  if (item.kind === "say") return <div className="repair-say"><Prose text={item.text} /></div>;
  if (item.kind === "step") return <StepRow item={item} />;
  if (item.kind === "question") return <Question item={item} repair={repair} />;
  return <Report repair={{ ...item, update: repair.update, left_running: null }} />;
}

/** What happened in order, with a day divider whenever the day changes and consecutive steps kept together. */
function Timeline({ repair }: { repair: Repair }) {
  const rows: ReactNode[] = [];
  let day = "";
  let steps: ReactNode[] = [];
  const flush = () => {
    if (steps.length) rows.push(<div key={`steps-${rows.length}`} className="agent-steps">{steps}</div>);
    steps = [];
  };
  (repair.timeline ?? []).forEach((item, i) => {
    const today = item.at ? dayText(item.at) : "";
    if (today && today !== day) {
      flush();
      day = today;
      rows.push(<p key={`day-${i}`} className="day-divider">{today}</p>);
    }
    const node = <Item key={`${item.kind}:${"id" in item ? item.id : i}`} item={item} repair={repair} />;
    if (item.kind === "step") steps.push(node);
    else {
      flush();
      rows.push(node);
    }
  });
  flush();
  return <>{rows}</>;
}

/** The conversation's closing line from fifteen minutes before the end: time left or up, with Keep going. */
function KeepGoing({ repair, left }: { repair: Repair; left: number | null }) {
  const keep = useRepairAction(() => repairRequest(`/api/repairs/${repair.id}/keep-going`, {}, newRequest()));
  const ended = Boolean(repair.ended);
  const timeUp = ended && repair.end === "time";
  if (!timeUp && (ended || left == null || left > KEEP_GOING_SECONDS)) return null;
  return <p className="conversation-status repair-keep" role="status">
    <span className="dot" data-state="waiting" aria-hidden />
    <span>{timeUp ? "Time’s up · the session ended" : `${span(left ?? 0)} of full access left`}{" · "}
      <button type="button" className="link" disabled={keep.isPending} onClick={() => keep.mutate(undefined)}>
        <BusyLabel busy={keep.isPending} label="Keep going" working="Keeping going…" /></button>
      {keep.isError ? <span className="text-danger"> {keep.error.message}</span> : null}</span>
  </p>;
}

/** Stop in the task's header button, asking first; Escape asks too on desktop. A stop the job did not confirm turns it
 * into Check status, which asks the job again. */
function StopButton({ repair, escape, onStopping, voiceOwnsEscape }: {
  repair: Repair; escape: boolean; onStopping: (stopping: boolean) => void; voiceOwnsEscape: boolean;
}) {
  const { phone } = useViewport();
  const [asking, setAsking] = useState(false);
  const stop = useRepairAction(() => repairRequest(`/api/repairs/${repair.id}/stop`, {}, newRequest()));
  useEffect(() => onStopping(stop.isPending), [stop.isPending, onStopping]);
  const stopping = stop.isPending || Boolean(repair.stopping);
  const unconfirmed = Boolean(repair.stop_unconfirmed);
  const keys = escape && !unconfirmed && !stopping;
  useEffect(() => {
    if (!keys || asking || voiceOwnsEscape) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.key !== "Escape" || event.repeat || event.isComposing || event.defaultPrevented ||
          target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") ||
          document.querySelector('[role="dialog"], [role="menu"], [aria-haspopup][aria-expanded="true"], .overlay')) return;
      event.preventDefault();
      setAsking(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [keys, asking, voiceOwnsEscape]);
  return <>
    <button type="button" className="btn task-steering" disabled={stopping && !unconfirmed}
      onClick={() => unconfirmed ? stop.mutate(undefined) : setAsking(true)} aria-keyshortcuts={keys ? "Escape" : undefined}>
      {unconfirmed ? <BusyLabel busy={stop.isPending} label="Check status" working="Checking…" />
        : <BusyLabel busy={stopping} label="Stop" working="Stopping…" />}
      {keys ? <kbd aria-hidden>Esc</kbd> : null}
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

function engineText(repair: Repair, overview: Overview | undefined) {
  const engine = overview?.engines.find((row) => row.engine === repair.engine)?.label ?? repair.engine;
  return repair.model ? `${modelName(repair.model)} on ${engine}` : engine;
}

/** The end of the conversation: the report, how the session ended without one, and what comes next. */
function Ending({ repair }: { repair: Repair }) {
  if (repair.stop_unconfirmed) return <SysLine fault>Stop unconfirmed · The repair may still be running.</SysLine>;
  if (!repair.ended) return null;
  if (repair.report) return <Report repair={repair} />;
  const stopped = repair.end === "stop";
  return <>
    {stopped ? <SysLine at={repair.stop?.at}>{repair.stop?.update ? "Stopped by you · the update it started continues; its result appears here"
      : "Stopped by you · the agent and its processes were stopped"}</SysLine>
      : repair.end === "time" ? null : <SysLine fault>The repair ended without a report</SysLine>}
    {repair.left_running?.length ? <section className="repair-outcome" data-tone="neutral" aria-label="Left running"><LeftRunning jobs={repair.left_running} /></section> : null}
  </>;
}

function Messages({ repair, onEscapeOwnership }: { repair: Repair; onEscapeOwnership: (owned: boolean) => void }) {
  const [draft, setDraft] = useState("");
  const send = useRepairAction((input: { text: string; request: string }) => repairRequest(`/api/repairs/${repair.id}/message`, { text: input.text }, input.request));
  return <div className="convo-dock">
    {send.isError ? <p className="text-danger text-meta" role="alert">{send.error.message}</p> : null}
    <Composer conversation={`repair/${repair.id}`} value={draft} onChange={setDraft} placeholder="Message the repair" ariaLabel="Message the repair"
      onEscapeOwnership={onEscapeOwnership}
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

/** The brief, folded at the top as a full-width row; it opens to what the agent was given. */
function Brief({ brief }: { brief: string }) {
  return <details className="repair-brief">
    <summary><span><b>Brief</b> What you wrote, the rules, how this installation is laid out and Altitude’s records</span>
      <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </summary>
    <pre>{brief}</pre>
  </details>;
}

const BACK_ICON = <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
  <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

export default function RepairRoute() {
  const id = useParams().id ?? "";
  const { phone } = useViewport();
  const navigate = useNavigate();
  const location = useLocation();
  const overview = useOverview();
  const selected = useSelectedProject();
  const query = useRepair(id);
  const now = useNow();
  const [stopping, setStopping] = useState(false);
  const [voiceOwnsEscape, setVoiceOwnsEscape] = useState(false);
  // A repair belongs to Altitude: its crumb leads to the Work it came from or the selected project's, and to Settings
  // before any project exists.
  const from = location.state && typeof location.state === "object" ? (location.state as { project?: unknown }).project : null;
  const project = scopedProject(overview.data, typeof from === "string" ? from : selected);
  const home = project ? `/projects/${project}?tab=work` : "/settings";
  const back = () => location.key !== "default" ? navigate(-1) : navigate(home);
  const repair = query.data;
  const title = repair?.title ?? "Repair";
  const left = repair?.left == null ? null : Math.max(0, repair.left - (now - query.dataUpdatedAt) / 1000);
  const app = repair?.route === "app";
  const running = Boolean(repair && app && !repair.ended);
  const low = running && left != null && left <= KEEP_GOING_SECONDS;
  const action = repair && app && (running || repair.stop_unconfirmed)
    ? <StopButton repair={repair} escape={!phone} onStopping={setStopping} voiceOwnsEscape={voiceOwnsEscape} /> : null;
  const chips = repair ? <div className="task-chips">
    <span className="chip" data-tone={{ waiting: "held", repaired: "ok", not_repaired: "danger", no_report: "danger" }[repair.status as string]}>{REPAIR_STATUS[repair.status]}</span>
    {app ? <span className="chip">{engineText(repair, overview.data)}</span> : <span className="chip">From a terminal</span>}
    {running && left != null ? <span className="chip" data-tone={low ? "held" : undefined}>Full access · {span(left)} left</span> : null}
  </div> : null;

  let body: ReactNode;
  if (query.isPending) body = <p><StatusMark label="Loading repair…" /></p>;
  else if (query.isError && !repair) {
    body = query.error instanceof ApiError && query.error.status === 404 ? <p className="text-meta text-muted">That repair is not on this computer.</p>
      : <p role="alert" className="text-danger">Could not load this repair. <button type="button" className="link" onClick={() => void query.refetch()}>Retry</button></p>;
  } else if (repair) {
    body = <>
      {repair.brief ? <Brief brief={repair.brief} /> : null}
      {app ? <><Timeline repair={repair} /><Ending repair={repair} /><KeepGoing repair={repair} left={left} /></> : <Terminal repair={repair} />}
    </>;
  }
  const foot = repair && app ? running && !repair.stop_unconfirmed && !stopping && !repair.stopping
    ? <Messages repair={repair} onEscapeOwnership={setVoiceOwnsEscape} />
    : repair.ended && repair.report ? <div className="convo-dock repair-center"><RepairButton label="Continue repair…" continues={repair.id} /></div>
      : repair.ended && repair.end !== "stop" && repair.end !== "time" ? <div className="convo-dock repair-center"><RepairButton label="Repair again" problem={repair.problem ?? ""} /></div>
        : null : null;
  const conversation = <section className="convo" aria-label="Repair conversation">
    {query.isError && repair ? <p className="conversation-notice" role="status">Altitude is not answering · the repair continues and this page catches up when it is back</p> : null}
    <div className="convo-scroll">
      <div className="convo-col">{body}</div>
    </div>
    {foot}
  </section>;
  const dot = repair ? <span className="dot" data-state={repairDot(repair.status)} aria-hidden /> : null;

  if (phone) {
    return <>
      <header className="phone-header" data-task>
        <button type="button" className="icon-btn" aria-label="Back" onClick={back}>{BACK_ICON}</button>
        <h1 className="phone-title" aria-label={title} tabIndex={-1}>
          <span className="phone-heading">
            <span className="truncate">{title}</span>
            {repair ? <span className="phone-status" aria-live="polite"><span className="task-state-line" role="status">
              {dot}<span data-tone={repairDot(repair.status) === "danger" ? "danger" : undefined}>{REPAIR_STATUS[repair.status].replace(/^[✓✕] /, "")}</span>
              {running && left != null ? <span data-tone={low ? "held" : undefined}> · {span(left)} left</span> : null}
            </span></span> : null}
          </span>
        </h1>
        {action ?? <span className="icon-btn invisible" aria-hidden />}
      </header>
      <div className="task-page" data-phone>{conversation}</div>
    </>;
  }
  return <div className="task-page">
    <header className="task-header">
      <div className="task-crumb-row">
        <button type="button" className="task-crumb" onClick={() => navigate(home)}>‹ {project ? "Work" : "Settings"}</button>
        <h1 className="task-title" tabIndex={-1}>{dot}<span>{title}</span></h1>
        <div className="task-actions">{action}</div>
      </div>
      {chips}
    </header>
    <div className="task-body"><div className="task-main">{conversation}</div></div>
  </div>;
}

