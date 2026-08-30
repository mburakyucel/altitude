import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, streamChat, useChat } from "../data/api";
import type { ChatMessage } from "../data/api";
import { useToast } from "../data/Toast";

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
  const label = role === "user" ? "you" : "L3";
  return [label, ...parts.filter((p): p is string => Boolean(p))].join(" · ");
}

function Bubble({ role, meta, text }: { role: string; meta: string; text: string }) {
  const mine = role === "user";
  return (
    <article
      className={`card max-w-[85%] space-y-1 ${mine ? "ml-auto" : "mr-auto"}`}
      data-role={role}
    >
      <p className="text-meta text-muted">{meta}</p>
      <p className="whitespace-pre-wrap text-body text-ink-2">{text}</p>
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
  user: string;
  assistant: string;
}

export default function Chat() {
  const params = useParams();
  const project = params["name"] ?? "";
  const chat = useChat(project);
  const toast = useToast();
  const queryClient = useQueryClient();

  const [draft, setDraft] = useState("");
  const [locals, setLocals] = useState<LocalTurn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const nextId = useRef(0);
  const foot = useRef<HTMLDivElement>(null);

  const history = chat.data?.history ?? [];
  const busy = chat.data?.busy ?? false;
  // Drop a local turn once the server's history has caught up with it.
  const pending = locals.filter(
    (l) => !history.some((m) => m.role === "user" && m.text === l.user),
  );

  useEffect(() => {
    foot.current?.scrollIntoView?.({ block: "end" });
  }, [history.length, pending.length]);

  const patch = (id: number, fn: (turn: LocalTurn) => LocalTurn) => {
    setLocals((prev) => prev.map((l) => (l.id === id ? fn(l) : l)));
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || streaming || busy) return;
    const id = (nextId.current += 1);
    setLocals((prev) => [...prev, { id, user: text, assistant: "" }]);
    setDraft("");
    setStreaming(true);
    try {
      const done = await streamChat(project, text, (chunk) => {
        patch(id, (l) => ({ ...l, assistant: l.assistant + chunk }));
      });
      if (done.error) {
        patch(id, (l) => ({ ...l, assistant: `${l.assistant}\n[error] ${done.error ?? ""}` }));
      }
      await queryClient.invalidateQueries({ queryKey: ["chat", project] });
    } catch (err) {
      // 409 is "L3 is busy": toast it and never retry. The text goes back in the composer.
      if (err instanceof ApiError && err.status === 409) {
        toast.show({ message: "L3 is busy", severity: "limit" });
        setLocals((prev) => prev.filter((l) => l.id !== id));
        setDraft(text);
      } else {
        const message = err instanceof Error ? err.message : String(err);
        patch(id, (l) => ({ ...l, assistant: `${l.assistant}\n[error] ${message}` }));
      }
    } finally {
      setStreaming(false);
    }
  };

  if (chat.isPending) return <p className="text-muted">Loading…</p>;
  if (chat.isError) return <p className="text-danger">{chat.error.message}</p>;

  const l3: Record<string, unknown> = chat.data.l3 ?? {};
  const ctx = num(l3["context_percent"]);
  const session = str(l3["session_id"]);
  const disabled = streaming || busy;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-page-title font-semibold">Chat</h1>
        <Link className="text-meta text-muted" to={`/projects/${project}`}>
          {project}
        </Link>
        <span className="ml-auto text-meta text-muted">
          {[
            busy ? "busy" : null,
            session ? `session ${session.slice(0, 8)}` : null,
            ctx != null ? `ctx ${ctx}%` : null,
            num(l3["turns"]) != null ? `turns ${num(l3["turns"])}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </header>

      <section className="flex flex-col gap-3" aria-label="Transcript">
        {history.length === 0 && pending.length === 0 ? (
          <p className="text-muted">No messages yet.</p>
        ) : null}
        {history.map((m, i) => (
          <HistoryBubble key={`${m.at ?? "m"}-${i}`} m={m} />
        ))}
        {pending.map((l) => (
          <div key={l.id} className="flex flex-col gap-3">
            <Bubble role="user" meta={metaLine("user", ["sending"])} text={l.user} />
            <Bubble role="assistant" meta={metaLine("assistant", ["streaming"])} text={l.assistant} />
          </div>
        ))}
        <div ref={foot} />
      </section>

      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <textarea
          className="field w-full"
          aria-label="Message L3"
          placeholder="Talk to L3 — or /idea … , /backlog"
          rows={3}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={disabled || draft.trim().length === 0}
          >
            {streaming ? "Sending…" : busy ? "Busy" : "Send"}
          </button>
          <span className="text-meta text-muted">⌘↵ sends</span>
        </div>
      </form>
    </div>
  );
}
