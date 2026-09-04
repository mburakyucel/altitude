import { useState } from "react";
import { Link, NavLink, Outlet, useOutletContext, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useL2Message, useTask, useTaskAction } from "../data/api";
import type { TaskMessage, TaskView } from "../data/api";
import { asOf, engineName, modelName, older, SESSION_STALE_MS } from "../data/observed";
import VoiceComposer from "../components/VoiceComposer";

// TaskView is a passthrough schema: everything the server sends beyond the declared
// fields (attempt, session_id, worktree, spend, live, ...) arrives typed
// `unknown`, so narrow it here rather than widening the schema in api.ts.
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

interface ActionSpec {
  action: string;
  label: string;
  states: string[];
  primary?: boolean;
  title?: string;
  reason?: boolean;
}

const ACTIONS: ActionSpec[] = [
  { action: "dispatch", label: "Dispatch", states: ["queued"] },
  { action: "stop", label: "Stop", states: ["running"] },
  { action: "done", label: "Mark done", states: ["reported"] },
  {
    action: "reject",
    label: "Reject",
    states: ["queued", "running", "blocked", "reported"],
    reason: true,
  },
];

function Json({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap text-meta text-ink-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function Events({ events }: { events: Array<Record<string, unknown>> }) {
  const lines = events.map((e) => {
    const { at, kind, ...rest } = e;
    const tail = Object.keys(rest).length > 0 ? ` ${JSON.stringify(rest)}` : "";
    return `${str(at) || "—"} ${str(kind) || "event"}${tail}`;
  });
  return (
    <details className="card">
      <summary className="cursor-pointer text-card-title">Events ({events.length})</summary>
      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-meta text-ink-2">
        {lines.join("\n")}
      </pre>
    </details>
  );
}

function messageTime(at: string | null | undefined): string {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}

function ConversationMessage({ message }: { message: TaskMessage }) {
  const mine = message.role === "burak";
  const when = messageTime(message.at);
  return (
    <article
      className={`card max-w-[85%] space-y-1 ${mine ? "ml-auto" : "mr-auto"}`}
      data-role={message.role}
    >
      <p className="text-meta text-muted">
        {mine ? "You" : message.role === "l3" ? "L3" : "L2"}
        {when ? ` · ${when}` : ""}
      </p>
      <p className="whitespace-pre-wrap text-body text-ink-2">{message.text}</p>
    </article>
  );
}

interface TaskContext {
  project: string;
  task: TaskView;
}

/** The task views (Conversation, Live session) read the task their layout already loaded. */
export function useTaskContext(): TaskContext {
  return useOutletContext<TaskContext>();
}

function TaskLayout({ project, task }: TaskContext) {
  const slug = task.slug;
  const state = task.state ?? "";
  const attempt = typeof task["attempt"] === "number" ? String(task["attempt"]) : "";
  const sessionId = str(task["session_id"]);
  const agentId = str(task["agent_id"]);
  const engine = str(task["l2_engine"]) || "claude";
  const worktree = str(task["worktree"]);
  const branch = str(task["branch"]);
  const blockedReason = str(task["blocked_reason"]);
  // Held by Altitude (blocked + resume_after) reads as queued, not as something you must unstick:
  // it gets the sentence and the neutral colour, never the danger line.
  const held = state === "blocked" && Boolean(task.resume_after);
  const model = str(task["engine_model"]) || str(task["model"]);
  const base = `/projects/${project}/tasks/${slug}`;

  // Two groups, never one run-on line: where the task stands, then what its worker is doing. The
  // worker figures come from the monitor's live row, so they carry its observation time and the
  // same staleness rule the Monitor page uses.
  const spend = rec(task["spend"]);
  const live = rec(task["live"]);
  const context = num(live["context_percent"]);
  const contextState = str(live["context_state"]);
  const observed = asOf(live["at"]);
  const staleWorker = state === "running" && older(live["at"], SESSION_STALE_MS);
  const turns = num(spend["turns"]);
  const subagents = num(spend["subagent_launches_reported"]);
  const worker = [
    engineName(engine),
    model ? modelName(model) : "",
    sessionId ? `session ${sessionId.slice(0, 8)}` : "",
    context != null
      ? `context ${Math.round(context)}%${contextState ? ` (${contextState})` : ""}${observed ? ` ${observed}` : ""}`
      : "",
    turns != null ? `turns ${turns}` : "",
    subagents != null ? `subagents ${subagents}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <Link className="text-meta text-muted" to={`/projects/${project}`}>
        ‹ {project}
      </Link>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {state ? <span className="pill">{state}</span> : null}
          <h1 className="text-page-title font-semibold">{task.title || slug}</h1>
        </div>
        <p className="text-meta text-muted">
          <span className="label">Task</span> {slug} · attempt {attempt || "—"} · worktree{" "}
          {worktree || "—"}
          {branch ? ` · branch ${branch}` : ""}
        </p>
        <p className="flex flex-wrap items-center gap-2 border-t border-border pt-2 text-meta text-muted">
          <span className="label">Worker</span>
          <span>{worker}</span>
          {staleWorker ? <span className="pill">stale</span> : null}
        </p>
        {agentId && engine === "claude" ? <p className="text-meta text-muted">attach: claude attach {agentId}</p> : null}
        {agentId && engine === "codex" ? <p className="text-meta text-muted">Codex worker {agentId.slice(0, 8)} · message this L2 to steer/resume it</p> : null}
        {held ? (
          <p className="text-body text-ink-2">
            Queued: Altitude resumes this L2 itself when the operational hold
            clears ({blockedReason})
          </p>
        ) : blockedReason ? (
          <p className="text-body text-danger">Blocked: {blockedReason}</p>
        ) : null}
      </header>

      <nav className="flex gap-2 border-b border-border" aria-label="Task views">
        <NavLink className="tab" to={base} end>
          Conversation
        </NavLink>
        <NavLink className="tab" to={`${base}/live`}>
          Live session
        </NavLink>
      </nav>

      <Outlet key={`${project}:${slug}`} context={{ project, task } satisfies TaskContext} />
    </div>
  );
}

export function TaskConversation() {
  const { project, task } = useTaskContext();
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const queryClient = useQueryClient();
  const act = useTaskAction(project);
  const sendL2 = useL2Message(project);

  const slug = task.slug;
  const state = task.state ?? "";
  const spend = rec(task["spend"]);
  const live = rec(task["live"]);
  const files = Object.entries(task.files ?? {});
  const messages = task.messages ?? [];
  const events = task.events ?? [];
  const hasActivity = Object.keys(spend).length > 0;
  const liveState = rec(live["agent"]);

  const refreshTask = () => {
    void queryClient.invalidateQueries({ queryKey: ["task", project, slug] });
  };
  const run = (spec: ActionSpec) => {
    const text = reason.trim();
    act.mutate(
      {
        project,
        slug,
        action: spec.action,
        ...(spec.reason && text ? { reason: text } : {}),
      },
      { onSuccess: refreshTask },
    );
  };
  const submitMessage = async (text: string) => {
    await sendL2.mutateAsync({ project, slug, text });
    setMessage("");
  };

  return (
    <div className="space-y-6">
      {hasActivity ? (
        <section className="card space-y-1">
          <h2 className="label">Activity</h2>
          <p className="text-meta text-muted">
            edits {num(spend["edits_hook"]) ?? 0} · retries {num(spend["retries"]) ?? 0}
          </p>
        </section>
      ) : null}

      {Object.keys(live).length > 0 ? (
        <section className="card space-y-1">
          <h2 className="label">Live</h2>
          <p className="text-body text-ink-2">
            {str(liveState["status"]) || str(live["state"]) || "running"}
          </p>
          <p className="text-meta text-muted">edits {num(live["edits"]) ?? 0}</p>
        </section>
      ) : null}

      <section className="space-y-3" aria-label="Task conversation">
        <h2 className="label">Task conversation</h2>
        {messages.length === 0 ? <p className="text-muted">No messages yet.</p> : null}
        <div className="flex flex-col gap-3">
          {messages.map((item, index) => (
            <ConversationMessage key={item.id || `${item.at ?? "message"}-${index}`} message={item} />
          ))}
        </div>
        {state === "running" || state === "blocked" ? (
          <VoiceComposer
            value={message}
            onChange={setMessage}
            onSubmit={submitMessage}
            ariaLabel="Message the L2"
            placeholder="Ask a question or steer this task"
            submitting={sendL2.isPending}
            actions={<span className="text-meta text-muted">⌘↵ sends</span>}
          />
        ) : null}
      </section>

      <section className="space-y-2">
        <h2 className="label">Actions</h2>
        <input
          className="field w-full"
          aria-label="Reason"
          placeholder="Reason (required to reject)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2">
          {ACTIONS.filter((a) => a.states.includes(state)).map((a) => (
            <button
              key={a.action}
              type="button"
              className={a.primary ? "btn btn-primary" : "btn"}
              title={a.title}
              // Reject archives the task immediately, so require an explicit reason.
              disabled={act.isPending || (a.reason === true && reason.trim().length === 0)}
              onClick={() => run(a)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </section>

      {task.report_json != null ? (
        <details className="card">
          <summary className="cursor-pointer text-card-title">report.json</summary>
          <Json value={task.report_json} />
        </details>
      ) : null}

      {files.map(([name, body]) => (
        <details key={name} className="card" open={name === "report"}>
          <summary className="cursor-pointer text-card-title">{name}</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-meta text-ink-2">{body}</pre>
        </details>
      ))}

      {events.length > 0 ? <Events events={events} /> : null}
    </div>
  );
}

export default function Task() {
  const params = useParams();
  const project = params["name"] ?? "";
  const slug = params["slug"] ?? "";
  const task = useTask(project, slug);
  if (task.isPending) return <p className="text-muted">Loading…</p>;
  if (task.isError) return <p className="text-danger">{task.error.message}</p>;
  return <TaskLayout project={project} task={task.data} />;
}
