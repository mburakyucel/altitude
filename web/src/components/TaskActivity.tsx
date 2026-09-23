import { useEffect, useState } from "react";
import type { TaskView } from "../data/api";
import { Stamp } from "./Stamp";

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

/** The one activity line Conversation and Live session share (SPEC.md §3.10), from the recorded output time. */
export function activityCue(activity: TaskView["activity"], now: number): Cue {
  const observation = activity?.observation;
  const quiet = elapsed(observation?.at, now);
  if (activity?.state === "unavailable") return { state: "idle", text: "Activity unavailable" };
  if (!observation) return { state: "idle", text: activity ? "No activity recorded yet" : "Reading activity…" };
  if (quiet == null) return { state: "idle", text: "Output recorded · time unavailable" };
  if (quiet >= 60) return { state: "quiet", text: `No new activity for ${duration(quiet)}` };
  return { state: "working", text: `Working · output ${age(quiet)}` };
}

/** The dot and words of the activity line; the dot pulses only while output is recent (never under reduced motion). */
export function CueLine({ cue }: { cue: Cue }) {
  return <span className="cue-line">
    <span className="dot" data-state={cue.state === "working" ? "running" : "idle"} data-pulse={cue.state === "working" || undefined} aria-hidden />
    {cue.text}
  </span>;
}

/** The activity line over a replaceable preview of public output; the task conversation owns lasting replies. */
export function TaskActivity({ activity }: { activity: TaskView["activity"] }) {
  const [expanded, setExpanded] = useState(false);
  const now = useNow();
  const commentary = activity?.commentary;
  const cue = activityCue(activity, now);
  const age = elapsed(commentary?.time_kind === "source" ? commentary.at : null, now);
  useEffect(() => setExpanded(false), [activity?.generation]);
  if (cue.state !== "working" || !commentary?.text.trim() || age == null || age >= 60) return null;
  return <section className="task-activity" aria-label="L2 activity" data-expanded={expanded || undefined}>
    <div className="task-activity-heading text-meta">
      <CueLine cue={cue} />
      <button type="button" className="link" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : "Expand"}</button>
    </div>
    <p className="task-activity-words">{commentary.text}</p>
    <p className="task-activity-age text-meta text-muted"><Stamp at={commentary.at} className="activity-time" /></p>
  </section>;
}
