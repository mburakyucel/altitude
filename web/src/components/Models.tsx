import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { StatusMark } from "./StatusMark";
import { ApiError, useOverview, useModelDefaults, useSaveModelDefaults, useResetModelOverride, useProjectDefaults, useSaveNewTasks, useSetDefault } from "../data/api";
import type { Choice, ChoiceOptions, Overview, ModelDefaults, ModelPair } from "../data/api";


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

function effortLabel(value: string, options: ChoiceOptions | undefined) {
  return Object.values(options?.efforts ?? {}).flat().find((e) => e.value === value)?.label ?? value;
}

/** The model a choice names, without its effort. */
function modelName(choice: Choice, options: ChoiceOptions | undefined, engines: Engines) {
  return choiceLabel(choice && { engine: choice.engine, model: choice.model }, options, engines);
}

/** The closed control's text: the choice, or why it cannot start now. */
export function closedLabel(choice: Choice, unavailable: string | null | undefined, options: ChoiceOptions | undefined, engines: Engines = []) {
  return unavailable && choice?.engine ? `${modelName(choice, options, engines)} unavailable · Auto meanwhile` : choiceLabel(choice, options, engines);
}

const modelKey = (choice: Choice) => choice?.engine ? JSON.stringify([choice.engine, choice.model ?? null]) : "";

/** Native selection shared by Settings role choices and per-engine defaults. Other fields commit as one group. */
export function ModelSelect({ value, models, engines, emptyLabel, busy, onChange, label: accessibleLabel = "Model" }: {
  label?: string; value: Choice; models: ChoiceOptions["models"]; engines: Engines; emptyLabel: string; busy: boolean;
  onChange: (value: Choice, expected: Choice) => Promise<unknown>;
}) {
  const [other, setOther] = useState<{ engine: string; model: string; base: Choice; submitted: boolean } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const committing = useRef(false);
  const select = useRef<HTMLSelectElement>(null);
  const custom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!busy && other?.submitted && modelKey(value) === modelKey({ engine: other.engine, model: other.model.trim() })) {
      if (custom.current?.contains(document.activeElement)) select.current?.focus();
      setOther(null);
    }
  }, [value, busy, other]);
  const engineRows = engines.map((e) => ({ engine: e.engine ?? e.value ?? "", label: e.label }));
  const label = (engine: string) => engineRows.find((e) => e.engine === engine)?.label ?? engine;
  const listed = !value?.model || models.some((m) => modelKey(m) === modelKey(value));
  const commit = async () => {
    if (!other || other.submitted || busy || committing.current) return;
    const model = other.model.trim();
    if (!model) { setOther(null); setInvalid(false); return; }
    if (/\s/.test(model)) { setInvalid(true); return; }
    if (modelKey({ engine: other.engine, model }) === modelKey(other.base)) { setOther(null); return; }
    committing.current = true;
    setOther({ ...other, submitted: true });
    try {
      await onChange({ engine: other.engine, model }, other.base);
      if (custom.current?.contains(document.activeElement)) select.current?.focus();
      setOther(null);
      setInvalid(false);
    } catch { /* The owning field presents the server error and retry. */ }
    finally { committing.current = false; }
  };
  return <div className="default-field">
    <select ref={select} aria-label={accessibleLabel} value={other ? "other" : value?.model ? modelKey(value) : ""} aria-disabled={busy}
      onPointerDown={(event) => { if (busy) event.preventDefault(); }}
      onKeyDown={(event) => { if (busy && event.key !== "Tab") event.preventDefault(); }}
      onChange={(event) => {
        if (busy) return;
        setInvalid(false);
        if (event.target.value === "other") {
          setOther({ engine: value?.engine ?? engineRows[0]?.engine ?? "", model: value?.model ?? "", base: value, submitted: false });
          return;
        }
        setOther(null);
        const next = models.find((m) => modelKey(m) === event.target.value);
        void onChange(next ? { engine: next.engine, ...(next.model ? { model: next.model } : {}) } : null, value).catch(() => {});
      }}>
      <option value="">{emptyLabel}</option>
      {models.map((m) => <option key={modelKey(m)} value={modelKey(m)}>
        {m.model ? engineRows.length === 1 ? m.label : `${m.label} · ${label(m.engine)}` : m.label}
      </option>)}
      {!listed ? <option value={modelKey(value)}>{value?.model || "Project default"} · {label(value!.engine!)}</option> : null}
      <option value="other">Other model…</option>
    </select>
    {other ? <div ref={custom} className="model-other-fields" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) void commit();
    }} onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); if (!busy) { setOther(null); setInvalid(false); } }
    }}>
      {engineRows.length > 1 ? <label>Engine<select value={other.engine} aria-disabled={busy}
        onPointerDown={(event) => { if (busy) event.preventDefault(); }}
        onKeyDown={(event) => { if (busy && event.key !== "Tab") event.preventDefault(); }}
        onChange={(event) => { if (!busy) setOther({ ...other, engine: event.target.value, submitted: false }); }}>
        {engineRows.map((e) => <option key={e.engine} value={e.engine}>{e.label}</option>)}
      </select></label> : null}
      <label>Model ID<input value={other.model} readOnly={busy} autoFocus spellCheck={false} autoCapitalize="off" autoComplete="off"
        onChange={(event) => { setOther({ ...other, model: event.target.value, submitted: false }); setInvalid(false); }}
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void commit(); } }} /></label>
      <p className="text-meta text-muted model-detail">Saved: {other.base?.model ?? "Altitude default"}</p>
      {invalid ? <p role="alert" className="text-meta text-danger">Use one model ID without spaces.</p> : null}
    </div> : null}
  </div>;
}

