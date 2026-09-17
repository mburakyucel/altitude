import { exactTime, stampText, when } from "../data/observed";

/** A record's own time (SPEC.md §3.10): readable, exact on hover, and explicit when the record has none. */
export function Stamp({ at, className }: { at: unknown; className: string }) {
  const text = stampText(at);
  if (!text) return <span className={className} data-unavailable="">time unavailable</span>;
  return (
    <time className={className} dateTime={typeof at === "string" ? at : new Date(when(at) ?? 0).toISOString()} title={exactTime(at)}>
      {text}
    </time>
  );
}
