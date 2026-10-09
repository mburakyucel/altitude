import { useId } from "react";

/** Where a reply's Create task stands: offered, working, waiting in the queue, answered, or failed. */
export type CreateTaskState = "ready" | "busy" | "wait" | "done" | "sent" | "fail";

const PATHS = {
  plus: "M10 4v12M4 10h12",
  clock: "M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14M10 6.5V10l2.5 1.5",
  check: "M4.5 10.5 8 14l7.5-8",
  retry: "M4 10a6 6 0 1 0 1.8-4.3M4 3.5v3h3",
  x: "M6 6l8 8M14 6l-8 8",
};

const LOOK: Record<CreateTaskState, { icon: keyof typeof PATHS | null; label: string; spoken?: string }> = {
  ready: { icon: "plus", label: "Create task" },
  busy: { icon: null, label: "Create task", spoken: "working" },
  wait: { icon: "clock", label: "Create task", spoken: "waiting for L3" },
  done: { icon: "check", label: "Task created" },
  sent: { icon: "check", label: "Create task", spoken: "sent" },
  fail: { icon: "retry", label: "Retry", spoken: "Create task" },
};

function Icon({ name }: { name: keyof typeof PATHS }) {
  return (
    <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/*
 * Create task under an L3 reply that offered one (SPEC.md §3.3). The button changes in place: its icon
 * and colour say what happened, so a press adds nothing to the conversation. Only `ready` and `fail`
 * press; `wait` adds × to take the press back. The button stays focusable throughout, so focus stays put.
 */
export function CreateTask({ title, state, error, onPress, onRemove, removing }: {
  title: string;
  state: CreateTaskState;
  error?: string | null;
  onPress: () => void;
  onRemove?: () => void;
  removing?: boolean;
}) {
  const titleId = useId();
  const look = LOOK[state];
  const pressable = state === "ready" || state === "fail";
  return (
    <div className="create-task" data-state={state}>
      <span className="create-task-pill">
        <button type="button" className="create-task-button" aria-describedby={titleId} aria-disabled={!pressable || undefined}
          onClick={() => { if (pressable) onPress(); }}>
          {look.icon ? <Icon name={look.icon} /> : <span className="spinner" aria-hidden />}
          {look.label}
          {look.spoken ? <span className="visually-hidden">, {look.spoken}</span> : null}
        </button>
        {state === "wait" && onRemove ? (
          <button type="button" className="create-task-remove" aria-label="Remove" disabled={removing} onClick={onRemove}>
            <Icon name="x" />
          </button>
        ) : null}
      </span>
      <span id={titleId} className={state === "ready" ? "create-task-title" : "visually-hidden"}>{title}</span>
      {error ? <CreateTaskError message={error} /> : null}
    </div>
  );
}

export function CreateTaskError({ message }: { message: string }) {
  return <span className="create-task-note text-danger" role="alert">{message}</span>;
}
