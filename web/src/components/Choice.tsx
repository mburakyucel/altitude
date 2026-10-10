import { useId } from "react";
import type { ReactNode } from "react";

/** Where a pressed choice stands: offered, working, waiting to be read, read, recorded, or failed. */
export type ChoiceState = "ready" | "busy" | "wait" | "sent" | "done" | "fail";

const PATHS = {
  plus: "M10 4v12M4 10h12",
  clock: "M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14M10 6.5V10l2.5 1.5",
  check: "M4.5 10.5 8 14l7.5-8",
  retry: "M4 10a6 6 0 1 0 1.8-4.3M4 3.5v3h3",
  x: "M6 6l8 8M14 6l-8 8",
};
const STATE_ICON: Record<Exclude<ChoiceState, "ready" | "busy">, keyof typeof PATHS> = { wait: "clock", sent: "check", done: "check", fail: "retry" };

function Icon({ name }: { name: keyof typeof PATHS }) {
  return (
    <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/*
 * One pressed choice (SPEC.md §3.3 Create task, §3.8.2 Decision selections). The pill changes in place: its
 * icon and colour say what happened, so a press adds no message. With `onPress` it is a button that stays
 * focusable in every state and presses only when `ready` or `fail`; without it, it records a choice already
 * made. `wait` with `onRemove` adds × to take the choice back. `about` names what the choice answers: shown
 * beside a ready offer or under a recorded choice, and otherwise only its accessible description.
 */
export function Choice({ label, state, spoken, icon, about, aboutShown = false, note, error, primary, mine, disabled, onPress, onRemove, removing }: {
  label: string;
  state: ChoiceState;
  spoken?: string;
  icon?: keyof typeof PATHS;
  about?: string;
  aboutShown?: boolean;
  note?: ReactNode;
  error?: string | null;
  primary?: boolean;
  mine?: boolean;
  disabled?: boolean;
  onPress?: () => void;
  onRemove?: () => void;
  removing?: boolean;
}) {
  const aboutId = useId();
  const pressable = !disabled && (state === "ready" || state === "fail");
  const glyph = state === "busy" ? <span className="spinner" aria-hidden /> : state === "ready" ? icon ? <Icon name={icon} /> : null : <Icon name={STATE_ICON[state]} />;
  const name = spoken ? `${label}, ${spoken}` : undefined;
  return (
    <div className={mine ? "choice choice-mine" : "choice"} data-state={state} data-primary={primary || undefined}>
      <span className="choice-pill">
        {onPress ? (
          <button type="button" className="choice-button" aria-label={name} aria-describedby={about ? aboutId : undefined}
            aria-disabled={!pressable || undefined} onClick={() => { if (pressable) onPress(); }}>
            {glyph}<span className="choice-label">{label}</span>
          </button>
        ) : (
          <span className="choice-button" aria-describedby={about ? aboutId : undefined}>
            {glyph}<span className="choice-label">{label}</span>{spoken ? <span className="visually-hidden">, {spoken}</span> : null}
          </span>
        )}
        {state === "wait" && onRemove ? (
          <button type="button" className="choice-remove" aria-label="Remove" disabled={removing} onClick={onRemove}>
            <Icon name="x" />
          </button>
        ) : null}
      </span>
      {about ? <span id={aboutId} className={aboutShown ? "choice-about" : "visually-hidden"}>{about}</span> : null}
      {note ? <span className="choice-about">{note}</span> : null}
      {error ? <ChoiceError message={error} /> : null}
    </div>
  );
}

/** A saved choice's place in its message's delivery (SPEC.md §3.8.2): never more than Altitude has observed. */
export function deliveryChoice(delivery?: string | null, recorded = false): { state: ChoiceState; spoken: string; note?: string } {
  if (recorded) return { state: "done", spoken: "decision recorded" };
  if (delivery === "sending") return { state: "busy", spoken: "sending" };
  if (delivery === "delivered") return { state: "sent", spoken: "sent" };
  if (delivery === "unconfirmed") return { state: "wait", spoken: "delivery unconfirmed", note: "Delivery unconfirmed" };
  return { state: "wait", spoken: "waiting for the L2" };
}

export function ChoiceError({ message }: { message: string }) {
  return <span className="choice-note text-danger" role="alert">{message}</span>;
}

/** Create task under an L3 reply that offered one (SPEC.md §3.3): the choice's label names its outcome. */
const CREATE_TASK: Record<ChoiceState, { label: string; spoken?: string }> = {
  ready: { label: "Create task" },
  busy: { label: "Create task", spoken: "working" },
  wait: { label: "Create task", spoken: "waiting for L3" },
  done: { label: "Task created" },
  sent: { label: "Create task", spoken: "sent" },
  fail: { label: "Retry", spoken: "Create task" },
};

export function CreateTask({ title, state, error, onPress, onRemove, removing }: {
  title: string; state: ChoiceState; error?: string | null;
  onPress: () => void; onRemove?: () => void; removing?: boolean;
}) {
  return <Choice {...CREATE_TASK[state]} state={state} icon="plus" about={title} aboutShown={state === "ready"}
    error={error} onPress={onPress} onRemove={onRemove} removing={removing} />;
}
