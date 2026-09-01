import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { useL2Message, useTask, useTranscript } from "../data/api";

function str(value: unknown): string { return typeof value === "string" ? value : ""; }

export default function LiveSession() {
  const { name = "", slug = "" } = useParams();
  const task = useTask(name, slug);
  const [raw, setRaw] = useState(false), [paused, setPaused] = useState(false);
  const [search, setSearch] = useState(""), [kind, setKind] = useState("all"), [message, setMessage] = useState("");
  const end = useRef<HTMLDivElement>(null), data = task.data;
  const dispatchId = str(data?.["dispatch_id"]), engine = str(data?.["l2_engine"]) || "claude";
  const sessionId = str(data?.["session_id"]), transcript = useTranscript(name, slug, dispatchId, engine, sessionId, raw);
  const send = useL2Message(name);
  const events = useMemo(() => (transcript.data?.events ?? []).filter((event) => {
    const haystack = `${event.type} ${event.text} ${JSON.stringify(event.raw ?? "")}`.toLowerCase();
    return (kind === "all" || event.kind === kind) && haystack.includes(search.toLowerCase());
  }), [transcript.data, kind, search]);
  useEffect(() => { if (!paused) end.current?.scrollIntoView({ behavior: "smooth" }); }, [events.length, paused]);
  if (task.isPending) return <p className="text-muted">Loading…</p>;
  if (task.isError) return <p className="text-danger">{task.error.message}</p>;
  return <div className="mx-auto max-w-5xl space-y-4">
    <Link className="text-meta text-muted" to={`/projects/${name}/tasks/${slug}`}>‹ Task</Link>
    <header><h1 className="text-page-title font-semibold">Live session</h1><p className="text-meta text-muted">{engine} · dispatch {dispatchId} · session {sessionId.slice(0, 8)}</p></header>
    <div className="card flex flex-wrap gap-2">
      <input className="field min-w-48 flex-1" aria-label="Search transcript" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
      <select className="field w-auto" aria-label="Filter event kind" value={kind} onChange={(e) => setKind(e.target.value)}>{['all','message','tool','command','file','engine','platform','boundary','error'].map((v) => <option key={v}>{v}</option>)}</select>
      <button className="btn" onClick={() => setPaused((v) => !v)}>{paused ? "Follow" : "Pause"}</button>
      <button className="btn" onClick={() => setRaw((v) => !v)}>{raw ? "Timeline" : "Raw events"}</button>
    </div>
    <p className="text-meta text-muted">{transcript.data?.redaction ?? "Loading observable events…"}</p>
    {transcript.isError ? <p className="text-danger">{transcript.error.message}</p> : null}
    <section className="space-y-2" aria-label="Live transcript">{events.map((event) => <article key={event.seq} className="card">
      <p className="text-meta text-muted">{event.at ?? "—"} · {event.source} · {event.kind} · {event.type}</p>
      {event.text ? <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-meta text-ink-2">{event.text}</pre> : null}
      {raw && event.raw != null ? <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-meta text-ink-2">{JSON.stringify(event.raw, null, 2)}</pre> : null}
    </article>)}<div ref={end} /></section>
    {(data?.state === "running" || data?.state === "blocked") ? <section className="card space-y-2">
      <textarea className="field" rows={3} aria-label="Steer current L2" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Steer this exact L2 generation" />
      <button className="btn btn-primary" disabled={!message.trim() || send.isPending} onClick={() => send.mutate({project:name, slug, text:message.trim(), dispatch_id:dispatchId, session_id:sessionId, engine}, {onSuccess:()=>setMessage("")})}>Send steering</button>
    </section> : null}
  </div>;
}
