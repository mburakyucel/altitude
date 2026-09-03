import { useRestart } from "../data/api";
import type { Restart } from "../data/api";

/** Shown on every page while altd runs code older than main. Altitude restarts itself at the quiet point;
 * the button restarts sooner by hand once nothing is running, and is the way back after a failed restart. */
export function RestartBanner({ restart }: { restart: Restart | null | undefined }) {
  const act = useRestart();
  if (!restart) return null;
  const files = restart.files?.length ?? 0;
  const since = restart.since ? restart.since.slice(0, 16).replace("T", " ") : "";
  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface px-3 py-2 text-body"
    >
      <span>
        Altitude runs code older than main: {files} file{files === 1 ? "" : "s"} changed
        {since ? ` since ${since}Z` : ""}.
      </span>
      {restart.waiting_for.length > 0 ? (
        <span className="text-muted">
          Altitude restarts itself once nothing is running; waiting for {restart.waiting_for.join(", ")}.
        </span>
      ) : restart.requested_at && !restart.failed ? (
        <span className="text-muted">Restarting…</span>
      ) : (
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => act.mutate()}
          disabled={act.isPending || act.isSuccess}
        >
          {act.isPending || act.isSuccess ? "Restarting…" : "Restart Altitude"}
        </button>
      )}
      {restart.failed ? <span className="text-muted">The automatic restart did not complete; L3 has the fault.</span> : null}
    </div>
  );
}
