import { useState } from "react";
import { Link, useLocation } from "react-router";
import { UnavailableAction } from "../components/UnavailableAction";
import { StatusMark } from "../components/StatusMark";
import { useRestart } from "../data/api";
import type { Restart } from "../data/api";
import { agoText, exactTime } from "../data/observed";

/** What the pending files touch, in words: the loaded backend paths, the web build inputs, or both. */
export function changedText(files: string[]): string {
  const backend = files.some((path) => ["altitude/", "bin/", "systemd/"].some((root) => path.startsWith(root)));
  const web = files.some((path) => path.startsWith("web/"));
  if (backend && web) return "the backend and the web app";
  if (backend) return "the backend";
  if (web) return "the web app";
  return "Altitude";
}

const DISMISSED = "altitude.restart.dismissed";

/** Presentation only: a new update or failure can notify again; ordinary status changes cannot. */
export function RestartBanner({ restart }: { restart: Restart | null | undefined }) {
  const location = useLocation();
  const [dismissed, setDismissed] = useState<{ update: string; failure: Restart["failed"] } | null>(() => {
    try { return JSON.parse(localStorage.getItem(DISMISSED) ?? "null"); } catch { return null; }
  });
  const update = JSON.stringify([restart?.head, restart?.since]);
  if (!restart || location.pathname === "/monitor" ||
    (dismissed?.update === update && (!restart.failed || dismissed.failure === restart.failed))) return null;
  return <div className="restart-banner" role="status" aria-label="Restart pending">
    <p className="restart-banner-summary">{restart.failed ? "Activation failed" : restart.requested_at ? <StatusMark announce={false} label="Altitude is restarting…" /> : "Update ready"}</p>
    <Link className="btn" to="/monitor" aria-label="Update details in Monitor">Details</Link>
    <button type="button" className="btn btn-ghost" aria-label="Dismiss update notice" onClick={() => {
      const next = { update, failure: restart.failed };
      setDismissed(next);
      try { localStorage.setItem(DISMISSED, JSON.stringify(next)); } catch { /* Dismiss for this page when browser storage is unavailable. */ }
    }}>×</button>
  </div>;
}

/** Monitor retains the authoritative overview status and quiet-point action after dismissal. */
export function RestartDetails({ restart }: { restart: Restart }) {
  const act = useRestart();
  const files = restart.files ?? [];
  const waiting = restart.waiting_for;
  // A hand-pressed Restart is under way from the click; an automatic one from the time altd requested it.
  const underWay = act.isPending || (!restart.failed && (act.isSuccess || Boolean(restart.requested_at)));
  const count = `${files.length} file${files.length === 1 ? "" : "s"}`;
  const landed = restart.since ? agoText(restart.since) : "";

  return <div className="card monitor-card restart-details" role="status" aria-label="Update status">
      <div className="restart-banner-text">
        <p className="restart-banner-what">
          Merged changes to {changedText(files)} are waiting to activate.
          <span className="restart-banner-meta" title={exactTime(restart.since)}>
            {" "}
            {count}
            {landed ? `, landed ${landed}` : ""}
          </span>
        </p>
        <p className="restart-banner-rule">
          {underWay
            ? <StatusMark announce={false} label="Altitude is restarting…" />
            : restart.failed
              ? `Automatic activation did not complete; L3 has the fault.${waiting.length ? ` Waiting for ${waiting.join(", ")}.` : ""}`
              : null}
          {!underWay && !restart.failed && waiting.length > 0 ? <UnavailableAction label="Restart" reason={`Altitude restarts at the next quiet moment. Waiting for ${waiting.join(", ")}.`} /> : null}
        </p>
      </div>
      {!underWay && waiting.length === 0 ? (
        <button type="button" className="btn btn-primary restart-banner-button" onClick={() => act.mutate()}>
          Restart
        </button>
      ) : null}
      {act.isError ? <p className="text-danger text-meta" role="alert">{act.error.message}</p> : null}
    </div>;
}
