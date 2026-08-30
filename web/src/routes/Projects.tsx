import { useState } from "react";
import { Link } from "react-router";
import { useOverview, useProjectAdd, useProjectRemove } from "../data/api";
import type { ProjectRow } from "../data/api";

/** The task states the row summarises, in the order the 0.1 app showed them. */
const STATES = [
  "blocked",
  "running",
  "reported",
  "proposed",
  "approved",
  "requested",
  "parked",
] as const;

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

/** The loose bits of the payload (l3, hold) are typed `unknown`: narrow before reading. */
function dict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function l3Summary(l3: unknown): string {
  const info = dict(l3);
  if (!str(info.session_id)) return "L3 not started";
  const context = num(info.context_percent) ?? 0;
  const last = age(info.last_turn);
  return `L3 ${context}%${last ? ` · ${last}` : ""}`;
}

function ManagedRow({ project }: { project: ProjectRow }) {
  const [confirming, setConfirming] = useState(false);
  const remove = useProjectRemove();
  const counts = project.counts ?? {};
  const active = STATES.filter((state) => (counts[state] ?? 0) > 0);
  const hold = dict(project.hold);
  const holdReason = str(hold.reason);

  return (
    <article className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Link className="text-card-title font-semibold" to={`/projects/${project.name}`}>
          {project.name}
        </Link>
        {project.hold ? (
          <span className="pill text-danger">{holdReason ? `hold: ${holdReason}` : "hold"}</span>
        ) : null}
        <span className="ml-auto text-meta text-muted">{l3Summary(project.l3)}</span>
        {confirming ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate({ name: project.name });
                setConfirming(false);
              }}
            >
              Confirm remove
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn"
            aria-label={`Remove ${project.name}`}
            onClick={() => setConfirming(true)}
          >
            Remove
          </button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {active.length > 0 ? (
          active.map((state) => (
            <span key={state} className="pill">
              {state} {counts[state]}
            </span>
          ))
        ) : (
          <span className="text-meta text-muted">no tasks</span>
        )}
      </div>
    </article>
  );
}

function UnmanagedRow({ project }: { project: ProjectRow }) {
  const [stacks, setStacks] = useState("");
  const add = useProjectAdd();
  return (
    <article className="card space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-card-title font-semibold">{project.name}</span>
        <span className="ml-auto text-meta text-muted">{project.git ? "git" : "no git"}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="field flex-1"
          placeholder="Stacks (comma-separated, optional)"
          aria-label={`Stacks for ${project.name}`}
          value={stacks}
          onChange={(event) => setStacks(event.target.value)}
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={add.isPending}
          onClick={() =>
            add.mutate({
              name: project.name,
              path: project.path ?? undefined,
              stacks: stacks.trim() || undefined,
            })
          }
        >
          Start L3
        </button>
      </div>
    </article>
  );
}

function AddProject() {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [stacks, setStacks] = useState("");
  const add = useProjectAdd();
  return (
    <form
      className="card space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!name.trim()) return;
        add.mutate({
          name: name.trim(),
          path: path.trim() || undefined,
          stacks: stacks.trim() || undefined,
        });
        setName("");
        setPath("");
        setStacks("");
      }}
    >
      <h2 className="label">Add a project</h2>
      <div className="flex flex-wrap gap-2">
        <input
          className="field flex-1"
          placeholder="Name"
          aria-label="Project name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <input
          className="field flex-1"
          placeholder="Path (optional)"
          aria-label="Project path"
          value={path}
          onChange={(event) => setPath(event.target.value)}
        />
        <input
          className="field flex-1"
          placeholder="Stacks (optional)"
          aria-label="Project stacks"
          value={stacks}
          onChange={(event) => setStacks(event.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={add.isPending || !name.trim()}>
          Add
        </button>
      </div>
    </form>
  );
}

export default function Projects() {
  const overview = useOverview();
  if (overview.isPending) return <p className="text-muted">Loading…</p>;
  if (overview.isError) return <p className="text-danger">{overview.error.message}</p>;
  const managed = overview.data.projects.filter((p) => p.managed);
  const unmanaged = overview.data.projects.filter((p) => !p.managed);
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="flex items-baseline gap-3">
        <h1 className="text-page-title font-semibold">Projects</h1>
        <span className="text-meta text-muted">{managed.length} managed</span>
      </header>
      <section className="space-y-3">
        <h2 className="label">Managed ({managed.length})</h2>
        {managed.length === 0 ? (
          <p className="text-muted">No managed projects yet.</p>
        ) : (
          managed.map((project) => <ManagedRow key={project.name} project={project} />)
        )}
      </section>
      <AddProject />
      {unmanaged.length > 0 ? (
        <section className="space-y-3">
          <h2 className="label">Not managed ({unmanaged.length})</h2>
          {unmanaged.map((project) => (
            <UnmanagedRow key={project.path ?? project.name} project={project} />
          ))}
        </section>
      ) : null}
    </div>
  );
}
