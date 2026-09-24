import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useOverview, useProject, useProjectDefaults, useSetDefault } from "../data/api";
import type { ProjectDefaults } from "../data/api";
import { L3EngineSelect } from "../components/L3EngineSelect";
import { useViewport } from "../shell/breakpoints";
import "./settings.css";

type Engine = ProjectDefaults["roles"][number]["engines"][number];

const roles = {
  l3: { title: "L3 · project conversation", timing: "Applies from L3's next turn, in its existing conversation." },
  l2: { title: "L2 · task owners", timing: "Applies to fresh task attempts. Started tasks keep their model and effort." },
};

function SaveStatus({ save, retry }: { save: ReturnType<typeof useSetDefault>; retry: () => void }) {
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
    <label>Model
      <input value={draft ?? saved} placeholder={`Default: ${field.default}`} list={list} disabled={save.isPending}
        spellCheck={false} autoCapitalize="off"
        onChange={(event) => { setDraft(event.target.value); save.reset(); }} onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); commit(); }
          if (event.key === "Escape") { setDraft(null); save.reset(); }
        }} />
    </label>
    {list ? <datalist id={list}>{field.choices.map((choice) => <option key={choice} value={choice} />)}</datalist> : null}
    <SaveStatus save={save} retry={() => save.variables && save.mutate(save.variables)} />
  </div>;
}

function EffortField({ name, engine }: { name: string; engine: Engine }) {
  const field = engine.effort;
  const save = useSetDefault(name);
  const shown = save.isPending ? save.variables.value : field.value;
  return <div className="default-field">
    <label>Effort
      <select value={shown ?? ""} disabled={save.isPending}
        onChange={(event) => save.mutate({ setting: field.setting, value: event.target.value || null })}>
        <option value="">Default ({field.default})</option>
        {field.choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
      </select>
    </label>
    <SaveStatus save={save} retry={() => save.variables && save.mutate(save.variables)} />
  </div>;
}

function Session({ name }: { name: string }) {
  const project = useProject(name);
  const overview = useOverview();
  const l3 = project.data?.l3 ?? {};
  const text = (key: string) => typeof l3[key] === "string" ? l3[key] as string : "";
  const engine = text("engine_last");
  if (!project.data) return null;
  if (!engine) return <p className="text-meta text-muted">L3 has not started.</p>;
  const label = overview.data?.engines.find((e) => e.engine === engine)?.label ?? engine;
  const effort = (value: string) => value === "xhigh" ? "Extra High" : value.charAt(0).toUpperCase() + value.slice(1);
  const launched = text("launch_effort"), reported = text("engine_reasoning_effort");
  return <p className="text-meta text-muted">
    Last turn: {[label, text("engine_model") || "model not reported",
      launched ? `${effort(launched)} effort requested` : "engine default effort",
      reported ? `engine reported ${effort(reported)}` : "applied effort not reported"].join(" · ")}
  </p>;
}

/** One project's settings: the L3 engine and a model/effort pair per role and engine. */
export default function ProjectSettings() {
  const { name = "" } = useParams();
  const { phone } = useViewport();
  const location = useLocation();
  const overview = useOverview();
  const defaults = useProjectDefaults(name);
  const back = <Link to="/settings" state={location.state} className="btn settings-back">‹ Settings</Link>;
  return <>
    {phone ? <header className="phone-header settings-header">{back}<h1>{name}</h1></header> : null}
    <div className="page settings-page">
      {!phone ? <>{back}<h1>{name}</h1></> : null}
      <p className="text-meta text-muted">Applies to this project only. A model or effort chosen for one launch, such as a task created with its own effort, wins over these defaults.</p>
      {defaults.isPending ? <p role="status">Loading settings…</p>
        : defaults.isError ? <p role="alert" className="text-danger">Could not load settings. <button className="link" onClick={() => void defaults.refetch()}>Retry</button></p>
          : <>
            <section className="settings-card" aria-label="L3 engine">
              <label className="default-engine"><strong>L3 engine</strong>
                <L3EngineSelect name={name} engine={defaults.data.l3_engine ?? ""} engines={overview.data?.engines ?? []} />
              </label>
              <p className="text-meta text-muted">Auto follows the project's routing preferences and available quota.</p>
              <Session name={name} />
            </section>
            {defaults.data.roles.map((row) => <section key={row.role} className="settings-card" aria-label={roles[row.role].title}>
              <h2>{roles[row.role].title}</h2>
              <p className="text-meta text-muted">{roles[row.role].timing}</p>
              {row.engines.map((engine) => <div key={engine.engine} className="default-engine-row" role="group" aria-label={`${row.role.toUpperCase()} on ${engine.label}`}>
                <h3>{engine.label}</h3>
                <ModelField name={name} engine={engine} />
                <EffortField name={name} engine={engine} />
              </div>)}
            </section>)}
            <p className="text-meta text-muted">These choices request a model and effort; they do not confirm what the engine used. Higher effort can use more time and tokens.</p>
          </>}
    </div>
  </>;
}