/** Each engine owns one atomic model/effort pair, across projects and roles. */
function ModelPairRow({ row }: { row: ModelDefaults["engines"][number] }) {
  const save = useSaveModelDefaults();
  const query = useModelDefaults();
  const [revision, setRevision] = useState(0);
  const locked = useRef(false);
  const shown = save.isPending ? save.variables.value : row.value;
  const submit = async (value: ModelPair, expected: ModelPair) => {
    if (locked.current) throw new Error("Save in progress");
    locked.current = true;
    try { await save.mutateAsync({ engine: row.engine, value, expected }); }
    finally { locked.current = false; }
  };
  const reload = () => { save.reset(); setRevision((n) => n + 1); void query.refetch(); };
  return <div className="defaults-row" role="group" aria-label={row.label}>
    <span className="defaults-label">{row.label}</span>
    <div className="default-field"><span className="phone-field-label">Model</span>
      <ModelSelect key={revision} label={`${row.label} model`} value={shown.model ? { engine: row.engine, model: shown.model, effort: shown.effort } : { engine: row.engine, effort: shown.effort }}
        models={row.models.map((model) => ({ engine: row.engine, model: model.value, label: model.label }))}
        engines={[{ engine: row.engine, label: row.label }]} emptyLabel="Altitude default" busy={save.isPending}
        onChange={(value, expected) => submit({ model: value?.model ?? null, effort: expected?.effort ?? null }, { model: expected?.model ?? null, effort: expected?.effort ?? null })} />
      <span className="text-meta text-muted model-detail">{shown.model ? `${shown.model} · Access unverified` : row.default_model}</span>
      {row.unavailable ? <span className="text-meta">Unavailable · {row.unavailable}</span> : null}
    </div>
    <div className="default-field"><span className="phone-field-label">Effort</span>
      <select aria-label={`${row.label} effort`} value={shown.effort ?? ""} aria-disabled={save.isPending}
        onPointerDown={(event) => { if (save.isPending) event.preventDefault(); }}
        onKeyDown={(event) => { if (save.isPending && event.key !== "Tab") event.preventDefault(); }}
        onChange={(event) => { if (!locked.current) void submit({ ...row.value, effort: event.target.value || null }, row.value).catch(() => {}); }}>
        <option value="">Altitude default</option>
        {shown.effort && !row.efforts.some((item) => item.value === shown.effort) ? <option value={shown.effort}>{shown.effort}</option> : null}
        {row.efforts.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
      {!shown.effort ? <span className="text-meta text-muted">{row.default_effort}</span> : shown.effort === "native" ? <span className="text-meta text-muted">Provider chooses effort</span> : null}
      <SaveResult save={save} reload={reload} retry={() => save.variables && void submit(save.variables.value, save.variables.expected).catch(() => {})} />
    </div>
  </div>;
}

function SaveResult({ save, reload, retry }: { save: { isPending: boolean; isSuccess: boolean; error: Error | null }; reload: () => void; retry: () => void }) {
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const denied = save.error instanceof ApiError && [401, 403].includes(save.error.status);
  return <>{save.isPending || save.isSuccess ? <StatusMark busy={save.isPending} label={save.isPending ? "Saving…" : "Saved."} /> : null}
    {save.error ? <p role="alert" className="text-meta text-danger">{conflict ? "Changed in another window." : save.error.message}{" "}
      {!denied ? <button className="link" onClick={conflict ? reload : retry}>{conflict ? "Reload" : "Retry save"}</button> : null}</p> : null}</>;
}

function ModelOverride({ row }: { row: ModelDefaults["overrides"][number] }) {
  const save = useResetModelOverride();
  const query = useModelDefaults();
  return <div className="settings-row" role="group" aria-label={row.label}>
    <span><strong>{row.label}</strong><small className="model-detail">{[query.data?.engines.find((item) => item.engine === row.engine)?.label ?? row.engine, row.model, row.effort].filter(Boolean).join(" · ")}</small>
      <SaveResult save={save} reload={() => { save.reset(); void query.refetch(); }} retry={() => save.variables && save.mutate(save.variables)} /></span>
    {row.editable ? <button className="link" disabled={save.isPending} onClick={() => save.mutate({ id: row.id, expected: row.expected }, {
      onSuccess: () => document.getElementById("models-heading")?.focus(),
    })}>Use global defaults</button> : <Link to={`/settings/projects/${encodeURIComponent(row.project ?? "")}#routing`}>Routing details</Link>}
  </div>;
}

export function GlobalModelSettings() {
  const query = useModelDefaults();
  return <section className="settings-section" id="models" aria-labelledby="models-heading">
    <h2 id="models-heading" tabIndex={-1}>Models and effort · All projects</h2>
    <p className="text-meta text-muted">Applies from the next chat reply and when tasks or reviews start. Started tasks keep theirs.</p>
    {query.isError ? <p role="alert">Could not load models. <button className="link" onClick={() => void query.refetch()}>Retry</button></p>
      : !query.data ? <StatusMark label="Loading models…" /> : <>
        <div className="settings-group defaults-table"><div className="defaults-head" aria-hidden><span /><span>Model</span><span>Effort</span></div>
          {query.data.engines.map((row) => <ModelPairRow key={row.engine} row={row} />)}</div>
        {query.data.overrides.length ? <><h3>Existing overrides</h3><div className="settings-group">{query.data.overrides.map((row) => <ModelOverride key={row.id} row={row} />)}</div></> : null}
      </>}
  </section>;
}

/** Routing preference is separate from the engine's global model and effort. */
export function EnginePreference({ project }: { project?: string }) {
  const overview = useOverview();
  const defaults = useProjectDefaults(project ?? "");
  const tasks = useSaveNewTasks();
  const coordinator = useSetDefault(project ?? "");
  const query = project ? defaults : overview;
  const save = project ? coordinator : tasks;
  const saved = project ? defaults.data?.l3_choice : overview.data?.new_tasks?.value;
  const unavailable = project ? defaults.data?.l3_unavailable : overview.data?.new_tasks?.unavailable;
  const engines: Engines = project ? defaults.data?.engines ?? [] : overview.data?.engines ?? [];
  const submit = (value: Choice, expected: Choice) => project ? coordinator.mutateAsync({ setting: "l3_choice", value, expected }) : tasks.mutateAsync({ value, expected });
  const shown = save.isPending ? (save.variables.value as Choice)?.engine : saved?.engine;
  if (query.isError) return <p role="alert">Could not load engines. <button className="link" onClick={() => void query.refetch()}>Retry</button></p>;
  if (!query.data) return <StatusMark label="Loading engines…" />;
  return <div className="settings-model-row"><label className="settings-row">
    <span><strong>{project ? "Coordinator" : "New tasks · All projects"}</strong>
      {saved?.model || saved?.effort ? <small>Changing engine clears this preference's model and effort override.</small> : null}</span>
    <select className="settings-select" aria-label={project ? "Coordinator engine" : "New tasks engine"} value={shown ?? ""} disabled={save.isPending}
      onChange={(event) => void submit(event.target.value ? { engine: event.target.value } : null, saved ?? null).catch(() => {})}>
      <option value="">Auto</option>{engines.map((engine) => <option key={engine.engine ?? engine.value} value={engine.engine ?? engine.value}>Prefer {engine.label}</option>)}
    </select></label>
    {unavailable && saved?.engine ? <p role="status" className="text-meta">Unavailable · Auto meanwhile: {unavailable}</p> : null}
    <SaveResult save={save} reload={() => { save.reset(); void query.refetch(); }} retry={() => save.variables && void submit(save.variables.value as Choice, save.variables.expected as Choice).catch(() => {})} />
  </div>;
}

/** A shortcut shares global settings rather than creating a second model editor. */
export function L3ModelButton({ project }: { project: string }) {
  const location = useLocation();
  const defaults = useProjectDefaults(project);
  const data = defaults.data;
  const text = data ? closedLabel(data.l3_choice, data.l3_unavailable, data, data.engines) : "…";
  return <Link className="models-pill" to="/settings#models" state={{ settingsFrom: location.pathname + location.search }} aria-label="Model settings">L3 · <strong>{text}</strong></Link>;
}

export function NewTasksButton({ overview, wide }: { overview: Overview | undefined; project?: string; wide?: boolean }) {
  const location = useLocation();
  const tasks = overview?.new_tasks;
  const text = tasks ? closedLabel(tasks.value, tasks.unavailable, tasks, overview?.engines) : "…";
  return <Link aria-label="Model settings" to="/settings#models" state={{ settingsFrom: location.pathname + location.search }} className="new-tasks" data-wide={wide || undefined} data-active={Boolean(tasks?.value) || undefined}>
    <span>New tasks · {text}{wide && tasks?.value ? " · every project" : ""}</span><span aria-hidden>›</span>
  </Link>;
}
