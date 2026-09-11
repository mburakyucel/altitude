import { useEffect, useRef, useState } from "react";
import type { TaskView } from "../data/api";

function elapsed(at: string | null | undefined, now: number) {
  const time = at ? Date.parse(at) : NaN;
  return Number.isFinite(time) ? Math.max(0, Math.floor((now - time) / 1000)) : null;
}
function age(seconds: number | null) {
  return seconds == null ? "Time unavailable" : seconds < 60 ? `${seconds} sec ago`
    : seconds < 3600 ? `${Math.floor(seconds / 60)} min ago` : `${Math.floor(seconds / 3600)} hr ago`;
}

/** A replaceable projection of existing public output; the task conversation owns lasting replies. */
export function TaskActivity({ activity, refresh }: { activity: TaskView["activity"]; refresh: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(Date.now);
  const last = useRef(activity);
  if (activity?.state !== "unavailable" || last.current?.generation !== activity.generation) last.current = activity;
  const known = activity?.state === "unavailable" ? last.current : activity;
  const commentary = known?.commentary;
  const observation = known?.observation;
  const quiet = elapsed(observation?.at, now);
  const stale = quiet != null && quiet >= 60;
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => setExpanded(false), [activity?.generation]);
  return <section className="task-activity" aria-label="L2 activity" data-expanded={expanded || undefined}>
    <div className="task-activity-heading">
      <strong>{activity?.state === "unavailable" ? "Activity unavailable" : stale ? "Last update" : "Latest from L2"}</strong>
      {commentary ? <button type="button" className="link" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : "Expand"}</button> : null}
    </div>
    {activity?.state === "unavailable" ? <p className="text-meta text-muted">{commentary ? "Last known update. " : "Could not read activity. "}<button type="button" className="link" onClick={refresh}>Retry activity</button></p> : null}
    <p className="task-activity-words">{commentary?.text ?? (activity ? "No public update yet." : "Reading activity…")}</p>
    {commentary ? <p className="task-activity-age text-meta text-muted">{age(elapsed(commentary.time_kind === "source" ? commentary.at : null, now))}</p> : null}
    <p className="task-activity-observation text-meta text-muted">
      <span className="dot" data-state={stale || !observation?.at || activity?.state === "unavailable" ? "idle" : "running"} aria-hidden />
      {stale ? `No new activity for ${age(quiet).replace(" ago", "")}` : observation ? `${observation.label} · ${age(quiet)}` : "No activity recorded yet"}
    </p>
  </section>;
}
