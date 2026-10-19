import { Link } from "react-router";
import type { Location } from "react-router";
import { CopyButton } from "./CodeBlock";
import { RepairButton } from "./RepairSheet";
import { BusyLabel, StatusMark } from "./StatusMark";
import { newRequest, repairRequest, useRepairAction, useRepairs } from "../data/api";
import type { RepairRow, RepairStatus } from "../data/api";
import { when } from "../data/observed";
import "./repair.css";

/** Repairs in Settings → About (SPEC.md §3.15): Repair…, the recent repairs and All repairs; each opens its page. */

const RECENT = 3;

function day(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function moment(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** The status chip: what the session is doing while it runs, then the outcome of Altitude's own check. */
export function RepairChip({ status }: { status: RepairStatus }) {
  if (status === "working" || status === "checking") {
    return <span className="chip repair-busy"><span className="spinner" aria-hidden="true" />{status === "working" ? "Working" : "Checking"}</span>;
  }
  if (status === "waiting") return <span className="chip repair-waiting">Waiting for you</span>;
  if (status === "stopped") return <span className="chip">Stopped</span>;
  if (status === "repaired") return <span className="chip" data-tone="ok">✓ Repaired</span>;
  if (status === "confirm" || status === "changed_by_hand") return <span className="chip repair-confirm">Confirm yourself</span>;
  return <span className="chip" data-tone="danger">✕ Not repaired</span>;
}

function RepairLink({ row, state }: { row: RepairRow; state: Location["state"] }) {
  return <Link className="settings-row repair-row" to={`/repairs/${row.id}`} state={state}>
    <span className="repair-row-text">
      <span className="repair-row-title">{day(row.started)} · {row.title}</span>
      {row.route === "terminal" ? <small>From a terminal</small> : null}
    </span>
    <RepairChip status={row.status} />
    <span aria-hidden>›</span>
  </Link>;
}

/** The Repair rows: Repair… (or the one running), then up to three recent repairs and All repairs. */
export function RepairRows({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  const running = repairs.data?.running;
  const rows = repairs.data?.repairs ?? [];
  return <>
    <div className="settings-row repair-start">
      <span><strong>Repair</strong>{" "}<small>Something not working? A repair agent finds out why and fixes it, with full access while it works.</small></span>
      {running ? <RunningRepair id={running.id} /> : <RepairButton label="Repair…" />}
    </div>
    {repairs.isPending ? <p className="settings-row"><StatusMark label="Loading repairs…" /></p>
      : repairs.isError ? <p role="alert" className="settings-row text-danger">Could not load repairs. <button type="button" className="link" onClick={() => void repairs.refetch()}>Retry</button></p>
        : rows.slice(0, RECENT).map((row) => <RepairLink key={row.id} row={row} state={state} />)}
    {rows.length > RECENT ? <Link className="settings-row" to="/settings/repairs" state={state}>
      <span><strong>All repairs</strong>{" "}<small>{rows.length} repairs</small></span><span aria-hidden>›</span>
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

/** All repairs, newest first, in the same row format. */
export function RepairsPage({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  if (repairs.isPending) return <p><StatusMark label="Loading repairs…" /></p>;
  if (repairs.isError) return <p role="alert" className="text-danger">Could not load repairs. <button type="button" className="link" onClick={() => void repairs.refetch()}>Retry</button></p>;
  if (!repairs.data.repairs.length) return <p className="text-meta text-muted">No repairs yet. Start one with Repair… in Settings → About.</p>;
  return <div className="settings-group">
    {repairs.data.repairs.map((row) => <RepairLink key={row.id} row={row} state={state} />)}
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
 * A repair's question, on its page and in Needs you: Go ahead with a command that deletes, installs or changes
 * something outside Altitude, or the operator's own step, such as one needing their password, to run in their terminal.
 */
export function RepairQuestion({ repair, question, ended, title }: { repair: string; question: RepairAsk; ended: boolean; title?: string }) {
  const answer = useRepairAction((choice: string) => repairRequest(`/api/repairs/${repair}/answer`, { question: question.id, answer: choice }, newRequest()));
  const settled = question.answer ?? (ended ? "expired" : null);
  const chosen = answer.isPending ? answer.variables : null;
  return <article className="decision repair-question" aria-label={question.title}>
    <p className="text-meta text-muted">{title ? <>Asked by the repair · <Link className="link" to={`/repairs/${repair}`}>{title}</Link></> : "Asked by the repair"}</p>
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
