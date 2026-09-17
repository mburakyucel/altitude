import { useEffect, useRef, useState } from "react";
import type { TaskView } from "../data/api";

export function elapsed(at: string | null | undefined, now: number) {
  const time = at ? Date.parse(at) : NaN;
  return Number.isFinite(time) ? Math.max(0, Math.floor((now - time) / 1000)) : null;
}
export function duration(seconds: number) {
  return seconds < 60 ? `${seconds} sec` : seconds < 3600 ? `${Math.floor(seconds / 60)} min`
    : seconds < 86_400 ? `${Math.floor(seconds / 3600)} hr` : `${Math.floor(seconds / 86_400)} d`;
}
function age(seconds: number | null) {
  return seconds == null ? "Time unavailable" : `${duration(seconds)} ago`;
}

/** The current time, re-read every five seconds so ages move while no record changes. */
export function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

export interface Cue {
  /** working: output recorded within 60 seconds; quiet: none since; idle: no evidence either way. */
  state: "working" | "quiet" | "idle";
  text: string;
}

/** The one activity cue Conversation and Live session share (SPEC.md §3.10), from the recorded output time. */
export function activityCue(activity: TaskView["activity"], now: number): Cue {
  const observation = activity?.observation;
  const quiet = elapsed(observation?.at, now);
  if (activity?.state === "unavailable") return { state: "idle", text: "Activity unavailable" };
  if (!observation) return { state: "idle", text: activity ? "No activity recorded yet" : "Reading activity…" };
  if (quiet == null) return { state: "idle", text: `${observation.label} · ${age(quiet)}` };
  if (quiet >= 60) return { state: "quiet", text: `No new activity for ${duration(quiet)}` };
  return { state: "working", text: `${observation.label} · ${age(quiet)}` };
}

/** The cue's dot: it pulses only while output is recent (never under reduced motion). */
export function CueDot({ state }: { state: Cue["state"] }) {
  return <span className="dot" data-state={state === "working" ? "running" : "idle"} data-pulse={state === "working" || undefined} aria-hidden />;
}

/** A replaceable projection of existing public output; the task conversation owns lasting replies. */
export function TaskActivity({ activity, refresh }: { activity: TaskView["activity"]; refresh: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const now = useNow();
  const last = useRef(activity);
  if (activity?.state !== "unavailable" || last.current?.generation !== activity.generation) last.current = activity;
  const unavailable = activity?.state === "unavailable";
  const known = unavailable ? last.current : activity;
  const commentary = known?.commentary;
  const observation = known?.observation;
  const cue = activityCue(activity, now);
  useEffect(() => setExpanded(false), [activity?.generation]);
  return <section className="task-activity" aria-label="L2 activity" data-expanded={expanded || undefined}>
    <div className="task-activity-heading">
      <strong>{unavailable ? "Activity unavailable" : cue.state === "quiet" ? "Last update" : "Latest from L2"}</strong>
      {commentary ? <button type="button" className="link" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : "Expand"}</button> : null}
    </div>
    {unavailable ? <p className="text-meta text-muted">{commentary ? "Last known update. " : "Could not read activity. "}<button type="button" className="link" onClick={refresh}>Retry activity</button></p> : null}
    <p className="task-activity-words">{commentary?.text ?? (activity ? "No public update yet." : "Reading activity…")}</p>
    {commentary ? <p className="task-activity-age text-meta text-muted">{age(elapsed(commentary.time_kind === "source" ? commentary.at : null, now))}</p> : null}
    <p className="task-activity-observation text-meta text-muted">
      <CueDot state={unavailable ? "idle" : cue.state} />
      {unavailable ? activityCue({ ...known!, state: "available" }, now).text : cue.text}
    </p>
  </section>;
}
