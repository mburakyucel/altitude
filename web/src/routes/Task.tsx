import { useState } from "react";
import { Link, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useL2Message, useTask, useTaskAction } from "../data/api";
import type { TaskView } from "../data/api";
import { launchLabel } from "../data/launches";

// TaskView is a passthrough schema: everything the server sends beyond the declared
// fields (dispatch_id, session_id, worktree, envelope, spend, live, ...) arrives typed
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
  { action: "propose", label: "Propose", states: ["requested"] },
  {
    action: "build",
    label: "Build now",
    states: ["requested", "parked", "proposed"],
    primary: true,
    title:
      "Executive override: approve as requested and dispatch now, skipping the proposal/critic loop",
  },
  { action: "dispatch", label: "Dispatch", states: ["approved"] },
  { action: "unpark", label: "Unpark", states: ["parked"] },
  { action: "done", label: "Mark done", states: ["reported"] },
  {
    action: "park",
    label: "Park",
    states: ["requested", "proposed", "approved", "blocked", "reported"],
    reason: true,
  },
  {
    action: "reject",
    label: "Reject",
    states: ["requested", "proposed", "approved", "blocked", "reported", "parked"],
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

function TaskDetail({ project, task }: { project: string; task: TaskView }) {
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const queryClient = useQueryClient();
  const act = useTaskAction(project);
  const sendL2 = useL2Message(project);

  const slug = task.slug;
  const state = task.state ?? "";
  const envelope = rec(task["envelope"]);
  const spend = rec(task["spend"]);
  const live = rec(task["live"]);
  const files = Object.entries(task.files ?? {});
  const events = task.events ?? [];
  const dispatchId = str(task["dispatch_id"]);
  const sessionId = str(task["session_id"]);
  const agentId = str(task["agent_id"]);
  const worktree = str(task["worktree"]);
  const branch = str(task["branch"]);
  const blockedReason = str(task["blocked_reason"]);
  const model = str(task["model"]);
  const spendUsed = spend["subagent_launches_hook"] ?? spend["subagent_launches_reported"];
  const cap = envelope["subagent_launches"] ?? spend["cap"];
  const hasEnvelope = Object.keys(envelope).length > 0 || Object.keys(spend).length > 0;
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
          {task.class ? <span className="pill">{task.class}</span> : null}
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
        {blockedReason ? <p className="text-body text-danger">Blocked: {blockedReason}</p> : null}
      </header>

      {hasEnvelope ? (
        <section className="card space-y-1">
          <h2 className="label">Envelope</h2>
          <p className="text-body text-ink-2">{launchLabel(spendUsed, cap)}</p>
          <p className="text-meta text-muted">
            turns {num(spend["turns"]) ?? 0} · max turns {num(envelope["max_turns"]) ?? "?"} · L1 in
            flight {num(envelope["l1_in_flight"]) ?? "?"} · edits {num(spend["edits_hook"]) ?? 0} ·
            retries {num(spend["retries"]) ?? 0}
            {str(envelope["verification"]) ? ` · verification ${str(envelope["verification"])}` : ""}
          </p>
        </section>
      ) : null}

      {Object.keys(live).length > 0 ? (
        <section className="card space-y-1">
          <h2 className="label">Live</h2>
          <p className="text-body text-ink-2">
            {str(liveState["status"]) || str(live["state"]) || "running"} ·{" "}
            {launchLabel(live["subagent_launches"], live["cap"])}
          </p>
          <p className="text-meta text-muted">
            edits {num(live["edits"]) ?? 0}
            {num(live["context_percent"]) != null ? ` · ctx ${num(live["context_percent"])}%` : ""}
            {str(live["context_state"]) ? ` · ${str(live["context_state"])}` : ""}
          </p>
        </section>
      ) : null}

      <section className="space-y-2">
        <h2 className="label">Actions</h2>
        <input
          className="field w-full"
          aria-label="Reason"
          placeholder="Reason (park / reject)"
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
              disabled={act.isPending}
              onClick={() => run(a)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </section>

      {state === "running" || state === "blocked" ? (
        <section className="space-y-2">
          <h2 className="label">Message the L2</h2>
          <textarea
            className="field w-full"
            aria-label="Message the L2"
            rows={2}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
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
        </section>
      ) : null}

      {task.critique != null ? (
        <details className="card">
          <summary className="cursor-pointer text-card-title">
            critique{str(rec(task.critique)["verdict"]) ? ` (${str(rec(task.critique)["verdict"])})` : ""}
          </summary>
          <Json value={task.critique} />
        </details>
      ) : null}

      {task.report_json != null ? (
        <details className="card">
          <summary className="cursor-pointer text-card-title">report.json</summary>
          <Json value={task.report_json} />
        </details>
      ) : null}

      {files.map(([name, body]) => (
        <details key={name} className="card" open={name === "report" || name === "proposal"}>
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
