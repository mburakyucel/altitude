import { Link } from "react-router";
import type { Location } from "react-router";
import { CopyButton } from "./CodeBlock";
import { RepairButton } from "./RepairSheet";
import { BusyLabel, StatusMark } from "./StatusMark";
import { newRequest, repairRequest, useRepairAction, useRepairs } from "../data/api";
import type { RepairRow, RepairStatus } from "../data/api";
import { agoText, when } from "../data/observed";
import "./repair.css";

/*
 * Repairs where they show (SPEC.md §3.7, §3.15): Work's Altitude repairs section, Settings → About's Repair… and
 * All repairs, each row in a task row's anatomy opening the repair's page.
 */

/** A finished repair stays in Work for three days after it ended. */
export const REPAIRS_SHOWN_MS = 72 * 3600_000;

export function moment(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** What a repair's status chip, row and phone status line say. */
export const REPAIR_STATUS: Record<RepairStatus, string> = {
  working: "Repair working", waiting: "Repair waiting for you", checking: "Checking the result", stopped: "Stopped",
  repaired: "✓ Repaired", confirm: "Confirm yourself", changed_by_hand: "Confirm yourself", not_repaired: "✕ Not repaired",
  no_report: "✕ Not repaired",
};

/** A repair's state dot, in the task dot's states. */
export function repairDot(status: RepairStatus) {
  return status === "working" || status === "checking" ? "running" : status === "waiting" ? "waiting"
    : status === "not_repaired" || status === "no_report" ? "danger" : "idle";
}

function rowMeta(row: RepairRow) {
  const terminal = row.route === "terminal";
  if (!row.ended && row.status !== "checking") {
    const doing = row.status === "waiting" ? "Repair waiting" : terminal ? "Repair working in a terminal" : "Repair working";
    return `${doing} · started ${agoText(row.started)}`;
  }
  return [REPAIR_STATUS[row.status], terminal ? "from a terminal" : "", `ended ${agoText(row.ended ?? row.started)}`].filter(Boolean).join(" · ");
}

/** One repair as a task row: its dot, title, "Your turn" while it asks, and what it is doing or how it ended. */
function RepairRowLink({ row, state }: { row: RepairRow; state?: Location["state"] }) {
  const turn = row.status === "waiting" ? "Your turn · the repair asks" : null;
  const meta = rowMeta(row);
  return <Link className="task-card" data-variant="row" to={`/repairs/${row.id}`} state={state}
    aria-label={[row.title, turn, meta].filter(Boolean).join(" · ")}>
    <span className="dot task-card-dot" data-state={repairDot(row.status)} aria-hidden />
    <span className="task-card-body">
      <span className="task-card-title">{row.title}</span>
      {turn ? <span className="task-card-attention">{turn}</span> : null}
      <span className="task-card-meta">{meta}</span>
    </span>
    <svg className="task-card-go" aria-hidden viewBox="0 0 20 20" width="16" height="16">
      <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </Link>;
}

/**
 * Work's Altitude repairs: the repair running and those that ended in the last three days, newest first. A repair
 * belongs to Altitude, so every project's Work shows the same rows; with none to show, or while the listing loads or
 * fails, the section is absent, and a failed refresh keeps the rows it has.
 */
export function WorkRepairs({ project }: { project: string }) {
  const repairs = useRepairs();
  const now = Date.now();
  const rows = (repairs.data?.repairs ?? []).filter((row) => !row.ended || now - (when(row.ended) ?? 0) < REPAIRS_SHOWN_MS);
  if (!rows.length) return null;
  return <section className="wp-section" aria-label="Altitude repairs">
    <h3 className="wp-section-title">Altitude repairs <span className="text-muted">({rows.length})</span></h3>
    <div className="wp-rows">{rows.map((row) => <RepairRowLink key={row.id} row={row} state={{ from: "work", project }} />)}</div>
  </section>;
}

/** Settings → About: Repair… (or the one running), then All repairs once there is one. */
export function RepairRows({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  const running = repairs.data?.running;
  return <>
    <div className="settings-row repair-start">
      <span><strong>Repair</strong>{" "}<small>Something not working? A repair agent finds out why and fixes it, with full access while it works.</small></span>
      {running ? <RunningRepair id={running.id} /> : <RepairButton label="Repair…" />}
    </div>
    {repairs.data?.repairs.length ? <Link className="settings-row" to="/settings/repairs" state={state}>
      <span><strong>All repairs</strong></span><span aria-hidden>›</span>
    </Link> : null}
  </>;
}

/** The repair running now: Running · Open for one from the app, without a link for one in a terminal. */
export function RunningRepair({ id, long = false }: { id: string | null; long?: boolean }) {
  return <small className="repair-busy">
    <span className="spinner" aria-hidden="true" />
    {id ? <>{long ? "Repair running" : "Running"} · <Link className="link" to={`/repairs/${id}`}>Open</Link></>
      : long ? "A repair is running in a terminal on this computer" : "Running in a terminal"}
  </small>;
}

/** All repairs, newest first, in Work's rows. */
export function RepairsPage({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  if (repairs.isPending) return <p><StatusMark label="Loading repairs…" /></p>;
  if (repairs.isError) return <p role="alert" className="text-danger">Could not load repairs. <button type="button" className="link" onClick={() => void repairs.refetch()}>Retry</button></p>;
  if (!repairs.data.repairs.length) return <p className="text-meta text-muted">No repairs yet. Start one with Repair… in Settings → About.</p>;
  return <div className="wp-rows">
    {repairs.data.repairs.map((row) => <RepairRowLink key={row.id} row={row} state={state} />)}
  </div>;
}

export interface RepairAsk {
  id: string;
  kind: "ask" | "step";
  title: string;
  reason: string | null;
  command: string;
  answer: "yes" | "no" | "ran" | "expired" | null;
}

const ANSWERED = { yes: "Went ahead · by you", no: "Not done · declined by you", ran: "Done · you ran it", expired: "Expired · the session ended" } as const;

/**
 * A repair's question, on its page under "Your turn" and in Needs you, which names the repair: Go ahead with a command that deletes, installs or changes
 * something outside Altitude, or the operator's own step, such as one needing their password, to run in their terminal.
 */
export function RepairQuestion({ repair, question, ended, title }: { repair: string; question: RepairAsk; ended: boolean; title?: string }) {
  const answer = useRepairAction((choice: string) => repairRequest(`/api/repairs/${repair}/answer`, { question: question.id, answer: choice }, newRequest()));
  const settled = question.answer ?? (ended ? "expired" : null);
  const chosen = answer.isPending ? answer.variables : null;
  return <article className="decision repair-question" aria-label={question.title}>
    {title ? <p className="text-meta text-muted">Asked by the repair · <Link className="link" to={`/repairs/${repair}`}>{title}</Link></p> : null}
    <h3>{question.title}</h3>
    {question.reason ? <p>{question.reason}</p> : null}
    <pre className="repair-command"><code>{question.command}</code></pre>
    {settled ? <p className="text-meta text-muted">{ANSWERED[settled]}</p>
      : <div className="repair-question-buttons">
        {question.kind === "step" ? <>
          <CopyButton text={question.command} />
          <button type="button" className="btn btn-primary" disabled={answer.isPending} onClick={() => answer.mutate("ran")}>
            <BusyLabel busy={chosen === "ran"} label="I ran it" working="Sending…" /></button>
        </> : <>
          <button type="button" className="btn btn-primary" disabled={answer.isPending} onClick={() => answer.mutate("yes")}>
            <BusyLabel busy={chosen === "yes"} label="Go ahead" working="Sending…" /></button>
          <button type="button" className="btn" disabled={answer.isPending} onClick={() => answer.mutate("no")}>
            <BusyLabel busy={chosen === "no"} label="Don’t" working="Sending…" /></button>
        </>}
      </div>}
    {answer.isError ? <p className="text-danger text-meta" role="alert">{answer.error.message}</p> : null}
  </article>;
}
