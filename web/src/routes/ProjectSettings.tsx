import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, forgetProject, overviewQuery, useOverview, useProject, useProjectDefaults, useProjectRemove, useSetDefault } from "../data/api";
import type { Overview, ProjectDefaults } from "../data/api";
import { ModelsDialog, choiceLabel, closedLabel } from "../components/Models";
import { Overlay } from "../shell/Overlay";
import { managedProjects } from "../shell/projects";
import { useViewport } from "../shell/breakpoints";
import { useSetupReading } from "./Setup";
import "./settings.css";

type Engine = ProjectDefaults["roles"][number]["engines"][number];
type Save = ReturnType<typeof useSetDefault>;

const roleTitle = { l2: "Tasks", l3: "L3" } as const;

function SaveStatus({ save, retry }: { save: Save; retry: () => void }) {
  if (save.isError) return <p className="text-meta text-danger" role="alert">
    {save.error.message} <button type="button" className="link" onClick={retry}>Retry save</button>
  </p>;
  return save.isPending || save.isSuccess ? <p className="text-meta text-muted" role="status">{save.isPending ? "Saving…" : "Saved."}</p> : null;
}

/** Free text with suggestions: Enter or leaving the field saves, Escape restores, empty means Default. */
function ModelField({ name, engine }: { name: string; engine: Engine }) {
  const field = engine.model;
  const save = useSetDefault(name);
  const [draft, setDraft] = useState<string | null>(null);
  const saved = field.value ?? "";
  // Retire the draft once the saved value renders, so the field never flashes the previous one.
  useEffect(() => { setDraft((current) => current !== null && current.trim() === saved ? null : current); }, [saved]);
  const commit = () => {
    const value = draft?.trim() ?? saved;
    if (value !== saved) save.mutate({ setting: field.setting, value: value || null });
    else setDraft(null);
  };
  const list = field.choices.length ? `${field.setting}-choices` : undefined;
  return <div className="default-field">
    <input aria-label="Model" value={draft ?? saved} placeholder={`Default: ${field.default}`} list={list} disabled={save.isPending}
      spellCheck={false} autoCapitalize="off"
      onChange={(event) => { setDraft(event.target.value); save.reset(); }} onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") { event.preventDefault(); commit(); }
        if (event.key === "Escape") { setDraft(null); save.reset(); }
      }} />
    {list ? <datalist id={list}>{field.choices.map((choice) => <option key={choice} value={choice} />)}</datalist> : null}
    <SaveStatus save={save} retry={() => save.variables && save.mutate(save.variables)} />
  </div>;
}

