import { useState } from "react";
import { useRestart } from "../data/api";
import type { Restart } from "../data/api";
import { agoText, exactTime } from "../data/observed";
import { useViewport } from "./breakpoints";
import { Overlay } from "./Overlay";

/** What the pending files touch, in words: the loaded backend paths, the web build inputs, or both. */
export function changedText(files: string[]): string {
  const backend = files.some((path) => ["altitude/", "bin/", "systemd/"].some((root) => path.startsWith(root)));
  const web = files.some((path) => path.startsWith("web/"));
  if (backend && web) return "the backend and the web app";
  if (backend) return "the backend";
  if (web) return "the web app";
  return "Altitude";
}

/**
 * The restart banner (SPEC.md §3.13): above the header on every route while a merged change awaits
 * activation. It says what changed in words and that Altitude restarts at the next quiet moment. The
 * Restart button appears when dispatch, L3 and verification are quiet (`waiting_for` is empty), even
 * with workers running, and disappears once the
 * restart is under way; the banner leaves when the new process answers with no pending restart.
 * `restart` is `GET /api/overview`'s `restart` field, the one data source.
 */
export function RestartBanner({ restart }: { restart: Restart | null | undefined }) {
  const act = useRestart();
  const { phone } = useViewport();
  const [expanded, setExpanded] = useState(false);
  if (!restart) return null;
  const files = restart.files ?? [];
  const waiting = restart.waiting_for;
  // A hand-pressed Restart is under way from the click; an automatic one from the time altd requested it.
  const underWay = act.isPending || (!restart.failed && (act.isSuccess || Boolean(restart.requested_at)));
  const count = `${files.length} file${files.length === 1 ? "" : "s"}`;
  const landed = restart.since ? agoText(restart.since) : "";

  const details = <div className="restart-banner-text">
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
            ? "Altitude is restarting…"
            : restart.failed
              ? "Automatic activation did not complete; L3 has the fault."
              : "Altitude restarts at the next quiet moment."}
          {!underWay && waiting.length > 0 ? ` Waiting for ${waiting.join(", ")}.` : null}
        </p>
      </div>;
  return <>
    <div className="restart-banner" role="status" aria-label="Restart pending">
      {phone ? <p className="restart-banner-summary">{underWay ? "Altitude is restarting…" : restart.failed ? "Activation failed. L3 has the fault." : "Update ready"}</p> : details}
      {phone ? <button type="button" className="btn btn-ghost" aria-haspopup="dialog" aria-expanded={expanded} onClick={() => setExpanded(true)}>Details</button> : null}
      {!underWay && waiting.length === 0 ? (
        <button type="button" className="btn btn-primary restart-banner-button" onClick={() => act.mutate()}>
          Restart
        </button>
      ) : null}
      {act.isError ? <p className="text-danger text-meta" role="alert">{act.error.message}</p> : null}
    </div>
    {phone && expanded ? <Overlay label="Update details" side="bottom" onClose={() => setExpanded(false)}>
      <div className="sheet project-details">
        <div className="sheet-heading"><h2>Update ready</h2><button type="button" className="btn btn-ghost" onClick={() => setExpanded(false)}>Close details</button></div>
        {details}
      </div>
    </Overlay> : null}
  </>;
}
