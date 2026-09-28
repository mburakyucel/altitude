import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { startUpdate } from "../data/api";
import type { Overview, Update } from "../data/api";

const DISMISSED = "altitude.update.dismissed";

/**
 * An installed copy's newer release: what's new, and Update after a confirm. The daemon runs the same
 * verified `alt update` for exactly this version; Altitude restarts into it or restores the running one.
 * Dismissing hides this version (or this failure) until a newer one appears.
 */
export function UpdateNotice({ update }: { update: Update | null | undefined }) {
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
  const key = available ? `${available.version}${failed ? ":failed" : ""}` : null;
  if (!update || (!running && (!available || dismissed === key))) return null;
  const dismiss = <button type="button" className="btn btn-ghost" aria-label="Dismiss new version notice" onClick={() => {
    setDismissed(key);
    try { localStorage.setItem(DISMISSED, key ?? ""); } catch { /* Dismiss for this page when browser storage is unavailable. */ }
  }}>×</button>;

  if (running) return <div className="restart-banner update-notice" role="status" aria-label="New version">
    <p className="restart-banner-summary">Installing Altitude {running}… Altitude restarts when it is ready.</p>
  </div>;

  const version = available!.version;
  const install = <button type="button" className="btn btn-primary" disabled={start.isPending} onClick={() => start.mutate(version)}>
    {start.isPending ? "Starting…" : failed ? "Try again" : `Install ${version}`}
  </button>;
  return <div className="restart-banner update-notice" role="status" aria-label="New version">
    {failed ? <p className="restart-banner-summary">
      The update to {version} did not finish. {failed.error} Altitude {update.current} keeps running.
    </p> : confirming ? <p className="restart-banner-summary">
      Install Altitude {version}? Altitude checks the download, then restarts. If {version} does not start, {update.current} comes back.
    </p> : <p className="restart-banner-summary">
      Altitude {version} is available.{" "}
      <a href={available!.notes} target="_blank" rel="noreferrer">What’s new</a>
    </p>}
    {failed || confirming ? install
      : <button type="button" className="btn" onClick={() => { start.reset(); setConfirming(true); }}>Update</button>}
    {confirming && !failed ? <button type="button" className="btn btn-ghost" disabled={start.isPending} onClick={() => setConfirming(false)}>Cancel</button> : null}
    {!confirming ? dismiss : null}
    {start.isError ? <p className="text-danger update-notice-error" role="alert">{start.error.message}</p> : null}
  </div>;
}