function EffortField({ name, engine }: { name: string; engine: Engine }) {
  const field = engine.effort;
  const save = useSetDefault(name);
  const shown = save.isPending ? save.variables.value as string | null : field.value;
  return <div className="default-field">
    <select aria-label="Effort" value={shown ?? ""} disabled={save.isPending}
      onChange={(event) => save.mutate({ setting: field.setting, value: event.target.value || null })}>
      <option value="">Default ({field.default})</option>
      {field.choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
    </select>
    <SaveStatus save={save} retry={() => save.variables && save.mutate(save.variables)} />
  </div>;
}

/** The L3 choice: the same value as the message box's button, with Back to Auto and Change…. */
function L3Section({ name, data }: { name: string; data: ProjectDefaults }) {
  const [open, setOpen] = useState(false);
  const save = useSetDefault(name);
  const client = useQueryClient();
  const project = useProject(name);
  const l3 = project.data?.l3 ?? {};
  const text = (key: string) => typeof l3[key] === "string" ? l3[key] as string : "";
  const effort = (value: string) => Object.values(data.efforts).flat().find((e) => e.value === value)?.label ?? value;
  const last = !text("engine_last") ? "L3 has not replied yet"
    : `last reply reported ${[text("engine_model") || "no model", text("engine_reasoning_effort") ? effort(text("engine_reasoning_effort")) : "no effort"].join(" · ")}`;
  return <section className="settings-section" aria-labelledby="settings-l3">
    <div><h2 id="settings-l3">L3</h2><p className="text-meta text-muted">Who answers in this project's chat. The same choice as the button under the message box; it comes before routing.</p></div>
    <div className="settings-group">
      <div className="settings-row">
        <span><strong>{closedLabel(data.l3_choice, data.l3_unavailable, data, data.engines)}</strong>{" "}
          <small>{data.l3_choice ? "Until you choose Auto" : "Project routing and defaults"} · {last}</small>
          {data.l3_unavailable && data.l3_choice ? <small>{data.l3_unavailable}</small> : null}
          {save.isError ? <small role="alert" className="text-danger">{save.error.message}</small> : null}
        </span>
        <span className="settings-actions">
          {data.l3_choice ? <button type="button" className="link" disabled={save.isPending}
            onClick={() => save.mutate({ setting: "l3_choice", value: null, expected: data.l3_choice },
              { onError: () => void client.invalidateQueries({ queryKey: ["defaults", name] }) })}>{save.isPending ? "Saving…" : "Back to Auto"}</button> : null}
          <button type="button" className="btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>Change…</button>
        </span>
      </div>
    </div>
    {open ? <ModelsDialog project={name} tab="l3" onClose={() => setOpen(false)} /> : null}
  </section>;
}

/** Model and effort per role and engine, used under Auto and as the fallback. */
function DefaultsSection({ name, data }: { name: string; data: ProjectDefaults }) {
  const overview = useOverview();
  const tasks = overview.data?.new_tasks;
  const rows = [...data.roles].sort((a, b) => (a.role === "l2" ? -1 : 1) - (b.role === "l2" ? -1 : 1));
  return <section className="settings-section" aria-labelledby="settings-defaults">
    <div><h2 id="settings-defaults">Auto defaults</h2><p className="text-meta text-muted">
      Used under Auto and as the fallback.{tasks?.value ? ` Tasks now start on New tasks: ${choiceLabel(tasks.value, tasks, overview.data?.engines)}.` : ""}
      {" "}Started tasks keep their model and effort; L3 uses a change from its next reply.</p></div>
    <div className="settings-group defaults-table">
      <div className="defaults-head" aria-hidden><span /><span>Model</span><span>Effort</span></div>
      {rows.flatMap((row) => row.engines.map((engine) => {
        const label = `${roleTitle[row.role]} · ${engine.label}`;
        return <div key={`${row.role}-${engine.engine}`} className="defaults-row" role="group" aria-label={label}>
          <span className="defaults-label">{label}</span>
          <ModelField name={name} engine={engine} />
          <EffortField name={name} engine={engine} />
        </div>;
      }))}
    </div>
    <p className="text-meta text-muted">These request a model and effort; the task and chat show what the engine reported. Higher effort can use more time and tokens.</p>
  </section>;
}

/** Auto, Prefer or Only an engine for tasks; Auto or Only an engine for L3. */
function RoutingSection({ name, data }: { name: string; data: ProjectDefaults }) {
  const save = useSetDefault(name);
  const client = useQueryClient();
  const [error, setError] = useState<Error | null>(null);
  const label = (engine: string) => data.engines.find((e) => e.value === engine)?.label ?? engine;
  const tasks = data.l2_engine ? `only:${data.l2_engine}` : data.l2_preference ? `prefer:${data.l2_preference}` : "";
  const l3 = data.l3_engine ? `only:${data.l3_engine}` : "";
  const write = async (changes: [string, string | null, string | null | undefined][]) => {
    setError(null);
    try {
      for (const [setting, value, expected] of changes)
        if (value !== (expected ?? null)) await save.mutateAsync({ setting, value, expected: expected ?? null });
    } catch (failure) {
      setError(failure as Error);
      // A refused or half-applied change shows what is stored now, not the choice that was refused.
      void client.invalidateQueries({ queryKey: ["defaults", name] });
    }
  };
  const chooseTasks = (value: string) => {
    const [kind, engine = null] = value ? value.split(":") : [""];
    void write([
      ["l2_engine", kind === "only" ? engine : null, data.l2_engine],
      ["l2_preference", kind === "prefer" ? engine : null, data.l2_preference],
    ]);
  };
  const preferred = data.l2_preference ? data.engines.find((e) => e.value === data.l2_preference) : undefined;
  const changed = error instanceof ApiError && error.status === 409;
  return <section className="settings-section" id="routing" aria-labelledby="settings-routing">
    <div><h2 id="settings-routing">Routing</h2><p className="text-meta text-muted">
      {data.routing ? `The order Auto tries. Custom routing: ${data.routing}.` : `The order Auto tries: ${data.engines.map((e) => e.label).join(" and ")} share work by weekly headroom.`}</p></div>
    <div className="settings-group">
      <label className="settings-row">
        <span><strong>Tasks</strong>{" "}<small>Auto, Prefer or Only an engine. Only keeps tasks on it, even with New tasks set; they wait while it is unavailable.</small>
          {preferred && !preferred.routed && !data.l2_engine ? <small>{preferred.label} is not in this project's custom routing, so Prefer has no effect.</small> : null}</span>
        <select className="settings-select" aria-label="Tasks routing" value={tasks} disabled={save.isPending} onChange={(event) => chooseTasks(event.target.value)}>
          <option value="">Auto</option>
          {data.engines.map((e) => <option key={`p-${e.value}`} value={`prefer:${e.value}`}>Prefer {e.label}</option>)}
          {data.engines.map((e) => <option key={`o-${e.value}`} value={`only:${e.value}`}>Only {e.label}</option>)}
        </select>
      </label>
      <label className="settings-row">
        <span><strong>L3</strong>{" "}<small>Auto or Only an engine. Only keeps L3 on it, even with an L3 choice; it waits while it is unavailable.</small></span>
        <select className="settings-select" aria-label="L3 routing" value={l3} disabled={save.isPending}
          onChange={(event) => void write([["l3_engine", event.target.value ? event.target.value.slice(5) : null, data.l3_engine]])}>
          <option value="">Auto</option>
          {data.engines.map((e) => <option key={e.value} value={`only:${e.value}`}>Only {label(e.value)}</option>)}
        </select>
      </label>
    </div>
    {save.isPending ? <p role="status" className="text-meta text-muted">Saving…</p> : null}
    {changed ? <p role="alert" className="text-meta text-danger">Changed in another window. The page shows the current routing.</p>
      : error ? <p role="alert" className="text-meta text-danger">{error.message}</p> : null}
  </section>;
}

type Removal = { status: "idle" | "removing" | "checking" | "unknown" | "unconfirmed" } | { status: "refused"; message: string };

/**
 * Remove project (SPEC.md §3.15): Cancel first and focused, a red named action, and what stays. A response
 * lost on the way rereads the project list before saying anything, so a removal that happened is not retried.
 */
function RemoveDialog({ name, removal, onRemove, onCheck, onClose }: {
  name: string; removal: Removal; onRemove: () => void; onCheck: () => void; onClose: () => void;
}) {
  const busy = removal.status === "removing" || removal.status === "checking";
  return <Overlay label={`Remove ${name} from Altitude?`} side="center" onClose={onClose}>
    <div className="remove-dialog">
      <h2>Remove {name} from Altitude?</h2>
      <p>L3 is detached and Altitude stops managing this folder.</p>
      <ul>
        <li>Stays on disk: the repository, worktrees, history and queued messages.</li>
        <li>Not kept: its settings here, such as models and routing.</li>
        <li>Undo: add the same folder as {name} again to reattach L3 with its history.</li>
        <li>Unfinished tasks and a running L3 reply must finish first.</li>
      </ul>
      {removal.status === "refused" ? <p role="alert" className="text-danger">{removal.message}</p> : null}
      {removal.status === "unconfirmed" ? <p role="alert" className="text-danger">Couldn't confirm removal; {name} is still in Altitude.</p> : null}
      {removal.status === "unknown" ? <p role="alert" className="text-danger">Couldn't confirm removal, and the project list could not be read.</p> : null}
      {busy ? <p role="status" className="text-meta text-muted">{removal.status === "checking" ? "Checking whether it was removed…" : "Removing…"} Closing doesn't cancel removal.</p> : null}
      <div className="remove-actions">
        <button type="button" className="btn" data-autofocus disabled={busy} onClick={onClose}>Cancel</button>
        {/* Removing again is offered only once the project is known to be present. */}
        {removal.status === "unknown" ? <button type="button" className="btn" onClick={onCheck}>Check again</button>
          : <button type="button" className="btn btn-danger" disabled={busy} onClick={onRemove}>
            {busy ? "Removing…" : removal.status === "unconfirmed" ? "Retry" : `Remove ${name}`}
          </button>}
      </div>
    </div>
  </Overlay>;
}

function ProjectSection({ name }: { name: string }) {
  const setup = useSetupReading(name);
  const remove = useProjectRemove();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [removal, setRemoval] = useState<Removal>({ status: "idle" });
  const leave = () => {
    const remaining = managedProjects(client.getQueryData<Overview>(["overview"]));
    navigate(remaining.length ? "/" : "/projects", { replace: true });
  };
  const run = async () => {
    setRemoval({ status: "removing" });
    try {
      await remove.mutateAsync({ name });
      leave();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500) return setRemoval({ status: "refused", message: failure.message });
      await check();
    }
  };
  const check = async () => {
    setRemoval({ status: "checking" });
    const overview = await client.fetchQuery({ ...overviewQuery, staleTime: 0 }).catch(() => null);
    if (!overview) return setRemoval({ status: "unknown" });
    if (managedProjects(overview).some((row) => row.name === name)) return setRemoval({ status: "unconfirmed" });
    await forgetProject(client, name);
    leave();
  };
  return <section className="settings-section" aria-labelledby="settings-project">
    <h2 id="settings-project">Project</h2>
    <div className="settings-group">
      <Link className="settings-row" to={`/projects/${encodeURIComponent(name)}?setup=1`}>
        <span><strong>Setup</strong>{" "}<small>{setup.label}</small></span><span aria-hidden>›</span>
      </Link>
      <div className="settings-row">
        <span><strong>Remove project</strong>{" "}<small>Detach L3. Files and history stay; its settings here don't.</small></span>
        <button type="button" className="btn btn-outline-danger" aria-haspopup="dialog" onClick={() => { setRemoval({ status: "idle" }); setOpen(true); }}>Remove…</button>
      </div>
    </div>
    {open ? <RemoveDialog name={name} removal={removal} onRemove={() => void run()} onCheck={() => void check()} onClose={() => setOpen(false)} /> : null}
  </section>;
}

/** One project's settings (SPEC.md §3.15): L3, Auto defaults, Routing and the project itself. */
export default function ProjectSettings() {
  const { name = "" } = useParams();
  const { phone } = useViewport();
  const location = useLocation();
  const defaults = useProjectDefaults(name);
  const overview = useOverview();
  const managed = managedProjects(overview.data).some((row) => row.name === name);
  const back = <Link to="/settings" state={location.state} className="btn settings-back">‹ Settings</Link>;
  useEffect(() => {
    if (defaults.data && location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [defaults.data, location.hash]);
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1>{name}</h1></header> : null}
    <div className="page settings-page">
      {!phone ? <>{back}<h1>{name}</h1></> : null}
      <p className="text-meta text-muted">Applies to this project only. A model or effort L3 sets for one task wins over these.</p>
      {defaults.isPending ? <p role="status">Loading settings…</p>
        : defaults.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void defaults.refetch()}>Retry</button></p>
          : <>
            <L3Section name={name} data={defaults.data} />
            <DefaultsSection name={name} data={defaults.data} />
            <RoutingSection name={name} data={defaults.data} />
          </>}
      {/* Removal does not depend on reading the model settings. */}
      {managed ? <ProjectSection name={name} /> : null}
    </div>
  </>;
}
