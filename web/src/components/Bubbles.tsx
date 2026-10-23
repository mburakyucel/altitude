import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { clockText, dayText, exactTime } from "../data/observed";
import { Prose, ProseAt } from "./Prose";

/*
 * The conversation's rows (SPEC.md §3.3), shared by the project conversation and the task page: the
 * operator's message is a right-aligned bubble, a reply is left-aligned prose with no bubble, a day
 * divider names the day. Hovering a row shows its time in the gutter; on the phone a long press does.
 */

/** A long press (touch or pen, not a mouse) reveals the row's time on the phone (SPEC.md §3.3). */
export function useLongPress(onLong: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onPointerDown: (event: ReactPointerEvent) => {
      if (event.pointerType === "mouse") return;
      clear();
      timer.current = setTimeout(onLong, 500);
    },
    onPointerUp: clear,
    onPointerCancel: clear,
    onPointerLeave: clear,
  };
}

export function DayDivider({ label }: { label: string }) {
  return (
    <div className="day-divider" role="separator">
      {label}
    </div>
  );
}

/**
 * One row with its time in the gutter. `mine` right-aligns it (the operator's bubble); `pending` shows
 * it at 60% until the server accepts the message, then it settles in place (SPEC.md §3.6 Sending);
 * `enter` fades a row in that arrives while the conversation is open.
 */
export function MessageRow({
  at,
  mine = false,
  pending = false,
  enter = false,
  role,
  children,
}: {
  at?: string | null;
  mine?: boolean;
  pending?: boolean;
  enter?: boolean;
  role?: string;
  children: ReactNode;
}) {
  const [shown, setShown] = useState(false);
  const press = useLongPress(() => setShown((v) => !v));
  const time = clockText(at);
  return (
    <div
      className="msg-row"
      data-role={role}
      data-mine={mine || undefined}
      data-pending={pending || undefined}
      data-enter={enter || undefined}
      data-time-shown={shown || undefined}
      {...press}
    >
      <ProseAt value={at}>{children}</ProseAt>
      {time ? (
        <time className="msg-time" dateTime={at ?? undefined} title={exactTime(at)}>
          {time}
        </time>
      ) : null}
    </div>
  );
}

/** Where the operator's message stands; ordinary messages have no delivery receipt. */
export type BubbleState = "pending" | "sending" | "queued" | "delivered" | "unconfirmed" | "failed";

/**
 * The operator's message: a right-aligned bubble (`--bubble`, `--radius-bubble`, 15px). Its shape says where it
 * stands (SPEC.md §3.6, §3.10), one cue each: at 60% while it is on its way (until the server, or for a claimed
 * message the session, acknowledges it); an outline while queued; a warning mark when it may not have arrived,
 * with one word when delivery is unconfirmed. Queued bubbles have no time yet. `side` is a quiet control in the same gutter, such as a queued message's remove.
 */
export function Bubble({ text, at, state, images, side, enter, children }: { text: string; at?: string | null; state?: BubbleState; images?: ReactNode; side?: ReactNode; enter?: boolean; children?: ReactNode }) {
  const unconfirmed = state === "unconfirmed";
  const failed = state === "failed";
  return (
    <MessageRow at={state === "queued" || state === "sending" ? null : at} mine pending={state === "pending" || state === "sending"} enter={enter}>
      <div className="bubble" data-state={state === "delivered" ? undefined : state}>
        {state === "pending" || state === "sending" ? <span className="sr-only" role="status" aria-label="Sending" />
          : unconfirmed || failed ? <span className="bubble-mark bubble-warning" data-tone={failed ? "danger" : undefined} aria-hidden="true">!</span> : side}
        {text}{images}
      </div>
      {state === "queued" ? <span className="sr-only">Queued</span> : null}
      {state === "delivered" ? <span className="sr-only">Delivered</span> : null}
      {unconfirmed || children ? <div className="message-delivery text-meta text-muted">
        {unconfirmed ? <span className="bubble-warning-text">Unconfirmed</span> : null}{children}
      </div> : null}
    </MessageRow>
  );
}

