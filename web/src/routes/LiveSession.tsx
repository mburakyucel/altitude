import { Component, createRef, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Prose } from "../components/Prose";
import { ApiError, fetchTranscriptRecord, useTranscript } from "../data/api";
import type { TaskView, TranscriptEvent } from "../data/api";
import { SteeringControls, SteeringNotice } from "../components/TaskSteering";
import { Stamp } from "../components/Stamp";
import { CueLine, activityCue, duration, elapsed, useNow } from "../components/TaskActivity";
import type { Steering } from "../components/TaskSteering";

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** How long a call has waited for its result, moving while nothing new is recorded. */
function Running({ at }: { at: string | null | undefined }) {
  const seconds = elapsed(at, useNow());
  return <>{seconds == null ? "running…" : `running · ${duration(seconds)}`}</>;
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
          <Stamp className="session-time" at={event.at} />
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
        <Stamp className="session-time" at={event.at} />
      </header>
      <Prose text={event.text} />
    </article>
  );
}

/** One compact row per tool call, its output folded under it (SPEC.md §3.10). The tool's name comes
 *  from the record; a shell command reads as `$`, an engine's file change as Edit. */
function ToolRow({ event: e, running }: { event: TranscriptEvent; running: boolean }) {
  const tool = e.tool ?? "";
  const name = e.kind === "command" ? "$" : tool === "file_change" ? "Edit" : tool || (e.kind === "file" ? "File" : "Tool");
  const summary = e.summary || e.text.split("\n")[0] || e.type;
  const output = e.output ?? "";
  const error = e.error === true;
  const detail = e.text.trim() && e.text.trim() !== summary ? e.text : "";
  const done = e.status === "completed" || output !== "";
  const pending = !done && running;
  const hint = error ? "error" : output ? `${output.split("\n").filter(Boolean).length} lines` : pending ? <Running at={e.at} /> : "no output";
  return (
    <details className="session-tool" data-kind={e.kind} data-error={error || undefined}>
      <summary>
        <b className="session-tool-name">{name}</b>
        <code className="session-call">{summary}</code>
        <span className="session-hint">{hint}</span>
        <Stamp className="session-time" at={e.at} />
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

/** The shared activity cue under the footer (SPEC.md §3.10); its own clock keeps the transcript still. */
function LiveCue({ activity }: { activity: TaskView["activity"] }) {
  const cue = activityCue(activity, useNow());
  return (
    <p className="live-activity text-meta text-muted" role="status">
      <CueLine cue={cue} />
    </p>
  );
}

/** The header dot pulses only while a live session's cue shows recent output. */
function LivePulse({ tone, activity }: { tone: "live" | "muted" | "off"; activity: TaskView["activity"] }) {
  const working = activityCue(activity, useNow()).state === "working";
  return <span className="live-pulse" data-tone={tone === "live" && !working ? "muted" : tone} aria-hidden />;
}

function Separator({ text, at, tone }: { text: string; at?: string | null; tone?: "live" }) {
  return (
    <div className="session-boundary" role="separator" data-tone={tone}>
      <span>{text}</span>
      {at !== undefined ? <Stamp className="session-time" at={at} /> : null}
    </div>
  );
}

function Conversation({ events, engineLabel, running }: { events: TranscriptEvent[]; engineLabel: string; running: boolean }) {
  return (
    <section className="session" aria-label="Live transcript">
      {events.map((event) => (
        <div key={event.id} data-transcript-id={event.id}>
          {event.kind === "boundary" ? <Separator text={event.text} at={event.at} />
            : event.kind === "error" ? <p className="session-error">{event.text}</p>
            : event.kind === "message" ? <Message event={event} engineLabel={engineLabel} />
            : <ToolRow event={event} running={running} />}
        </div>
      ))}
    </section>
  );
}

interface RecordScope {
  project: string;
  slug: string;
  engine: string;
  sessionId: string;
  attempt: number;
}

/** Detail bytes are requested only by opening a record or explicitly asking for its next chunk. */
function RawRecord({ event, scope, active }: { event: TranscriptEvent; scope: RecordScope; active: boolean }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [offset, setOffset] = useState<number | null>(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const request = useRef<AbortController | null>(null);
  const cancel = () => { request.current?.abort(); request.current = null; setPending(false); };
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (!active) { request.current?.abort(); request.current = null; setPending(false); } }, [active]);
  const read = async () => {
    if (offset === null || request.current || !active) return;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError(false);
    try {
      const result = await fetchTranscriptRecord(scope.project, scope.slug, scope.engine, scope.sessionId, scope.attempt, event.id, offset, controller.signal);
      if (controller.signal.aborted) return;
      setText((previous) => previous + result.text);
      setOffset(result.next_offset);
    } catch {
      if (!controller.signal.aborted) setError(true);
    } finally {
      if (request.current === controller) { request.current = null; setPending(false); }
    }
  };
  return (
    <article className="raw-row" data-transcript-id={event.id}>
      <p className="raw-meta">
        {event.at ?? "—"} · {event.source} · {event.kind} · {event.type}
        {event.role ? ` · ${event.role}` : ""}
      </p>
      {event.summary ? <p className="raw-summary">{event.summary}</p> : null}
      {event.text ? <pre className="session-out">{event.text}</pre> : null}
      {event.output ? <pre className="session-out">{event.output}</pre> : null}
      <button type="button" className="link live-record-control" aria-expanded={open} onClick={() => {
        if (open) { cancel(); setOpen(false); }
        else { setOpen(true); if (!text) void read(); }
      }}>{open ? "Hide full record" : "Full record"}</button>
      {open ? <div>
        {text ? <pre className="session-out">{text}</pre> : null}
        {pending ? <p className="session-notice" role="status">Loading record… <button type="button" className="link live-record-control" onClick={cancel}>Cancel</button></p>
          : error ? <p className="session-notice" role="status">Could not read this record. <button type="button" className="link live-record-control" onClick={() => void read()}>Retry</button></p>
          : offset !== null ? <button type="button" className="link live-record-control" onClick={() => void read()}>{text ? "Show more" : "Load record"}</button> : null}
      </div> : null}
    </article>
  );
}

function RawList({ rows, redaction, scope, active }: { rows: TranscriptEvent[]; redaction: string; scope: RecordScope; active: boolean }) {
  return <section className="session" aria-label="Raw events">
    <p className="session-notice">{redaction}</p>
    {rows.map((event) => <RawRecord key={event.id} event={event} scope={scope} active={active} />)}
  </section>;
}

interface ScrollProps {
  children: ReactNode;
  active: boolean;
  following: boolean;
  onPause: () => void;
  onOlder: () => void;
}
interface ReadingAnchor { id: string; offset: number }

/** Manual row anchoring covers prepends and changes above a reader; native anchoring is disabled. */
class TranscriptBody extends Component<ScrollProps> {
  private body = createRef<HTMLDivElement>();
  private content = createRef<HTMLDivElement>();
  private anchor: ReadingAnchor | null = null;
  private previousScroll = 0;
  private touchY: number | null = null;
  private observer: ResizeObserver | null = null;

  private remember = () => {
    const node = this.body.current;
    if (!node) return;
    const top = node.getBoundingClientRect().top;
    const row = Array.from(node.querySelectorAll<HTMLElement>("[data-transcript-id]"))
      .find((candidate) => candidate.getBoundingClientRect().bottom > top);
    this.anchor = row ? { id: row.dataset.transcriptId!, offset: row.getBoundingClientRect().top - top } : null;
    this.previousScroll = node.scrollTop;
  };

  private restore = () => {
    const node = this.body.current;
    if (!node || !this.props.active) return;
    if (this.props.following) node.scrollTop = node.scrollHeight;
    else if (this.anchor) {
      const row = Array.from(node.querySelectorAll<HTMLElement>("[data-transcript-id]"))
        .find((candidate) => candidate.dataset.transcriptId === this.anchor?.id);
      if (row) node.scrollTop += row.getBoundingClientRect().top - node.getBoundingClientRect().top - this.anchor.offset;
    }
    this.remember();
  };

  componentDidMount() {
    this.restore();
    this.observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(this.restore);
    if (this.content.current) this.observer?.observe(this.content.current);
  }
  getSnapshotBeforeUpdate() {
    const node = this.body.current;
    // A refresh can commit after native scrolling but before its scroll event.
    // Capture that reading position before the DOM changes, not the stale anchor.
    if (!this.props.following && node && node.scrollTop !== this.previousScroll) {
      const up = node.scrollTop < this.previousScroll;
      this.remember();
      return up;
    }
    return false;
  }
  componentDidUpdate(_previous: ScrollProps, _state: unknown, movedUp: boolean) {
    this.restore();
    // restore records the new scrollTop, so the queued native event is now a no-op.
    if (movedUp) this.upward();
  }
  componentWillUnmount() { this.observer?.disconnect(); }

  private upward = () => {
    if (!this.props.active) return;
    this.props.onPause();
    if ((this.body.current?.scrollTop ?? Infinity) <= 64) this.props.onOlder();
  };

  render() {
    return <div className="live-body" ref={this.body} tabIndex={0} aria-label="Session activity"
      onWheel={(event) => { if (event.deltaY < 0) this.upward(); }}
      onTouchStart={(event) => { this.touchY = event.touches[0]?.clientY ?? null; }}
      onTouchMove={(event) => {
        const y = event.touches[0]?.clientY;
        if (y !== undefined && this.touchY !== null && y > this.touchY) this.upward();
        this.touchY = y ?? null;
      }}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && ["ArrowUp", "PageUp", "Home"].includes(event.key)) this.upward();
      }}
      onScroll={() => {
        const node = this.body.current;
        if (!node || !this.props.active || node.scrollTop === this.previousScroll) return;
        const up = node.scrollTop < this.previousScroll;
        this.remember();
        if (up) this.upward();
      }}>
      <div ref={this.content}>{this.props.children}</div>
    </div>;
  }
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
  steering?: Steering;
  readOnly?: boolean;
  active?: boolean;
  /** Replaces the panel title beside the activity pulse, as the desktop Live session | Terminal switch does. */
  heading?: ReactNode;
}

