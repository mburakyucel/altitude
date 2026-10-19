import { Link } from "react-router";
import type { Location } from "react-router";
import { StatusMark } from "./StatusMark";
import { Command } from "./Onboarding";
import { ApiError, useRepair, useRepairs } from "../data/api";
import type { Repair, RepairRow } from "../data/api";
import { when } from "../data/observed";

/** Repairs in Settings → About (SPEC.md §3.15): how to start one, the recent ones and each one's page. */

const RECENT = 3;

function day(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

function moment(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** The outcome chip: Altitude's own check once the repair has ended. */
export function RepairChip({ row, running }: { row: RepairRow; running: boolean }) {
  if (running) return <span className="chip repair-busy"><span className="spinner" aria-hidden="true" />Running</span>;
  if (!row.outcome) return <span className="chip repair-busy"><span className="spinner" aria-hidden="true" />Checking</span>;
  if (row.outcome === "repaired") return <span className="chip" data-tone="ok">✓ Repaired</span>;
  if (row.outcome === "confirm" || row.outcome === "changed_by_hand") return <span className="chip repair-confirm">Confirm yourself</span>;
  return <span className="chip" data-tone="danger">✕ Not repaired</span>;
}

function RepairLink({ row, running, state }: { row: RepairRow; running: boolean; state: Location["state"] }) {
  return <Link className="settings-row repair-row" to={`/settings/repairs/${row.id}`} state={state}>
    <span className="repair-row-text">
      <span className="repair-row-title">{day(row.started)} · {row.title}</span>
      {row.route === "terminal" ? <small>From a terminal</small> : null}
    </span>
    <RepairChip row={row} running={running} />
    <span aria-hidden>›</span>
  </Link>;
}

/** The Repair rows: what a repair is and how to start one, then up to three recent repairs and All repairs. */
export function RepairRows({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  const running = repairs.data?.running;
  const rows = repairs.data?.repairs ?? [];
  return <>
    <div className="settings-row repair-start">
      <span><strong>Repair</strong>{" "}<small>Something not working? Run this in a terminal on this computer: an agent finds out why and fixes it, with your full access while it works.</small></span>
      {running ? <small className="repair-busy"><span className="spinner" aria-hidden="true" />Running in a terminal</small> : <Command text="alt repair" />}
    </div>
    {repairs.isPending ? <p className="settings-row"><StatusMark label="Loading repairs…" /></p>
      : repairs.isError ? <p role="alert" className="settings-row text-danger">Could not load repairs. <button type="button" className="link" onClick={() => void repairs.refetch()}>Retry</button></p>
        : rows.slice(0, RECENT).map((row) => <RepairLink key={row.id} row={row} running={running?.id === row.id} state={state} />)}
    {rows.length > RECENT ? <Link className="settings-row" to="/settings/repairs" state={state}>
      <span><strong>All repairs</strong>{" "}<small>{rows.length} repairs</small></span><span aria-hidden>›</span>
    </Link> : null}
  </>;
}

/** All repairs, newest first, in the same row format. */
export function RepairsPage({ state }: { state: Location["state"] }) {
  const repairs = useRepairs();
  if (repairs.isPending) return <p><StatusMark label="Loading repairs…" /></p>;
  if (repairs.isError) return <p role="alert" className="text-danger">Could not load repairs. <button type="button" className="link" onClick={() => void repairs.refetch()}>Retry</button></p>;
  if (!repairs.data.repairs.length) return <p className="text-meta text-muted">No repairs yet. Run <code>alt repair</code> in a terminal on this computer to start one.</p>;
  return <div className="settings-group">
    {repairs.data.repairs.map((row) => <RepairLink key={row.id} row={row} running={repairs.data.running?.id === row.id} state={state} />)}
  </div>;
}

function Checked({ repair }: { repair: Repair }) {
  const check = repair.check;
  if (!repair.ended) return <p className="text-meta text-muted">Altitude checks the result once the repair ends.</p>;
  if (!check) return <p className="text-meta text-muted repair-busy"><span className="spinner" aria-hidden="true" />Altitude is checking the result…</p>;
  const parts = [check.version ? `version ${check.version.replace(/^v/, "")}` : null,
    check.matches === true ? "matches its release" : null].filter(Boolean);
  return <>
    <p className="text-meta">Altitude checked: {[...parts, check.failing.length ? null : "all checks pass"].filter(Boolean).join(" · ")}</p>
    {check.matches === false ? <p className="text-meta text-danger">The installed files differ from the release: the installation was changed by hand.</p> : null}
    {check.failing.length ? <p className="text-meta text-danger">Failing: {check.failing.map((item) => item.name).join(", ")}</p> : null}
    {repair.update && repair.report && check.version !== repair.update.version
      ? <p className="text-meta text-danger">The update to {repair.update.version.replace(/^v/, "")} is not installed.</p> : null}
  </>;
}

/** One repair's page: its outcome, report and Altitude's check; a terminal repair's steps stay in that terminal. */
export function RepairPage({ id }: { id: string }) {
  const repair = useRepair(id);
  const repairs = useRepairs();
  if (repair.isPending) return <p><StatusMark label="Loading repair…" /></p>;
  if (repair.isError && repair.error instanceof ApiError && repair.error.status === 404) return <p className="text-meta text-muted">That repair is not on this computer.</p>;
  if (repair.isError) return <p role="alert" className="text-danger">Could not load this repair. <button type="button" className="link" onClick={() => void repair.refetch()}>Retry</button></p>;
  const data = repair.data;
  const running = repairs.data?.running?.id === data.id;
  const report = data.report;
  return <div className="repair-page">
    <p className="repair-meta">
      <RepairChip row={data} running={running} />
      <small>{data.route === "terminal" ? "From a terminal · " : ""}started {moment(data.started)}{data.ended ? ` · ended ${moment(data.ended)}` : ""}</small>
    </p>
    {data.outcome === "changed_by_hand" ? <p className="text-meta">Confirm yourself · the installation was changed by hand.</p> : null}
    {report ? <section className="repair-report" aria-label="Report">
      <h2>What was wrong</h2><p>{report.wrong}</p>
      <h2>What changed</h2><p>{report.changed}</p>
      <h2>What remains</h2><p>{report.remains}</p>
      {report.try ? <p><strong>Try:</strong> {report.try}</p> : null}
    </section> : data.ended ? <p className="text-meta">The repair ended without a report.</p> : null}
    <Checked repair={data} />
    {data.route === "terminal" ? <p className="text-meta text-muted">Its steps are in that terminal’s history.</p> : null}
    {data.brief ? <details className="repair-brief"><summary>Brief</summary><pre>{data.brief}</pre></details> : null}
  </div>;
}

/** The title a repair's page shows; Repair while it loads. */
export function useRepairTitle(id: string | undefined) {
  const repair = useRepair(id);
  return id ? repair.data?.title ?? "Repair" : "Repairs";
}
