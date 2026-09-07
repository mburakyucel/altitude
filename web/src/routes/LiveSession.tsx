import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Prose } from "../components/Prose";
import { ApiError, useTranscript } from "../data/api";
import type { TaskView, TranscriptEvent } from "../data/api";

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** One line of the conversation: a prompt, a reply, a boundary, an error, or a tool call with its output
 *  nested under it. A Claude result joins the call that shares its tool_use_id. Codex reports a command
 *  twice (started, then completed with its output); the later report replaces the earlier one in place. */
interface Item {
  event: TranscriptEvent;
  results: TranscriptEvent[];
}

const CALLS = new Set(["command", "file", "tool"]);
// Engine bookkeeping and the task's non-boundary events stay out of the conversation; Raw events lists them.
const HIDDEN = new Set(["engine", "platform"]);

function conversation(events: TranscriptEvent[]): Item[] {
  const items: Item[] = [];
  const calls = new Map<string, Item>();
  for (const event of events) {
    const id = event.tool_use_id ?? "";
    const known = id ? calls.get(id) : undefined;
    if (known && event.kind === "result") {
      known.results.push(event);
      continue;
    }
    if (known && CALLS.has(event.kind)) {
      known.event = event;
      continue;
    }
    if (HIDDEN.has(event.kind) || (event.kind === "message" && event.text.trim() === "")) continue;
    const item: Item = { event, results: [] };
    if (id && CALLS.has(event.kind)) calls.set(id, item);
    items.push(item);
  }
  return items;
}

/** A subtle timestamp (SPEC.md §3.10): the time of day, the exact instant on hover. */
function Time({ at }: { at: string | null | undefined }) {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.valueOf())) return null;
  return (
    <time className="session-time" dateTime={at} title={date.toLocaleString()}>
      {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </time>
  );
}

/** `code` and **bold** spans. Everything else stays text: nothing in a transcript is ever parsed as HTML. */

