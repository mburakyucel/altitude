import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, taskAction } from "../data/api";
import type { TaskView } from "../data/api";

export function useTaskSteering(project: string, task: TaskView, refresh: () => Promise<unknown>) {
  const client = useQueryClient();
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
      void client.invalidateQueries({ queryKey: ["overview"] });
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
  return <div className="task-steering">
    {steering.state === "running" ? <button type="button" className="btn task-stop" disabled={disabled} onClick={steering.stop} aria-keyshortcuts={escape ? "Escape" : undefined}>Stop{escape ? <kbd aria-hidden>Esc</kbd> : null}</button> : null}
    {steering.state === "stopping" ? <span role="status">Stopping…</span> : null}
    {steering.state === "stopped" ? <><span role="status">Stopped</span><button type="button" className="btn btn-ghost" disabled={disabled} onClick={steering.resume}>Continue session</button></> : null}
    {steering.state === "resuming" ? <span role="status">Waiting to resume</span> : null}
    {steering.state === "stop_unconfirmed" ? <span role="status">Stop unconfirmed · The worker may still be running</span> : null}
    {steering.error ? <span className="text-danger" role="alert">{steering.error}</span> : null}
    {steering.state === "stop_unconfirmed" || steering.error ? <button type="button" className="link" onClick={() => void steering.recheck()}>Check status</button> : null}
  </div>;
}
