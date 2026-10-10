import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { when } from "../data/observed";
import { Prose } from "./Prose";

/*
 * The conversation's rows (SPEC.md §3.3), shared by the project conversation and the task page: the
 * operator's message is a right-aligned bubble, a reply is left-aligned prose with no bubble, a day
 * divider names the day. Hovering a row shows its time in the gutter; on the phone a long press does.
 */

export function dayLabel(at: number): string {
  const day = new Date(at);
  const today = new Date();
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).valueOf();
  const diff = Math.round((startOf(today) - startOf(day)) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function clock(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

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
 * it at 60% until the server accepts the message, then it settles in place (SPEC.md §3.6 Sending).
 */
export function MessageRow({
  at,
  mine = false,
  pending = false,
  role,
  children,
}: {
  at?: string | null;
  mine?: boolean;
  pending?: boolean;
  role?: string;
  children: ReactNode;
}) {
  const [shown, setShown] = useState(false);
  const press = useLongPress(() => setShown((v) => !v));
  const time = when(at);
  return (
    <div
      className="msg-row"
      data-role={role}
      data-mine={mine || undefined}
      data-pending={pending || undefined}
      data-time-shown={shown || undefined}
      {...press}
    >
      {children}
      {time != null ? (
        <time className="msg-time" dateTime={at ?? undefined} title={new Date(time).toLocaleString()}>
          {clock(time)}
        </time>
      ) : null}
    </div>
  );
}

/** Where the operator's message stands; ordinary messages have no delivery receipt. */
export type BubbleState = "pending" | "sending" | "queued" | "delivered" | "unconfirmed";

/**
 * The operator's message: a right-aligned bubble (`--bubble`, `--radius-bubble`, 15px). Its shape says where it
 * stands (SPEC.md §3.6, §3.10): a ring in the gutter while sending, at 60% until the server acknowledges it; an
 * outline while queued; a warning mark and one word when delivery is unconfirmed. Queued bubbles have no time
 * yet. `side` is a quiet control in the same gutter, such as a queued message's remove.
 */
export function Bubble({ text, at, state, images, side, children }: { text: string; at?: string | null; state?: BubbleState; images?: ReactNode; side?: ReactNode; children?: ReactNode }) {
  const unconfirmed = state === "unconfirmed";
  return (
    <MessageRow at={state === "queued" ? null : at} mine pending={state === "pending"}>
      <div className="bubble" data-state={state === "delivered" ? undefined : state}>
        {state === "pending" || state === "sending" ? <span className="spinner bubble-mark" role="status" aria-label="Sending" />
          : unconfirmed ? <span className="bubble-mark bubble-warning" aria-hidden="true">!</span> : side}
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
  children,
}: {
  text: string;
  at?: string | null;
  role?: string;
  children?: ReactNode;
}) {
  return (
    <MessageRow at={at} role={role}>
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
  summary,
  at,
  images = 0,
  onOpen,
  children,
}: {
  text: string;
  summary?: string | null;
  at?: string | null;
  images?: number;
  onOpen?: () => void;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <MessageRow at={at} role="l3">
      <div className="coordination" data-open={open || undefined}>
        <div className="coordination-line">
          <span className="sys-dot" aria-hidden />
          <span className="coordination-label">{summary ? `L3 · ${summary}` : "L3 messaged the L2"}{images ? ` · ${images} image${images === 1 ? "" : "s"}` : ""}</span>
          <button type="button" className="link" aria-expanded={open}
            onClick={() => { if (!open) onOpen?.(); setOpen(!open); }}>{open ? "Hide" : "Show"}</button>
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
