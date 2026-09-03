import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranscript } from "../data/api";
import type { TranscriptEvent } from "../data/api";
import { useTaskContext } from "./Task";

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
// Engine bookkeeping and the task's non-boundary events stay out of the conversation; raw mode lists them.
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

function itemText(item: Item): string {
  const e = item.event;
  return [e.summary ?? "", e.text, e.output ?? "", ...item.results.map((r) => r.text)].join("\n").toLowerCase();
}

function Time({ at }: { at: string | null | undefined }) {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.valueOf())) return null;
  return (
    <time className="session-time" dateTime={at} title={date.toLocaleString()}>
      {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
    </time>
  );
}

/** `code` and **bold** spans. Everything else stays text: nothing in a transcript is ever parsed as HTML. */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

/** Markdown-lite prose: fenced code blocks, paragraphs split on blank lines, line breaks kept. */
function Prose({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let code: string[] | null = null;
  let para: string[] = [];
  const flush = () => {
    if (para.length > 0) {
      blocks.push(
        <p key={blocks.length}>
          {para.map((line, index) => (
            <Fragment key={index}>
              {index > 0 ? "\n" : null}
              {inline(line)}
            </Fragment>
          ))}
        </p>,
      );
    }
    para = [];
  };
  for (const line of text.split("\n")) {
    if (line.trimStart().startsWith("```")) {
      if (code) {
        blocks.push(
          <pre key={blocks.length} className="session-code">
            {code.join("\n")}
          </pre>,
        );
        code = null;
      } else {
        flush();
        code = [];
      }
      continue;
    }
    if (code) code.push(line);
    else if (line.trim() === "") flush();
    else para.push(line);
  }
  if (code) {
    blocks.push(
      <pre key={blocks.length} className="session-code">
        {(code as string[]).join("\n")}
      </pre>,
    );
  }
  flush();
  return <div className="session-prose">{blocks}</div>;
}

/** A long prompt (the brief) opens folded to its first lines. */
function Clamped({ text, lines = 12 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const all = text.split("\n");
  if (open || all.length <= lines + 3) return <Prose text={text} />;
  return (
    <>
      <Prose text={all.slice(0, lines).join("\n")} />
      <button type="button" className="btn btn-ghost text-meta" onClick={() => setOpen(true)}>
        Show the full prompt ({all.length} lines)
      </button>
    </>
  );
}

function Message({ event, engine }: { event: TranscriptEvent; engine: string }) {
  const role = event.role ?? "assistant";
  if (role === "user") {
    return (
      <article className="session-prompt" data-role="user">
        <header className="session-head">
          <span className="session-marker">›</span>
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
        <span className="session-marker">●</span>
        <span>{engine === "codex" ? "Codex" : "Claude"}</span>
        <Time at={event.at} />
      </header>
      <Prose text={event.text} />
    </article>
  );
}

function ToolRow({ item, running }: { item: Item; running: boolean }) {
  const e = item.event;
  const summary = e.summary ?? "";
  const label =
    e.kind === "command"
      ? `$ ${summary}`
      : e.tool && e.tool !== "file_change"
        ? `${e.tool} ${summary}`.trim()
        : summary || e.type;
  const output = item.results.length > 0 ? item.results.map((r) => r.text).join("\n") : (e.output ?? "");
  const error = e.error === true || item.results.some((r) => r.error === true);
  const detail = e.text.trim() && e.text.trim() !== summary ? e.text : "";
  const done = item.results.length > 0 || e.status === "completed" || output !== "";
  const pending = !done && running;
  const hint = error ? "error" : output ? `${output.split("\n").filter(Boolean).length} lines` : pending ? "running…" : "no output";
  return (
    <details className="session-tool" data-kind={e.kind} data-error={error || undefined}>
      <summary>
        <code className="session-call">{label}</code>
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

function Conversation({ items, engine, running }: { items: Item[]; engine: string; running: boolean }) {
  return (
    <section className="session" aria-label="Live transcript">
      {items.map((item) => {
        const e = item.event;
        if (e.kind === "boundary") {
          return (
            <div key={e.seq} className="session-boundary" role="separator">
              <span>{e.text}</span>
              <Time at={e.at} />
            </div>
          );
        }
        if (e.kind === "error") {
          return (
            <p key={e.seq} className="session-error">
              {e.text}
            </p>
          );
        }
        if (e.kind === "message") return <Message key={e.seq} event={e} engine={engine} />;
        if (e.kind === "result") {
          // an output whose call is not in view (the tab opened mid-turn): still readable, still folded
          return <ToolRow key={e.seq} item={{ event: { ...e, summary: "output", tool: null }, results: [e] }} running={running} />;
        }
        return <ToolRow key={e.seq} item={item} running={running} />;
      })}
    </section>
  );
}

function RawList({ rows }: { rows: TranscriptEvent[] }) {
  return (
    <section className="space-y-2" aria-label="Raw records">
      {rows.map((event) => (
        <article key={event.seq} className="card">
          <p className="text-meta text-muted">
            {event.at ?? "—"} · {event.source} · {event.kind} · {event.type}
            {event.role ? ` · ${event.role}` : ""}
          </p>
          {event.summary ? <p className="mt-1 font-mono text-meta text-ink-2">{event.summary}</p> : null}
          {event.text ? <pre className="session-out mt-2">{event.text}</pre> : null}
          {event.output ? <pre className="session-out mt-2">{event.output}</pre> : null}
          {event.raw != null ? <pre className="session-out mt-2">{JSON.stringify(event.raw, null, 2)}</pre> : null}
        </article>
      ))}
    </section>
  );
}

/** The worker's own session (Claude's session JSONL, every turn of the Codex thread) plus Altitude's task
 *  events, read as a Claude Code window: prompts, replies, and each tool call with its output folded under
 *  it. The server derives the files from the task record; the page sends no paths. */
export default function LiveSession() {
  const { project, task } = useTaskContext();
  const [raw, setRaw] = useState(false);
  const [paused, setPaused] = useState(false);
  const [search, setSearch] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const engine = str(task["l2_engine"]) || "claude";
  const sessionId = str(task["session_id"]);
  const running = task.state === "running";
  const transcript = useTranscript(project, task.slug, engine, sessionId, raw);
  const needle = search.trim().toLowerCase();
  const items = useMemo(
    () => conversation(transcript.data?.events ?? []).filter((item) => !needle || itemText(item).includes(needle)),
    [transcript.data, needle],
  );
  const rows = useMemo(
    () =>
      (transcript.data?.events ?? []).filter(
        (event) => !needle || `${event.type} ${event.text} ${JSON.stringify(event.raw ?? "")}`.toLowerCase().includes(needle),
      ),
    [transcript.data, needle],
  );
  const count = raw ? rows.length : items.length;
  useEffect(() => {
    if (!paused) end.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [count, paused]);

  if (!sessionId) {
    return <p className="text-muted">No session yet: the live view opens with the task's first worker.</p>;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <input
          className="field min-w-40 flex-1"
          aria-label="Search transcript"
          placeholder="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="button" className="btn" onClick={() => setPaused((v) => !v)}>
          {paused ? "Follow" : "Pause"}
        </button>
        <button type="button" className="btn" onClick={() => setRaw((v) => !v)}>
          {raw ? "Conversation" : "Raw"}
        </button>
      </div>
      <p className="text-meta text-muted">{transcript.data?.redaction ?? "Loading the session…"}</p>
      {transcript.isError ? <p className="text-danger">{transcript.error.message}</p> : null}
      {raw ? <RawList rows={rows} /> : <Conversation items={items} engine={engine} running={running} />}
      <div ref={end} />
    </div>
  );
}