/** The small × beside a queued bubble: it removes that one message and spins in place while removing. */
export function RemoveMessage({ message, removing, disabled, onClick }: { message: string; removing: boolean; disabled: boolean; onClick: () => void }) {
  return <button type="button" className="bubble-mark bubble-remove" aria-label={removing ? "Removing" : "Remove"} aria-busy={removing || undefined}
    aria-description={message || "Image message"}
    disabled={disabled || removing} onClick={onClick}>{removing ? <span className="spinner" aria-hidden="true" /> : <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>}</button>;
}

/** A reply: left-aligned prose, no bubble. */
export function Reply({
  text,
  at,
  role,
  enter,
  children,
}: {
  text: string;
  at?: string | null;
  role?: string;
  enter?: boolean;
  children?: ReactNode;
}) {
  return (
    <MessageRow at={at} role={role} enter={enter}>
      <div className="reply">
        <Prose text={text} />
        {children}
      </div>
    </MessageRow>
  );
}

/**
 * A message L3 sent the L2 on the task page: one compact line, "L3 · <summary>" or "L3 messaged the L2"
 * without one, whose Show opens the complete original text and images in place (SPEC.md §3.10).
 * The record keeps every word.
 */
export function Coordination({
  text,
  label,
  summary,
  at,
  images = 0,
  onOpen,
  enter,
  children,
}: {
  text: string;
  /** The folded line, when the row is not L3's message (a reopened task's earlier completion). */
  label?: string;
  summary?: string | null;
  at?: string | null;
  images?: number;
  onOpen?: () => void;
  enter?: boolean;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <MessageRow at={at} role="l3" enter={enter}>
      <div className="coordination" data-open={open || undefined}>
        <div className="coordination-line">
          <span className="sys-dot" aria-hidden />
          <span className="coordination-label">{label ?? (summary ? `L3 · ${summary}` : "L3 messaged the L2")}{images ? ` · ${images} image${images === 1 ? "" : "s"}` : ""}</span>
          {text ? <button type="button" className="link" aria-expanded={open}
            onClick={() => { if (!open) onOpen?.(); setOpen(!open); }}>{open ? "Hide" : "Show"}</button> : null}
        </div>
        {open ? <div className="coordination-body">
          <Prose text={text} />
          {children}
        </div> : null}
      </div>
    </MessageRow>
  );
}

/** The typing indicator under the operator's bubble while a chat turn runs (SPEC.md §4.2). */
export function Typing({ label = "L3 is answering" }: { label?: string }) {
  return (
    <div className="msg-row">
      <div className="typing" role="status" aria-label={label}>
        <span aria-hidden />
        <span aria-hidden />
        <span aria-hidden />
      </div>
    </div>
  );
}

/** A failure line on a message: "Not sent: <reason>." in the server's own words, or the bare outcome. */
export function notDone(what: string, reason?: string): string {
  const words = reason?.trim().replace(/\.+$/, "");
  return words ? `${what}: ${words.charAt(0).toLowerCase()}${words.slice(1)}.` : `${what}.`;
}

/**
 * Entrance (SPEC.md §3.6): a row that first appears while this view is open fades in once. Whatever the view
 * opened with appears at once, and so does a row already known under another of its ids: a message moving
 * between the outbox, the queue and the history, or a reply passing from its stream to its stored copy.
 */
export function useEntrance(ready: boolean) {
  const state = useRef({ opened: false, known: new Set<string>(), decided: new Map<string, boolean>() });
  useEffect(() => { if (ready) state.current.opened = true; });
  return useCallback((key: string, ...ids: (string | null | undefined)[]) => {
    const { opened, known, decided } = state.current;
    const named = [key, ...ids].filter((id): id is string => Boolean(id));
    let entering = decided.get(key);
    if (entering === undefined) {
      entering = opened && !named.some((id) => known.has(id));
      decided.set(key, entering);
    }
    for (const id of named) known.add(id);
    return entering;
  }, []);
}
