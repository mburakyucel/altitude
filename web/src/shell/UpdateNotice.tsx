import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { StatusMark, BusyLabel } from "../components/StatusMark";
import { RunningRepair } from "../components/Repairs";
import { RepairButton } from "../components/RepairSheet";
import { startUpdate } from "../data/api";
import type { Overview, RunningRepair as Running, Update } from "../data/api";

const DISMISSED = "altitude.update.dismissed";
/** An attempt that ended without a known cause (installation.UPDATE_FAILED). */
export const UPDATE_FAILED = "the updater stopped without saying why.";

/** "a", "a and b", "a, b and c": the work a pending automatic update waits for. */
export function waitingText(waiting: string[]) {
  return waiting.length > 1 ? `${waiting.slice(0, -1).join(", ")} and ${waiting[waiting.length - 1]}` : waiting[0] ?? "";
}

/**
 * An installed copy's pending release, installation result, or Update after a confirm. The daemon runs the same
 * verified `alt update` for exactly this version; Altitude restarts into it or restores the running one.
 * Dismissing hides this version (or this failure) until a newer one appears. A failed attempt offers Try again and
 * Repair, leading with Try again only for a cause that passes on its own at the first attempt; while a repair runs,
 * the notice links it instead.
 */
export function UpdateNotice({ update, repair }: { update: Update | null | undefined; repair?: Running | null }) {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(() => {
    try { return localStorage.getItem(DISMISSED); } catch { return null; }
  });
  const start = useMutation({
    mutationFn: startUpdate,
    onSuccess: (status) => {
      setConfirming(false);
      client.setQueryData<Overview>(["overview"], (overview) => overview && { ...overview, update: status });
    },
  });
  const attempt = update?.attempt;
  const available = update?.available;
  const running = attempt?.state === "running" ? attempt.version : null;
  const failed = attempt?.state === "failed" && attempt.version === available?.version ? attempt : null;
  const installed = !available ? update?.installed : null;
  const key = available ? `${available.version}${failed ? ":failed" : ""}` : installed ? `${installed.version}:installed` : null;
  if (!update || (!running && (!key || dismissed === key))) return null;
  const dismiss = <button type="button" className="btn btn-ghost" aria-label="Dismiss new version notice" onClick={() => {
    setDismissed(key);
    try { localStorage.setItem(DISMISSED, key ?? ""); } catch { /* Dismiss for this page when browser storage is unavailable. */ }
  }}>×</button>;

  if (running) return <div className="restart-banner update-notice" role="status" aria-label="New version">
    <p className="restart-banner-summary"><StatusMark announce={false} label={`Installing Altitude ${running}… Altitude restarts when it is ready.`} /> Altitude {running}</p>
  </div>;

  if (installed) return <div className="restart-banner update-notice" role="status" aria-label="New version">
    <p className="restart-banner-summary"><StatusMark announce={false} busy={false} label={`Updated to ${installed.version}.`} /> {installed.version}{" "}
      <a href={installed.notes} target="_blank" rel="noreferrer">What’s new</a>
    </p>
    {dismiss}
  </div>;

  if (update.automatic_pending && !failed) return <div className="restart-banner update-notice" role="status" aria-label="New version">
    <p className="restart-banner-summary">Altitude {available!.version} installs automatically at the next quiet moment.{update.waiting_for.length ? ` Waiting for ${waitingText(update.waiting_for)}.` : ""}{" "}
      <a href={available!.notes} target="_blank" rel="noreferrer">What’s new</a>
    </p>
    {dismiss}
  </div>;

  const version = available!.version;
  const error = start.isError ? <p className="text-danger update-notice-error" role="alert">{start.error.message}</p> : null;
  if (failed) {
    const cause = failed.error || UPDATE_FAILED;
    const repairFirst = !failed.passing || Boolean(failed.retry);
    const retry = <button type="button" className={repairFirst ? "btn" : "btn btn-primary"} disabled={start.isPending} onClick={() => start.mutate(version)}>
      <BusyLabel busy={start.isPending} label="Try again" working="Starting…" />
    </button>;
    const fix = <RepairButton label="Repair" className={repairFirst ? "btn btn-primary" : "btn"} problem={`The update to ${version} failed: ${cause}`} />;
    return <div className="restart-banner update-notice" role="status" aria-label="New version">
      <p className="restart-banner-summary">The update to {version} did not finish: {cause} Altitude {update.current} keeps running.</p>
      {repair ? <RunningRepair id={repair.id} long />
        : <div className="update-notice-buttons">{repairFirst ? <>{fix}{retry}</> : <>{retry}{fix}</>}</div>}
      {dismiss}
      {error}
    </div>;
  }
  const install = <button type="button" className="btn btn-primary" disabled={start.isPending} onClick={() => start.mutate(version)}>
    <BusyLabel busy={start.isPending} label={`Install ${version}`} working="Starting…" />
  </button>;
  return <div className="restart-banner update-notice" role="status" aria-label="New version">
    {confirming ? <p className="restart-banner-summary">
      Install Altitude {version}? Altitude checks the download, then restarts. If {version} does not start, {update.current} comes back.
    </p> : <p className="restart-banner-summary">
      Altitude {version} is available.{" "}
      <a href={available!.notes} target="_blank" rel="noreferrer">What’s new</a>
    </p>}
    {confirming ? install
      : <button type="button" className="btn" onClick={() => { start.reset(); setConfirming(true); }}>Update</button>}
    {confirming ? <button type="button" className="btn btn-ghost" disabled={start.isPending} onClick={() => setConfirming(false)}>Cancel</button> : null}
    {!confirming ? dismiss : null}
    {error}
  </div>;
}