/**
 * The live session panel (SPEC.md §3.10): the worker's own session plus task events, read as a
 * transcript. Tinted prompt blocks, the worker's
 * prose, one compact row per tool call with its output folded, separators at task boundaries, subtle
 * timestamps, Raw events behind a toggle. States: waiting (queued), connecting, streaming, ended,
 * unavailable. The server derives the files from the task record; the page sends no paths.
 */
export default function LiveSession(props: LiveSessionProps) {
  const scope = [props.project, props.task.slug, props.task.l2_engine, props.task.session_id, props.task.attempt].join(":");
  return <ScopedLiveSession key={scope} {...props} />;
}

function ScopedLiveSession({ project, task, engineLabel, waiting, steering, readOnly, active = true, heading }: LiveSessionProps) {
  const [raw, setRaw] = useState(false);
  const [paused, setPaused] = useState(false);
  const engine = str(task["l2_engine"]);
  const sessionId = str(task["session_id"]);
  const state = task.state ?? "";
  const running = state === "running";
  const hasSession = !waiting && Boolean(sessionId);
  const attempt = typeof task.attempt === "number" ? task.attempt : 0;
  const transcript = useTranscript(hasSession ? project : "", task.slug, engine, sessionId, raw, running, active, attempt, !paused);
  const events = transcript.data?.events ?? [];
  const scope = { project, slug: task.slug, engine, sessionId, attempt };
  const loadOlder = () => {
    if (transcript.data?.has_earlier && !transcript.historyPending && !transcript.historyError && !transcript.reconnecting) void transcript.loadOlder();
  };
  const transportStatus = transcript.isError && transcript.data
    ? <p className="session-notice" role="status">Could not update the session. <button type="button" className="link live-record-control" onClick={() => void transcript.refetch()}>Retry</button></p>
    : transcript.reconnecting ? <Separator text="Reconnecting to the session…" />
    : transcript.catchingUp ? <Separator text="Catching up…" /> : null;

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
  } else if (transcript.isError && !transcript.data) {
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
  } else if (!transcript.data?.has_engine_records && !raw) {
    // Only Altitude's own boundaries so far: a worker that has not written its first record yet, or an
    // attempt whose session file is gone.
    content = (
      <>
        <Conversation events={events} engineLabel={engineLabel} running={running} />
        {transportStatus ?? (running ? <p className="session-notice">Connecting to the session…</p> : unavailable)}
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
          <RawList rows={events} redaction={transcript.data!.redaction} scope={scope} active={active} />
        ) : (
          <Conversation events={events} engineLabel={engineLabel} running={running} />
        )}
        {transportStatus ?? <Separator text={footer} tone={running && !paused ? "live" : undefined} />}
        {running && !transcript.isError && !transcript.reconnecting && !transcript.catchingUp ? <LiveCue activity={task.activity} /> : null}
      </>
    );
  }
  if (transcript.isError || transcript.reconnecting || transcript.catchingUp) tone = "muted";

  return (
    <section className="live-panel" aria-label="Live session">
      <header className="live-head">
        {heading ? <div className="live-title"><LivePulse tone={tone} activity={task.activity} />{heading}</div> : <h2 className="live-title">
          <LivePulse tone={tone} activity={task.activity} />
          Live session
        </h2>}
        {hasSession || steering ? (
          <div className="live-tools">
            {steering ? <SteeringControls steering={steering} disabled={readOnly} /> : null}
            {hasSession && running ? (
              <button type="button" className="btn btn-ghost live-tool" onClick={() => setPaused((v) => !v)}>
                {paused ? "Follow" : "Pause"}
              </button>
            ) : null}
            {hasSession ? <button
              type="button"
              className="btn btn-ghost live-tool"
              aria-pressed={raw}
              title={transcript.data?.redaction}
              onClick={() => { setPaused(false); setRaw((v) => !v); }}
            >
              Raw events
            </button> : null}
          </div>
        ) : null}
      </header>
      {steering ? <SteeringNotice steering={steering} /> : null}
      <TranscriptBody key={String(raw)} active={active} following={!paused} onPause={() => setPaused(true)} onOlder={loadOlder}>
        {transcript.data && hasSession ? <div className="live-history-status">
          {transcript.historyPending ? <span role="status">Loading earlier activity…</span>
            : transcript.historyError ? <span role="status">Could not load earlier activity. <button type="button" className="btn btn-ghost live-record-control" onClick={() => void transcript.loadOlder()}>Retry</button></span>
            : !transcript.data.has_earlier ? <span>Beginning of session</span> : null}
        </div> : null}
        {content}
      </TranscriptBody>
    </section>
  );
}
