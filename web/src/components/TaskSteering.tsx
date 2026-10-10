import { useEffect, useRef, useState } from "react";
import { BusyLabel } from "./StatusMark";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";

export function useTaskSteering(project: string, task: TaskView, refresh: () => Promise<unknown>) {
  const generation = task.steering?.generation ?? null;
  const currentGeneration = useRef(generation);
  currentGeneration.current = generation;
  const [local, setLocal] = useState<{ generation: string | null; state: "stopping" | "stop_unconfirmed" | "resuming" | null; error?: string } | null>(null);
  const pending = useRef<{ generation: string | null } | null>(null);
  const serverState = task.steering?.state ?? (task.state === "running" ? "running" : "idle");
  const current = local?.generation === generation ? local : null;
  const state = current?.state ?? serverState;
  useEffect(() => {
    if (local && (local.generation !== generation ||
        (serverState !== "running" && local.state?.startsWith("stop")) ||
        (serverState !== "stopped" && local.state === "resuming"))) setLocal(null);
  }, [serverState, generation, local]);
  const perform = async (action: "stop" | "resume") => {
    if (pending.current?.generation === generation) return;
    const request = { generation };
    pending.current = request;
    setLocal({ generation, state: action === "stop" ? "stopping" : "resuming" });
    try {
      await taskAction({ project, slug: task.slug, action,
        ...(action === "stop" ? { generation: task.steering?.generation ?? null } : { stop_id: task.steering?.stop_id ?? null }) });
      if (currentGeneration.current !== generation || pending.current !== request) return;
      await refresh();
    } catch (cause) {
      if (currentGeneration.current !== generation || pending.current !== request) return;
      setLocal({ generation, state: action === "stop" ? "stop_unconfirmed" : null,
        error: cause instanceof ApiError && [401, 403].includes(cause.status)
        ? `You do not have permission to ${action === "stop" ? "stop" : "continue"} this task.`
        : action === "stop" ? "The worker may still be running." : "Could not confirm continuation. Check status before trying again." });
    } finally { if (pending.current === request) pending.current = null; }
  };
  return {
    state, error: current?.error ?? task.steering?.error,
    stop: () => { if (state === "running") void perform("stop"); },
    resume: () => { if (state === "stopped") void perform("resume"); },
    recheck: async () => {
      const request = pending.current;
      try { await refresh(); if (generation === currentGeneration.current && pending.current === request) setLocal(null); }
      catch { if (generation === currentGeneration.current && pending.current === request) setLocal({ generation, state: current?.state ?? null, error: "Could not check status. Try again." }); }
    },
  };
}
export type Steering = ReturnType<typeof useTaskSteering>;

export function SteeringControls({ steering, disabled = false, escape = false }: { steering: Steering; disabled?: boolean; escape?: boolean }) {
  const { state, error } = steering;
  if (state === "idle" && !error) return null;
  const checking = state === "stop_unconfirmed" || Boolean(error);
  const pending = state === "stopping" || state === "resuming";
  return <button type="button" className="btn task-steering" disabled={pending || (!checking && disabled)}
    onClick={checking ? () => void steering.recheck() : state === "stopped" ? steering.resume : steering.stop}
    aria-keyshortcuts={escape && state === "running" ? "Escape" : undefined}>
    <BusyLabel busy={pending} label={checking ? "Check status" : state === "stopped" || state === "resuming" ? "Continue" : "Stop"} working={state === "resuming" ? "Resuming…" : "Stopping…"} />
    {escape && state === "running" && !checking ? <kbd aria-hidden>Esc</kbd> : null}
  </button>;
}

export function SteeringNotice({ steering }: { steering: Steering }) {
  const text = steering.state === "stop_unconfirmed" ? `Stop unconfirmed · ${steering.error || "The worker may still be running."}`
    : steering.error || (steering.state === "resuming" ? "Waiting to resume" : "");
  return text ? <p className="task-line" role={steering.error ? "alert" : "status"}>
    {text}
  </p> : null;
}