/** A long prompt (the brief) opens folded to its first lines. */
function Clamped({ text, lines = 12 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const all = text.split("\n");
  if (open || all.length <= lines + 3) return <Prose text={text} />;
  return (
    <>
      <Prose text={all.slice(0, lines).join("\n")} />
      <button type="button" className="link text-meta" onClick={() => setOpen(true)}>
        Show the full prompt ({all.length} lines)
      </button>
    </>
  );
}

function Message({ event, engineLabel }: { event: TranscriptEvent; engineLabel: string }) {
  const role = event.role ?? "assistant";
  if (role === "user") {
    return (
      <article className="session-prompt" data-role="user">
        <header className="session-head">
          <span>Prompt</span>
          <Time at={event.at} />
        </header>
        <Clamped text={event.text} />
      </article>
    );
  }
  if (role === "system") {
    return (
      <p className="session-notice" data-role="system">
        {event.text}
      </p>
    );
  }
  return (
    <article className="session-reply" data-role="assistant">
      <header className="session-head">
        <span>{engineLabel}</span>
        <Time at={event.at} />
      </header>
      <Prose text={event.text} />
    </article>
  );
}

/** One compact row per tool call, its output folded under it (SPEC.md §3.10). The tool's name comes
 *  from the record; a shell command reads as `$`, an engine's file change as Edit. */
function ToolRow({ item, running }: { item: Item; running: boolean }) {
  const e = item.event;
  const tool = e.tool ?? "";
  const name = e.kind === "command" ? "$" : tool === "file_change" ? "Edit" : tool || (e.kind === "file" ? "File" : "Tool");
  const summary = e.summary || e.text.split("\n")[0] || e.type;
  const output = item.results.length > 0 ? item.results.map((r) => r.text).join("\n") : (e.output ?? "");
  const error = e.error === true || item.results.some((r) => r.error === true);
  const detail = e.text.trim() && e.text.trim() !== summary ? e.text : "";
  const done = item.results.length > 0 || e.status === "completed" || output !== "";
  const pending = !done && running;
  const hint = error ? "error" : output ? `${output.split("\n").filter(Boolean).length} lines` : pending ? "running…" : "no output";
  return (
    <details className="session-tool" data-kind={e.kind} data-error={error || undefined}>
      <summary>
        <b className="session-tool-name">{name}</b>
        <code className="session-call">{summary}</code>
        <span className="session-hint">{hint}</span>
        <Time at={e.at} />
      </summary>
      {detail ? <pre className="session-out">{detail}</pre> : null}
      {output ? (
        <pre className="session-out" data-output="true">
          {output}
        </pre>
      ) : (
        <p className="session-notice">{pending ? "Waiting for output…" : "No output."}</p>
      )}
    </details>
  );
}

function Separator({ text, at, tone }: { text: string; at?: string | null; tone?: "live" }) {
  return (
    <div className="session-boundary" role="separator" data-tone={tone}>
      <span>{text}</span>
      {at ? <Time at={at} /> : null}
    </div>
  );
}

function Conversation({ items, engineLabel, running }: { items: Item[]; engineLabel: string; running: boolean }) {
  return (
    <section className="session" aria-label="Live transcript">
      {items.map((item) => {
        const e = item.event;
        if (e.kind === "boundary") return <Separator key={e.seq} text={e.text} at={e.at} />;
        if (e.kind === "error") {
          return (
            <p key={e.seq} className="session-error">
              {e.text}
            </p>
          );
        }
        if (e.kind === "message") return <Message key={e.seq} event={e} engineLabel={engineLabel} />;
        if (e.kind === "result") {
          // an output whose call is not in view (the panel opened mid-turn): still readable, still folded
          return <ToolRow key={e.seq} item={{ event: { ...e, summary: "output", tool: null }, results: [e] }} running={running} />;
        }
        return <ToolRow key={e.seq} item={item} running={running} />;
      })}
    </section>
  );
}

/** Raw events (SPEC.md §3.10): every redacted record, engine bookkeeping included, behind its toggle. */
function RawList({ rows, redaction }: { rows: TranscriptEvent[]; redaction: string }) {
  return (
    <section className="session" aria-label="Raw events">
      <p className="session-notice">{redaction}</p>
      {rows.map((event) => (
        <article key={event.seq} className="raw-row">
          <p className="raw-meta">
            {event.at ?? "—"} · {event.source} · {event.kind} · {event.type}
            {event.role ? ` · ${event.role}` : ""}
          </p>
          {event.summary ? <p className="raw-summary">{event.summary}</p> : null}
          {event.text ? <pre className="session-out">{event.text}</pre> : null}
          {event.output ? <pre className="session-out">{event.output}</pre> : null}
          {event.raw != null ? <pre className="session-out">{JSON.stringify(event.raw, null, 2)}</pre> : null}
        </article>
      ))}
    </section>
  );
}

function Connecting() {
  return (
    <div className="session" aria-label="Connecting">
      <div className="skeleton h-14 w-full" />
      <div className="skeleton h-4 w-3/4" />
      <div className="skeleton h-8 w-full" />
      <div className="skeleton h-8 w-5/6" />
      <p className="session-notice">Connecting to the session…</p>
    </div>
  );
}

export interface LiveSessionProps {
  project: string;
  task: TaskView;
  /** The engine's display name, from the engine seam's readout; never spelled here. */
  engineLabel: string;
  /** What a queued task waits for; the line replaces the session (SPEC.md §3.10). */
  waiting?: string | null;
}

/**
 * The live session panel (SPEC.md §3.10): the worker's own session (Claude's session JSONL, every turn of
 * the Codex thread) plus Altitude's task events, read as a transcript. Tinted prompt blocks, the worker's
 * prose, one compact row per tool call with its output folded, separators at task boundaries, subtle
 * timestamps, Raw events behind a toggle. States: waiting (queued), connecting, streaming, ended,
 * unavailable. The server derives the files from the task record; the page sends no paths.
 */
export default function LiveSession({ project, task, engineLabel, waiting }: LiveSessionProps) {
  const [raw, setRaw] = useState(false);
  const [paused, setPaused] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const engine = str(task["l2_engine"]);
  const sessionId = str(task["session_id"]);
  const state = task.state ?? "";
  const running = state === "running";
  const hasSession = !waiting && Boolean(sessionId);
  const transcript = useTranscript(hasSession ? project : "", task.slug, engine, sessionId, raw, running);
  const events = useMemo(() => transcript.data?.events ?? [], [transcript.data]);
  const items = useMemo(() => conversation(events), [events]);
  const fromEngine = events.some((event) => event.source !== "platform");
  const count = raw ? events.length : items.length;

  useEffect(() => {
    const node = body.current;
    if (node && !paused) node.scrollTop = node.scrollHeight;
  }, [count, paused, raw]);

  const unavailable = <p className="live-line text-muted">No session file for this attempt</p>;
  let content: ReactNode;
  let tone: "live" | "muted" | "off" = "off";
  if (waiting) {
    content = <p className="live-line text-muted">{waiting}</p>;
  } else if (!sessionId) {
    content = unavailable;
  } else if (transcript.isPending) {
    content = <Connecting />;
    tone = "muted";
  } else if (transcript.isError) {
    content =
      transcript.error instanceof ApiError && transcript.error.status === 404 ? (
        unavailable
      ) : (
        <p className="live-line text-danger">
          Could not read the session.{" "}
          <button type="button" className="link" onClick={() => transcript.refetch()}>
            Retry
          </button>
        </p>
      );
  } else if (!fromEngine && !raw) {
    // Only Altitude's own boundaries so far: a worker that has not written its first record yet, or an
    // attempt whose session file is gone.
    content = (
      <>
        <Conversation items={items} engineLabel={engineLabel} running={running} />
        {running ? <p className="session-notice">Connecting to the session…</p> : unavailable}
      </>
    );
    tone = running ? "muted" : "off";
  } else {
    const footer = running
      ? paused
        ? "Paused · Follow to catch up"
        : "Following live · new steps appear at the bottom"
      : state === "blocked"
        ? "Session paused until the task resumes"
        : "Session ended";
    tone = running ? "live" : "off";
    content = (
      <>
        {raw ? (
          <RawList rows={events} redaction={transcript.data.redaction} />
        ) : (
          <Conversation items={items} engineLabel={engineLabel} running={running} />
        )}
        <Separator text={footer} tone={running && !paused ? "live" : undefined} />
      </>
    );
  }

  return (
    <section className="live-panel" aria-label="Live session">
      <header className="live-head">
        <h2 className="live-title">
          <span className="live-pulse" data-tone={tone} aria-hidden />
          Live session
        </h2>
        {hasSession ? (
          <div className="live-tools">
            {running ? (
              <button type="button" className="btn btn-ghost live-tool" onClick={() => setPaused((v) => !v)}>
                {paused ? "Follow" : "Pause"}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-ghost live-tool"
              aria-pressed={raw}
              title={transcript.data?.redaction}
              onClick={() => setRaw((v) => !v)}
            >
              Raw events
            </button>
          </div>
        ) : null}
      </header>
      <div className="live-body" ref={body}>
        {content}
      </div>
    </section>
  );
}
