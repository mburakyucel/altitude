import { Link } from "react-router";
import { StatusMark } from "./StatusMark";
import { ApiError, useProjectModelSettings, useResetProjectModelSetting } from "../data/api";
import type { Choice, ChoiceOptions, ProjectModelSettings } from "../data/api";


type Engines = { engine?: string; value?: string; label: string }[];

/** A saved choice in the same words its controls use, including inherited engine defaults. */
export function choiceLabel(choice: Choice, options: ChoiceOptions | undefined, engines: Engines = []): string {
  if (!choice || (!choice.engine && !choice.effort)) return "Auto";
  const engineLabel = (engine: string) => engines.find((e) => (e.engine ?? e.value) === engine)?.label ?? engine;
  const option = options?.models.find((m) => m.engine === choice.engine && (m.model ?? null) === (choice.model ?? null));
  const model = !choice.engine ? "Auto" : option?.label ?? (choice.model ? `${choice.model} · ${engineLabel(choice.engine)}` : `${engineLabel(choice.engine)} default`);
  const effort = choice.effort ? effortLabel(choice.effort, options) : "";
  return [model, effort].filter(Boolean).join(" · ");
}

export function effortLabel(value: string, options: ChoiceOptions | undefined) {
  return Object.values(options?.efforts ?? {}).flat().find((e) => e.value === value)?.label ?? value;
}

/** The model a choice names, without its effort. */
export function modelName(choice: Choice, options: ChoiceOptions | undefined, engines: Engines) {
  return choiceLabel(choice && { engine: choice.engine, model: choice.model }, options, engines);
}

function SaveResult({ save, reload, retry }: { save: { isPending: boolean; error: Error | null }; reload: () => void; retry: () => void }) {
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const denied = save.error instanceof ApiError && [401, 403].includes(save.error.status);
  return <>{save.isPending ? <StatusMark busy label="Resetting…" /> : null}
    {save.error ? <p role="alert" className="text-meta text-danger">{conflict ? "Changed in another window." : save.error.message}{" "}
      {!denied ? <button className="link" onClick={conflict ? reload : retry}>{conflict ? "Reload" : "Retry"}</button> : null}</p> : null}</>;
}

function ProjectModelSetting({ row }: { row: ProjectModelSettings["rows"][number] }) {
  const save = useResetProjectModelSetting();
  const query = useProjectModelSettings();
  // A press or its Retry: the last reset removes the whole section, so focus goes to the page's heading instead.
  const reset = () => save.mutate({ id: row.id, expected: row.expected }, {
    onSuccess: (result) => (result.rows.length ? document.getElementById("project-model-settings-heading") : document.querySelector<HTMLElement>("h1"))?.focus(),
  });
  return <div className="settings-row" role="group" aria-label={row.label}>
    <span><strong>{row.label}</strong><small className="model-detail">{row.detail}</small>
      <SaveResult save={save} reload={() => { save.reset(); void query.refetch(); }} retry={reset} /></span>
    {row.editable ? <button className="link" disabled={save.isPending} onClick={reset}>Reset</button>
      : <Link to={`/settings/projects/${encodeURIComponent(row.project)}#routing`}>Routing details</Link>}
  </div>;
}

/**
 * A project's own model and effort for a role and engine, which apply only where a model button says Default or
 * Auto. Listed only when any exist; the coordinator and New tasks choices live on their buttons.
 */
export function ProjectModelSettingsSection() {
  const query = useProjectModelSettings();
  if (query.isError) return <section className="settings-section" aria-label="Project settings"><p role="alert">Could not load project model settings. <button className="link" onClick={() => void query.refetch()}>Retry</button></p></section>;
  if (!query.data?.rows.length) return null;
  return <section className="settings-section" aria-labelledby="project-model-settings-heading">
    <h2 id="project-model-settings-heading" tabIndex={-1}>Project settings</h2>
    <p className="text-meta text-muted">Used where a model button says Default or Auto.</p>
    <div className="settings-group">{query.data.rows.map((row) => <ProjectModelSetting key={row.id} row={row} />)}</div>
  </section>;
}
