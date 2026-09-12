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
 * it at 60% until the server accepts the message (SPEC.md §3.6 Sending).
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

/** The operator's message: a right-aligned bubble (`--bubble`, `--radius-bubble`, 15px). */
export function Bubble({ text, at, pending = false, receipt, children }: { text: string; at?: string | null; pending?: boolean; receipt?: string; children?: ReactNode }) {
  return (
    <MessageRow at={at} mine pending={pending}>
      <div className="bubble">{text}</div>
      {receipt || children ? <div className="message-delivery text-meta text-muted">
        {receipt ? <span>{receipt}</span> : null}{children}
      </div> : null}
    </MessageRow>
  );
}

/** A reply: left-aligned prose, no bubble; `from` labels the L3's or the L2's replies on the task page. */
export function Reply({
  text,
  at,
  from,
  role,
  children,
}: {
  text: string;
  at?: string | null;
  from?: string;
  role?: string;
  children?: ReactNode;
}) {
  return (
    <MessageRow at={at} role={role}>
      <div className="reply">
        {from ? <span className="reply-from">{from}</span> : null}
        <Prose text={text} />
        {children}
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
