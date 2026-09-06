import { useState } from "react";
import { Link, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  useDecide,
  useL2Message,
  useL3Reset,
  useProject,
  useTaskAction,
} from "../data/api";
import type { ProjectDecision, TaskRow } from "../data/api";
import VoiceComposer from "../components/VoiceComposer";

/** "5m", "3h", "2d" — empty string when the timestamp is missing or unparseable. */
function age(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const then = Date.parse(value);
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60_000);
  if (minutes < 0) return "";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** The payload's loose corners (live, l3, config, log rows) arrive as `unknown`. */
function dict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** Actions offered directly on the compact task card. */
function actionsFor(state: string): { action: string; label: string; primary?: boolean }[] {
  const out: { action: string; label: string; primary?: boolean }[] = [];
  if (state === "queued") out.push({ action: "dispatch", label: "Dispatch" });
  return out;
}

function TaskCard({ project, task }: { project: string; task: TaskRow }) {
  const [panel, setPanel] = useState<"" | "message">("");
  const [text, setText] = useState("");
  const act = useTaskAction(project);
  const message = useL2Message(project);

  const state = task.state ?? "";
  const raw = dict(task);
  const live = dict(task.live);
  const agent = dict(live.agent);

  const meta: string[] = [];
  if (state === "running") {
    const status = str(agent.status) || "?";
    const agentState = str(agent.state);
    meta.push(`L2 ${status}${agentState ? `/${agentState}` : ""}`);
    meta.push(`ctx ${num(live.context_percent) ?? "?"}%`);
    meta.push(`edits ${num(live.edits) ?? 0}`);
  }
  const prs = arr(raw.prs);
  if (prs.length > 0) meta.push(`PRs ${prs.map((n) => `#${String(n)}`).join(" ")}`);
  const blockedReason = str(raw.blocked_reason);
  // A blocked task carrying resume_after is *held* by Altitude, not stuck on you: it says so in
  // words and keeps the neutral card, so only a real block gets the danger border.
  const held = state === "blocked" && Boolean(task.resume_after);
  if (held) {
    meta.push(
      `queued: Altitude resumes this L2 itself when the operational hold clears (${blockedReason})`,
    );
  } else if (blockedReason) {
    meta.push(`blocked: ${blockedReason}`);
  }

  const progress = str(task.progress_tail);
  const canMessage = ["running", "blocked"].includes(state);

  return (
    <article className={`card space-y-3 ${state === "blocked" && !held ? "border-danger/40" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="pill">{state}</span>
        <Link
          className="text-card-title font-semibold"
          to={`/projects/${project}/tasks/${task.slug}`}
        >
          {task.title || task.slug}
        </Link>
        <span className="ml-auto text-meta text-muted">{age(task.updated)}</span>
      </div>
      {meta.length > 0 ? <p className="text-meta text-muted">{meta.join(" · ")}</p> : null}
      {progress ? (
        <details>
          <summary className="text-meta text-muted">Progress</summary>
          <pre className="mt-2 overflow-x-auto text-meta text-ink-2">{progress}</pre>
        </details>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {actionsFor(state).map((a) => (
          <button
            key={a.action}
            type="button"
            className={a.primary ? "btn btn-primary" : "btn"}
            disabled={act.isPending}
            onClick={() => act.mutate({ project, slug: task.slug, action: a.action })}
          >
            {a.label}
          </button>
        ))}
        {canMessage ? (
          <button
            type="button"
            className="btn"
            onClick={() => setPanel(panel === "message" ? "" : "message")}
          >
            Message L2
          </button>
        ) : null}
        <Link className="ml-auto text-meta" to={`/projects/${project}/tasks/${task.slug}`}>
          Open
        </Link>
      </div>
      {panel ? (
        <VoiceComposer
            value={text}
            placeholder="Message for the L2"
            ariaLabel={`Message the L2 on ${task.slug}`}
            rows={2}
            autoFocus
            onChange={setText}
            disabled={act.isPending}
            submitting={message.isPending}
            onSubmit={async (submitted) => {
              await message.mutateAsync({ project, slug: task.slug, text: submitted });
              setText("");
              setPanel("");
            }}
          />
      ) : null}
    </article>
  );
}

/**
 * A task blocked on user input. Task-specific discussion stays in the L2 conversation.
 */
function DecisionCard({ project, row }: { project: string; row: ProjectDecision }) {
  const [note, setNote] = useState("");
  const decide = useDecide();
  const queryClient = useQueryClient();
  const slug = str(row.slug);
  const title = str(row.title) || slug;
  const options = arr(row.options).map(String);
  const fallback = ["Resume", "Reject"];
  const labels = options.length > 0 ? options : fallback;
  const onSettled = () => queryClient.invalidateQueries({ queryKey: ["project", project] });
  return (
    <article className="card space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-card-title font-semibold">{title}</h3>
        <span className="ml-auto text-meta text-muted">{age(row.asked)}</span>
      </div>
      {row.context ? <p className="text-body text-muted">{row.context}</p> : null}
      {str(row.question) ? (
        <p className="text-body font-semibold text-ink">{str(row.question)}</p>
      ) : null}
      <textarea
        className="field w-full"
        rows={2}
        placeholder="Answer or steering note (optional)"
        aria-label={`Note for ${title}`}
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      <div className="flex flex-wrap items-center gap-2">
        {labels.map((label, index) => (
          <button
            key={label}
            type="button"
            className={index === 0 ? "btn btn-primary" : "btn"}
            disabled={decide.isPending}
            onClick={() =>
              decide.mutate({ project, slug, option: index, note: note || undefined }, { onSettled })
            }
          >
            {label}
          </button>
        ))}
      </div>
      {row.detail ? (
        <details>
          <summary className="text-meta text-muted">Why</summary>
          <p className="mt-2 whitespace-pre-wrap text-meta text-ink-2">{row.detail}</p>
        </details>
      ) : null}
    </article>
  );
}

function L3Card({ project, data }: { project: string; data: Record<string, unknown> }) {
  const reset = useL3Reset(project);
  const l3 = dict(data.l3);
  const config = dict(data.config);
  const hold = dict(data.hold);
  const sessionId = str(l3.session_id);
  const summary = sessionId
    ? [
        `session ${sessionId.slice(0, 8)}`,
        `${num(l3.turns) ?? 0} turns`,
        `context ${num(l3.context_percent) ?? 0}%`,
        `last ${age(l3.last_turn) || "—"}`,
        ...(l3.rotate_next ? ["rotates next turn"] : []),
      ].join(" · ")
    : "not started yet — send a chat message";
  return (
    <section className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="label">L3</h2>
        <span className="text-meta text-muted">{summary}</span>
        {data.busy ? <span className="pill">thinking…</span> : null}
        <Link className="btn ml-auto" to={`/chat/${project}`}>
          Chat
        </Link>
        <button
          type="button"
          className="btn"
          disabled={reset.isPending}
          onClick={() => reset.mutate()}
        >
          Rotate
        </button>
      </div>
      <p className="text-meta text-muted">
        approval: {str(config.approval) || "default"} · WIP{" "}
        {num(config.wip) ?? "—"}
      </p>
      {data.hold ? (
        <p className="text-meta text-danger">hold: {str(hold.reason) || "on hold"}</p>
      ) : null}
    </section>
  );
}

export default function Project() {
  const params = useParams();
  const name = params.name ?? "";
  const project = useProject(name);

  if (project.isPending) return <p className="text-muted">Loading…</p>;
  if (project.isError) return <p className="text-danger">{project.error.message}</p>;

  const data = project.data;
  const raw = dict(data);
  const tasks = data.tasks;
  const decisions = data.decisions ?? [];
  const fyis = [...(data.inbox ?? [])].reverse().slice(0, 10);
  const incidents = [...(data.incidents ?? [])].reverse();
  const archive = data.archive ?? [];
  const designViewer = str(raw.design_viewer); // the server answers with the URL, or nothing at all

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="flex flex-wrap items-center gap-3">
        <Link className="text-meta" to="/projects">
          ‹ Projects
        </Link>
        <h1 className="text-page-title font-semibold">{data.name}</h1>
        {/* Absent unless the project checkout has boards to show. Hover draws the accent border,
            a press fills with the accent tint, and the shell's :focus-visible ring is the keyboard
            state; the label is already accent, so only those two carry the change. A new tab, so
            the boards' own pan/zoom keys never fight this page's. */}
        {designViewer ? (
          <a
            className="btn ml-auto hover:border-accent active:bg-accent-tint"
            href={designViewer}
            target="_blank"
            rel="noreferrer"
          >
            Design
          </a>
        ) : null}
      </header>

      <L3Card project={name} data={raw} />

      {decisions.length > 0 ? (
        <section className="space-y-3">
          <h2 className="label">Needs you ({decisions.length})</h2>
          {decisions.map((row, index) => (
            <DecisionCard key={str(row.slug) || index} project={name} row={row} />
          ))}
        </section>
      ) : null}

      <section className="space-y-3">
        <h2 className="label">Tasks ({tasks.length})</h2>
        {tasks.length === 0 ? (
          <p className="text-muted">No open tasks.</p>
        ) : (
          tasks.map((task) => <TaskCard key={`${name}:${task.slug}`} project={name} task={task} />)
        )}
      </section>

      <section className="space-y-2">
        <h2 className="label">Recent FYIs</h2>
        {fyis.length === 0 ? (
          <p className="text-muted">None yet.</p>
        ) : (
          <ul className="space-y-1">
            {fyis.map((fyi, index) => (
              <li key={`${str(fyi.at)}-${index}`} className="flex items-baseline gap-2 text-body">
                <span className="text-meta text-muted">
                  {age(fyi.at) || "—"}
                  {str(fyi.slug) ? ` · ${str(fyi.slug)}` : ""}
                </span>
                <span className="text-ink-2">{str(fyi.text)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {incidents.length > 0 ? (
        <section className="space-y-2">
          <h2 className="label">Incidents</h2>
          <ul className="space-y-1">
            {incidents.map((incident, index) => (
              <li key={str(incident.id) || index} className="text-body">
                <span className="font-semibold">{str(incident.id)}</span> {str(incident.title)}{" "}
                <span className="text-meta text-muted">
                  {arr(incident.tags).map(String).join(", ") || "evidence"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <details className="space-y-2">
        <summary className="label">Done / rejected ({archive.length})</summary>
        <ul className="mt-2 space-y-1 text-meta text-muted">
          {[...archive].reverse().map((task) => (
            <li key={task.slug}>
              {task.slug} {task.state} — {task.title}
            </li>
          ))}
        </ul>
      </details>

      <details>
        <summary className="label">STATE.md</summary>
        <pre className="mt-2 overflow-x-auto text-meta text-ink-2">{data.state_md ?? ""}</pre>
      </details>
    </div>
  );
}
