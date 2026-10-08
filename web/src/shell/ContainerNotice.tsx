import type { ContainerLifecycle } from "../data/api";

/** Read-only recovery guidance from the existing overview poll; host administration owns Continue. */
export function ContainerNotice({ lifecycle }: { lifecycle: ContainerLifecycle | null | undefined }) {
  if (!lifecycle || lifecycle.ready) return null;
  return <div className="restart-banner" role="status" aria-label="Container work paused">
    <div>
      <p className="restart-banner-summary">New AI work is paused</p>
      <p className="text-meta">Messages and task requests stay queued. Stop remains available.</p>
      {lifecycle.continue_command ? <p className="text-meta">
        After checking recovery, run on the host: <code>{lifecycle.continue_command}</code>
      </p> : <p className="text-meta">{lifecycle.reason}</p>}
    </div>
  </div>;
}
