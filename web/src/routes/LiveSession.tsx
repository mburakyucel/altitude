import { useEffect, useMemo, useRef, useState } from "react";
import { useTranscript } from "../data/api";
import { useTaskContext } from "./Task";

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const KINDS = ["all", "message", "tool", "command", "file", "engine", "platform", "boundary", "error"];

/** The worker's own session log (Claude's session JSONL, every turn of the Codex thread) plus Altitude's
 *  task events, as one timeline. The server derives the files from the task record; the page sends no paths. */
export default function LiveSession() {
  const { project, task } = useTaskContext();
  const [raw, setRaw] = useState(false);
  const [paused, setPaused] = useState(false);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const end = useRef<HTMLDivElement>(null);
  const engine = str(task["l2_engine"]) || "claude";
  const sessionId = str(task["session_id"]);
  const transcript = useTranscript(project, task.slug, engine, sessionId, raw);
  const events = useMemo(
    () =>
      (transcript.data?.events ?? []).filter((event) => {
        const haystack = `${event.type} ${event.text} ${JSON.stringify(event.raw ?? "")}`.toLowerCase();
        return (kind === "all" || event.kind === kind) && haystack.includes(search.toLowerCase());
      }),
    [transcript.data, kind, search],
  );
  useEffect(() => {
    if (!paused) end.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [events.length, paused]);

  if (!sessionId) {
    return <p className="text-muted">No session yet: the live view opens with the task's first worker.</p>;
  }
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap gap-2">
        <input
          className="field min-w-48 flex-1"
          aria-label="Search transcript"
          placeholder="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="field w-auto" aria-label="Filter event kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <button type="button" className="btn" onClick={() => setPaused((v) => !v)}>
          {paused ? "Follow" : "Pause"}
        </button>
        <button type="button" className="btn" onClick={() => setRaw((v) => !v)}>
          {raw ? "Timeline" : "Raw events"}
        </button>
      </div>
      <p className="text-meta text-muted">{transcript.data?.redaction ?? "Loading observable events…"}</p>
      {transcript.isError ? <p className="text-danger">{transcript.error.message}</p> : null}
      <section className="space-y-2" aria-label="Live transcript">
        {events.map((event) => (
          <article key={event.seq} className="card">
            <p className="text-meta text-muted">
              {event.at ?? "—"} · {event.source} · {event.kind} · {event.type}
            </p>
            {event.text ? (
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-meta text-ink-2">{event.text}</pre>
            ) : null}
            {raw && event.raw != null ? (
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-meta text-ink-2">
                {JSON.stringify(event.raw, null, 2)}
              </pre>
            ) : null}
          </article>
        ))}
        <div ref={end} />
      </section>
    </div>
  );
}
