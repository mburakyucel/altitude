import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { streamChat, useChat, useChatDequeue, useL3Engine, useOverview } from "../data/api";
import type { L3Engine } from "../data/api";
import type { ActiveTurn, ChatMessage, ChatView, QueuedMessage } from "../data/api";
import VoiceComposer from "../components/VoiceComposer";

/** "5m", "3h", "2d" — empty string when the timestamp is missing or unparseable. */
function age(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60_000);
  if (minutes < 0) return "";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

// ChatMessage/ChatView are passthrough schemas: trigger and context_percent arrive as unknown.
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function metaLine(role: string, parts: Array<string | null>): string {
  const label = role === "user" ? "you" : role === "server" ? "server" : "L3";
  return [label, ...parts.filter((p): p is string => Boolean(p))].join(" · ");
}

function Bubble({
  role,
  meta,
  text,
  action,
}: {
  role: string;
  meta: string;
  text: string;
  action?: ReactNode;
}) {
  const mine = role === "user";
  return (
    <article
      className={`card min-w-0 max-w-[85%] space-y-1 [overflow-wrap:anywhere] ${mine ? "ml-auto" : "mr-auto"}`}
      data-role={role}
    >
      <p className="flex min-w-0 items-center gap-2 text-meta text-muted">
        <span className="min-w-0 flex-1">{meta}</span>
        {action}
      </p>
      <p className="min-w-0 whitespace-pre-wrap text-body text-ink-2">{text}</p>
    </article>
  );
}

function HistoryBubble({ m }: { m: ChatMessage }) {
  const ctx = num(m["context_percent"]);
  return (
    <Bubble
      role={m.role}
      meta={metaLine(m.role, [
        str(m["trigger"]) || null,
        age(m.at) || null,
        ctx != null ? `ctx ${ctx}%` : null,
      ])}
      text={m.text}
    />
  );
}

interface LocalTurn {
  id: number;
  turnId?: string;
  user: string;
  assistant: string;
  /** Signature of the transcript this turn was sent against (see `signature`). */
  base: string;
  /** L3 looked busy when this was sent, so it is on its way to the queue, not to a stream. */
  queueing: boolean;
}

/** A message waiting for the next turn boundary, with the control that takes it back off the queue. */
function QueuedBubble({
  m,
  position,
  onRemove,
}: {
  m: QueuedMessage;
  position: number | null;
  onRemove: () => void;
}) {
  const mine = m.trigger === "chat" && m.role === "burak";
  return (
    <Bubble
      role={mine ? "user" : "assistant"}
      meta={metaLine(mine ? "user" : (m.role ?? "server"), [
        position != null ? `queued ${position}` : "queued",
        m.trigger && m.trigger !== "chat" ? m.trigger : null,
        age(m.at) || null,
      ])}
      text={m.text}
      action={mine ? (
        <button type="button" className="btn btn-ghost ml-auto shrink-0" onClick={onRemove}>
          Remove
        </button>
      ) : null}
    />
  );
}

function ActiveBubble({ turn }: { turn: ActiveTurn }) {
  return (
    <Bubble
      role="assistant"
      meta={metaLine("assistant", ["thinking", turn.trigger, age(turn.started_at) || null])}
      text="Thinking…"
    />
  );
}

/**
 * Identity of a loaded transcript — length, last stamp, last text — never the *sender's* text.
 * Matching a local turn against the history by the message the user just typed would erase it the
 * moment those words already appear above (re-sending "status?"), so the bubble and the streamed
 * reply would never show; comparing whole-transcript snapshots cannot make that mistake.
 *
 * All three parts are needed. altitude/l3.py:45 caps the reply at `chat.jsonl[-limit:]` (the
 * client asks for 60), so past 60 messages the length is pinned and only the tail moves; the tail
 * stamp is second-resolution (state.py:20) and the schema lets `at` be nullish, so the last text
 * is what keeps the signature moving in the cases where the first two go constant.
 */
function signature(history: ChatMessage[]): string {
  const last = history[history.length - 1];
  return `${history.length}:${last?.at ?? ""}:${last?.text ?? ""}`;
}

