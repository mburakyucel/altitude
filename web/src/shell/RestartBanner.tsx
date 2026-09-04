import { useRestart } from "../data/api";
import type { Restart } from "../data/api";

/** Shown while merged backend or web changes await activation. Altitude restarts at the quiet point;
 * the button starts that guarded path sooner once nothing is running, and is the way back after a failure. */
export function RestartBanner({ restart }: { restart: Restart | null | undefined }) {
  const act = useRestart();
  if (!restart) return null;
  const files = restart.files?.length ?? 0;
  const since = restart.since ? restart.since.slice(0, 16).replace("T", " ") : "";
  const backend = restart.files?.some((path) =>
    ["altitude/", "bin/", "systemd/"].some((root) => path.startsWith(root)),
  );
  const web = restart.files?.some((path) => path.startsWith("web/"));
  const changes = backend && web ? "backend and web" : backend ? "backend" : web ? "web" : "Altitude";
  const waiting = restart.waiting_for.length > 0;
  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface px-3 py-2 text-body"
    >
      <span>
        Merged {changes} changes are waiting to activate: {files} file{files === 1 ? "" : "s"} changed
        {since ? ` since ${since}Z` : ""}.
      </span>
      {restart.failed ? (
        <span className="text-muted">
          Automatic activation did not complete; L3 has the fault.
          {waiting ? ` Retry is available once nothing is running; waiting for ${restart.waiting_for.join(", ")}.` : null}
        </span>
      ) : waiting ? (
        <span className="text-muted">
          Altitude activates them automatically once nothing is running; waiting for {restart.waiting_for.join(", ")}.
        </span>
      ) : restart.requested_at && !restart.failed ? (
        <span className="text-muted">Activating changes…</span>
      ) : null}
      {!waiting && (!restart.requested_at || restart.failed) ? (
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => act.mutate()}
          disabled={act.isPending || act.isSuccess}
        >
          {act.isPending || act.isSuccess ? "Activating…" : "Restart Altitude"}
        </button>
      ) : null}
    </div>
  );
}
