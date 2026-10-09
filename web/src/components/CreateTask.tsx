import { useId } from "react";
import { ICONS } from "../shell/TabBar";

export type CreateTaskPhase = "ready" | "sending" | "checking";

/*
 * Create task under an L3 reply that offered one (SPEC.md §3.3): a quiet outlined button led by the
 * Work tab's icon, the task's title beside it, and the reason when a press was not sent. Pressing is
 * the operator's next message, so the conversation shows what happens next.
 */
export function CreateTask({ title, phase, error, onPress }: {
  title: string;
  phase: CreateTaskPhase;
  error?: string | null;
  onPress: () => void;
}) {
  const titleId = useId();
  const busy = phase !== "ready";
  return (
    <div className="create-task">
      <button type="button" className="create-task-button" aria-describedby={titleId} aria-disabled={busy || undefined}
        onClick={() => { if (!busy) onPress(); }}>
        {busy ? <span className="spinner" aria-hidden /> : (
          <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
            <path d={ICONS.work} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
        )}
        {phase === "sending" ? "Sending…" : phase === "checking" ? "Checking…" : "Create task"}
      </button>
      <span id={titleId} className="create-task-title">{title}</span>
      {phase === "checking" ? <span className="create-task-note text-muted" role="status">Couldn’t confirm it was sent. Checking the conversation…</span> : null}
      {error ? <CreateTaskError message={error} /> : null}
    </div>
  );
}

export function CreateTaskError({ message }: { message: string }) {
  return <span className="create-task-note text-danger" role="alert">{message}</span>;
}