export default function Chat() {
  const params = useParams();
  const project = params["name"] ?? "";
  const chat = useChat(project);
  // The shell already holds ["overview"]; this reads the same cache entry, no extra request.
  const overview = useOverview();
  const queryClient = useQueryClient();

  // Route params can change without remounting Chat. Keep a separate draft per destination so text
  // and voice review from one project's L3 can never be submitted to another project's L3.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draft = drafts[project] ?? "";
  const setDraft = (text: string) => setDrafts((previous) => ({ ...previous, [project]: text }));
  // The project's L3 engine pin: a named engine holds every L3 turn there until set back to Auto.
  const pin = useL3Engine(project);
  const [locals, setLocals] = useState<LocalTurn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const nextId = useRef(0);
  const transcript = useRef<HTMLElement>(null);
  const following = useRef(true);

  const history = chat.data?.history ?? [];
  const active = chat.data?.active;
  const busy = chat.data?.busy ?? false;
  const queued = chat.data?.queued ?? [];
  const dequeue = useChatDequeue(project);
  const sig = signature(history);
  // Each local turn is keyed by its own id and holds the transcript it was sent against; it is
  // dropped in the same render as the refetched history that moved past it — never earlier, so a
  // repeated message keeps its bubble and its streamed reply, and never later, so it never doubles.
  const terminalTurns = new Set(history
    .filter((m) => ["assistant", "error"].includes(m.role))
    .map((m) => str(m["turn_id"]))
    .filter(Boolean));
  // A focus/refetch can reveal the server's just-written user row before the stream ends. Keep the
  // current local response through reader completion and until its terminal history row arrives.
  const pending = locals.filter((l) => l.base === sig
    || (streaming && l.id === nextId.current)
    || Boolean(l.turnId && !terminalTurns.has(l.turnId)));
  const activeFinished = Boolean(active && terminalTurns.has(active.id));
  const localOwnsActive = Boolean(active && pending.some((l) => l.turnId === active.id
    || (streaming && l.id === nextId.current && !l.queueing)));
  const shownActive = active && !activeFinished && !localOwnsActive
    ? active
    : null;
  const activeId = shownActive?.id ?? "";
  const streamed = pending.map((l) => `${l.id}:${l.assistant.length}`).join(",");

  useEffect(() => {
    following.current = true;
    setLocals([]);
  }, [project]);

  useEffect(() => {
    const node = transcript.current;
    if (node && following.current) node.scrollTop = node.scrollHeight;
  }, [project, sig, streamed, activeId]);

  const patch = (id: number, fn: (turn: LocalTurn) => LocalTurn) => {
    setLocals((prev) => prev.map((l) => (l.id === id ? fn(l) : l)));
  };

  const send = async (submitted: string) => {
    const text = submitted.trim();
    if (!text || streaming) return;
    const id = (nextId.current += 1);
    following.current = true;
    // A busy L3 queues this message, but the page's view of busy is up to one poll old: the server's
    // answer, streamed or queued, decides which of the two this turn turns out to be.
    setLocals((prev) => [...prev, { id, user: text, assistant: "", base: sig, queueing: busy }]);
    setStreaming(true);
    try {
      const sent = await streamChat(project, text, (chunk) => {
        patch(id, (l) => ({ ...l, assistant: l.assistant + chunk, queueing: false }));
      });
      if (sent.queued) {
        // It waits for the next turn boundary; the server's queue owns it from here.
        const row = sent.queued;
        setLocals((prev) => prev.filter((l) => l.id !== id));
        queryClient.setQueryData<ChatView>(["chat", project], (cached) =>
          cached ? { ...cached, queued: [...(cached.queued ?? []), row] } : cached,
        );
      } else {
        // It streamed after all, whatever the page believed when it was sent.
        patch(id, (l) => ({
          ...l,
          turnId: sent.turn_id ?? l.turnId,
          queueing: false,
          assistant: sent.error ? `${l.assistant}\n[error] ${sent.error}` : l.assistant,
        }));
      }
      setDraft("");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      patch(id, (l) => ({ ...l, assistant: `${l.assistant}\n[error] ${message}` }));
      throw err;
    } finally {
      setStreaming(false);
      await queryClient.invalidateQueries({ queryKey: ["chat", project] });
    }
  };

  if (chat.isPending) return <p className="text-muted">Loading…</p>;
  if (chat.isError) return <p className="text-danger">{chat.error.message}</p>;

  const l3: Record<string, unknown> = chat.data.l3 ?? {};
  const ctx = num(l3["context_percent"]);
  const session = str(l3["session_id"]);
  // One link per managed project, straight to that project's chat.
  const switchable = (overview.data?.projects ?? []).filter((p) => p.managed);

  return (
    <div className="chat-route mx-auto flex min-h-0 w-full min-w-0 max-w-3xl flex-1 flex-col gap-4 overflow-hidden">
      <header className="flex min-w-0 shrink-0 flex-wrap items-baseline gap-3 [overflow-wrap:anywhere]">
        <h1 className="text-page-title font-semibold">Chat</h1>
        <Link className="min-w-0 text-meta text-muted" to={`/projects/${project}`}>
          {project}
        </Link>
        <span className="ml-auto min-w-0 text-meta text-muted">
          {[
            busy ? "busy" : null,
            queued.length > 0 ? `${queued.length} queued` : null,
            session ? `session ${session.slice(0, 8)}` : null,
            ctx != null ? `ctx ${ctx}%` : null,
            num(l3["turns"]) != null ? `turns ${num(l3["turns"])}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </header>

      {switchable.length > 0 ? (
        <nav className="flex min-w-0 shrink-0 flex-wrap gap-2" aria-label="Projects">
          {switchable.map((p) => (
            <NavLink
              key={p.name}
              to={`/projects/${p.name}`}
              className="pill min-w-0 max-w-full whitespace-normal [overflow-wrap:anywhere] hover:text-ink aria-[current=page]:border-accent-tint-border aria-[current=page]:bg-accent-tint aria-[current=page]:text-accent-ink"
            >
              {p.name}
            </NavLink>
          ))}
        </nav>
      ) : null}

      <section
        ref={transcript}
        className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-x-hidden overflow-y-auto"
        aria-label="Transcript"
        onScroll={(event) => {
          const node = event.currentTarget;
          following.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
        }}
      >
        {history.length === 0 && pending.length === 0 && queued.length === 0 && !shownActive ? (
          <p className="text-muted">No messages yet.</p>
        ) : null}
        {history.map((m, i) => (
          <HistoryBubble key={`${m.at ?? "m"}-${i}`} m={m} />
        ))}
        {pending.map((l) => (
          <div key={l.id} className="flex min-w-0 flex-col gap-3">
            <Bubble
              role="user"
              meta={metaLine("user", [l.queueing ? "queueing" : "sending"])}
              text={l.user}
            />
            {l.queueing && !l.assistant ? null : (
              <Bubble
                role="assistant"
                meta={metaLine("assistant", ["streaming"])}
                text={l.assistant || "Thinking…"}
              />
            )}
          </div>
        ))}
        {shownActive ? <ActiveBubble turn={shownActive} /> : null}
        {queued.map((m, i) => (
          <QueuedBubble
            key={m.id}
            m={m}
            position={queued.length > 1 ? i + 1 : null}
            onRemove={() => dequeue.mutate(m.id)}
          />
        ))}
      </section>

      <VoiceComposer
        key={project}
        className="min-w-0 shrink-0"
        value={draft}
        onChange={setDraft}
        onSubmit={send}
        ariaLabel="Message L3"
        placeholder="Talk to L3 about roadmap, architecture, or what to build"
        submitLabel={busy ? "Queue" : "Send"}
        submitting={streaming}
        disabled={streaming}
        actions={
          <>
          <select
            className="field min-w-0"
            aria-label="L3 engine"
            title="Which engine runs L3 for this project until you change it; Auto follows the weekly quota"
            value={chat.data?.engine ?? "auto"}
            onChange={(e) => pin.mutate(e.target.value === "auto" ? null : (e.target.value as L3Engine))}
          >
            <option value="auto">Auto</option>
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
          <span className="text-meta text-muted">⌘↵ sends</span>
          </>
        }
      />
    </div>
  );
}
