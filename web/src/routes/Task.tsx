import { useState } from "react";
import { Link, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useL2Message, useTask, useTaskAction } from "../data/api";
import type { TaskMessage, TaskView } from "../data/api";

// TaskView is a passthrough schema: everything the server sends beyond the declared
// fields (dispatch_id, session_id, worktree, spend, live, ...) arrives typed
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
  { action: "dispatch", label: "Dispatch", states: ["approved"] },
  { action: "unpark", label: "Unpark", states: ["parked"] },
  { action: "done", label: "Mark done", states: ["reported"] },
  {
    action: "park",
    label: "Park",
    states: ["approved", "running", "blocked"],
    reason: true,
  },
  {
    action: "reject",
    label: "Reject",
    states: ["approved", "blocked", "parked"],
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
        {mine ? "You" : "L2"}
        {when ? ` · ${when}` : ""}
      </p>
      <p className="whitespace-pre-wrap text-body text-ink-2">{message.text}</p>
    </article>
  );
}

function TaskDetail({ project, task }: { project: string; task: TaskView }) {
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
  const dispatchId = str(task["dispatch_id"]);
  const sessionId = str(task["session_id"]);
  const agentId = str(task["agent_id"]);
  const worktree = str(task["worktree"]);
  const branch = str(task["branch"]);
  const blockedReason = str(task["blocked_reason"]);
  // Held by Altitude (blocked + resume_after) reads as queued, not as something you must unstick:
  // it gets the sentence and the neutral colour, never the danger line.
  const held = state === "blocked" && Boolean(task.resume_after);
  const model = str(task["model"]);
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

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link className="text-meta text-muted" to={`/projects/${project}`}>
        ‹ {project}
      </Link>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {state ? <span className="pill">{state}</span> : null}
          <h1 className="text-page-title font-semibold">{task.title || slug}</h1>
        </div>
        <p className="text-meta text-muted">
          {slug} · dispatch {dispatchId || "—"} · session {sessionId ? sessionId.slice(0, 8) : "—"} ·
          worktree {worktree || "—"}
          {branch ? ` · branch ${branch}` : ""}
          {model ? ` · model ${model}` : ""}
        </p>
        {agentId ? <p className="text-meta text-muted">attach: claude attach {agentId}</p> : null}
        {held ? (
          <p className="text-body text-ink-2">
            Queued: Altitude resumes this L2 itself when the WIP / one-rule-task-at-a-time hold
            clears ({blockedReason})
          </p>
        ) : blockedReason ? (
          <p className="text-body text-danger">Blocked: {blockedReason}</p>
        ) : null}
      </header>

      {hasActivity ? (
        <section className="card space-y-1">
          <h2 className="label">Activity</h2>
          <p className="text-meta text-muted">
            turns {num(spend["turns"]) ?? 0} · L1 runs {num(spend["subagent_launches_reported"]) ?? 0}
            {" · "}edits {num(spend["edits_hook"]) ?? 0} · retries {num(spend["retries"]) ?? 0}
          </p>
        </section>
      ) : null}

      {Object.keys(live).length > 0 ? (
        <section className="card space-y-1">
          <h2 className="label">Live</h2>
          <p className="text-body text-ink-2">
            {str(liveState["status"]) || str(live["state"]) || "running"} · L1 runs {num(live["l1_runs"]) ?? 0}
          </p>
          <p className="text-meta text-muted">
            edits {num(live["edits"]) ?? 0}
            {num(live["context_percent"]) != null ? ` · ctx ${num(live["context_percent"])}%` : ""}
            {str(live["context_state"]) ? ` · ${str(live["context_state"])}` : ""}
          </p>
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
          <div className="space-y-2">
            <textarea
              className="field w-full"
              aria-label="Message the L2"
              placeholder="Ask a question or steer this task"
              rows={3}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && message.trim()) {
                  e.preventDefault();
                  sendL2.mutate(
                    { project, slug, text: message.trim() },
                    { onSuccess: () => setMessage("") },
                  );
                }
              }}
            />
            <button
              type="button"
              className="btn"
              disabled={sendL2.isPending || message.trim().length === 0}
              onClick={() => {
                sendL2.mutate(
                  { project, slug, text: message.trim() },
                  { onSuccess: () => setMessage("") },
                );
              }}
            >
              Send
            </button>
          </div>
        ) : null}
      </section>

      <section className="space-y-2">
        <h2 className="label">Actions</h2>
        <input
          className="field w-full"
          aria-label="Reason"
          placeholder="Reason (required to park / reject)"
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
              // Park and Reject are destructive and irreversible from here; the 0.1 app refused
              // them without a reason and Project.tsx still does, so they stay disabled until one
              // is typed rather than firing on a single unconfirmed click.
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
  return <TaskDetail project={project} task={task.data} />;
}
