import { useState } from "react";
import type { KeyboardEvent } from "react";
import { Link } from "react-router";
import { ApiError, useOverview, useProject, useProjectDefaults, useSaveNewTasks, useSetDefault } from "../data/api";
import type { Choice, ChoiceOptions, Overview } from "../data/api";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";

export type ModelsTab = "l3" | "tasks";

type Engines = { engine?: string; value?: string; label: string }[];

/** "Fable · High", "Codex default", "Auto · High", "gpt-5.5 · Codex": a choice in the words its controls use. */
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

/** The model a choice names, without its effort: "Fable", "Codex default". */
function modelName(choice: Choice, options: ChoiceOptions | undefined, engines: Engines) {
  return choiceLabel(choice && { engine: choice.engine, model: choice.model }, options, engines);
}

/** The closed control's text: the choice, or why it cannot start now. */
export function closedLabel(choice: Choice, unavailable: string | null | undefined, options: ChoiceOptions | undefined, engines: Engines = []) {
  return unavailable && choice?.engine ? `${modelName(choice, options, engines)} unavailable · Auto meanwhile` : choiceLabel(choice, options, engines);
}

interface Draft { engine: string | null; model: string | null; effort: string | null; other: boolean }

function draftOf(choice: Choice, options: ChoiceOptions): Draft {
  const engine = choice?.engine ?? null, model = choice?.model ?? null;
  const listed = !engine || options.models.some((m) => m.engine === engine && (m.model ?? null) === model);
  return { engine, model, effort: choice?.effort ?? null, other: !listed };
}

/** The saved value a draft becomes; null is Auto. Only set keys are sent, so a saved value reads back the same. */
function valueOf(draft: Draft): Choice {
  if (!draft.engine && !draft.effort) return null;
  return {
    ...(draft.engine ? { engine: draft.engine } : {}),
    ...(draft.engine && draft.model ? { model: draft.model.trim() } : {}),
    ...(draft.effort ? { effort: draft.effort } : {}),
  };
}

const normal = (choice: Choice) => JSON.stringify(valueOf({ engine: choice?.engine ?? null, model: choice?.model ?? null, effort: choice?.effort ?? null, other: false }));

/** Auto requests its effort on whichever engine routing picks, so it offers every engine's levels once. */
function effortsFor(engine: string | null, options: ChoiceOptions) {
  if (engine) return options.efforts[engine] ?? [];
  const all = Object.values(options.efforts).flat();
  return all.filter((e, i) => all.findIndex((x) => x.value === e.value) === i);
}

interface TabState {
  saved: Choice;
  options: ChoiceOptions | undefined;
  engines: Engines;
  unavailable: string | null | undefined;
  loading: boolean;
  failed: boolean;
  reload: () => void;
}

/**
 * The Models dialog (SPEC.md §3.6): the L3 tab is this project's L3 choice; the Tasks tab is New tasks for
 * every project. One draft at a time belongs to the open tab; switching tabs or closing drops it. Use saves
 * only that tab and closes the dialog. Without a project only the Tasks tab exists.
 */
export function ModelsDialog({ project, tab: initial, onClose }: { project?: string; tab: ModelsTab; onClose: () => void }) {
  const { phone } = useViewport();
  const [tab, setTab] = useState<ModelsTab>(project ? initial : "tasks");
  const [draft, setDraft] = useState<Draft | null>(null);
  const overview = useOverview();
  const defaults = useProjectDefaults(project ?? "");
  const projectView = useProject(project ?? "", Boolean(project));
  const saveTasks = useSaveNewTasks();
  const saveL3 = useSetDefault(project ?? "");
  const save = tab === "l3" ? saveL3 : saveTasks;
  const saving = save.isPending;
  const engines = overview.data?.engines ?? [];
  const tasks = overview.data?.new_tasks;

  const state: TabState = tab === "l3" ? {
    saved: defaults.data?.l3_choice ?? null, options: defaults.data, engines: defaults.data?.engines ?? engines,
    unavailable: defaults.data?.l3_unavailable, loading: defaults.isPending, failed: defaults.isError,
    reload: () => void defaults.refetch(),
  } : {
    saved: tasks?.value ?? null, options: tasks ?? undefined, engines, unavailable: tasks?.unavailable,
    loading: overview.isPending, failed: overview.isError || (Boolean(overview.data) && !tasks),
    reload: () => void overview.refetch(),
  };

  const switchTo = (next: ModelsTab) => {
    if (saving || next === tab) return;
    setTab(next);
    setDraft(null);
    saveTasks.reset();
    saveL3.reset();
  };
  const onTabKey = (event: KeyboardEvent) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "l3" : event.key === "End" ? "tasks" : tab === "l3" ? "tasks" : "l3";
    switchTo(next);
    document.getElementById(`models-tab-${next}`)?.focus();
  };

  const commit = (value: Choice) => {
    const done = { onSuccess: onClose };
    if (tab === "l3") saveL3.mutate({ setting: "l3_choice", value, expected: state.saved }, done);
    else saveTasks.mutate({ value, expected: state.saved }, done);
  };
  const reload = () => { save.reset(); setDraft(null); state.reload(); };

  const label = tab === "l3" ? `Models · L3 · ${project} only` : "Models · Tasks · All projects";
  return <Overlay label={label} side={phone ? "bottom" : "center"} onClose={onClose}>
    <div className="models">
      <header className="models-head">
        <h2>Models</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>×</button>
      </header>
      {project ? <div className="models-tabs" role="tablist" aria-label="Who the choice is for" onKeyDown={onTabKey}>
        {(["l3", "tasks"] as const).map((key) => <button key={key} id={`models-tab-${key}`} type="button" role="tab"
          aria-selected={tab === key} aria-controls="models-panel" tabIndex={tab === key ? 0 : -1} disabled={saving && tab !== key}
          onClick={() => switchTo(key)}>
          <strong>{key === "l3" ? "L3" : "Tasks"}</strong><small>{key === "l3" ? `${project} only` : "All projects"}</small>
        </button>)}
      </div> : null}
      <div id="models-panel" role={project ? "tabpanel" : undefined} aria-labelledby={project ? `models-tab-${tab}` : undefined} className="models-panel">
        {state.failed ? <p role="alert" className="text-danger">Could not load models. <button type="button" className="link" onClick={state.reload}>Retry</button></p>
          : state.loading || !state.options ? <p role="status" className="text-muted">Loading models…</p>
            : <Choices key={tab} tab={tab} project={project} state={state as TabState & { options: ChoiceOptions }} draft={draft}
              setDraft={(next) => { save.reset(); setDraft(next); }} saving={saving}
              l3Only={defaults.data?.l3_engine ?? null} only={tasks?.only ?? []}
              lastReply={projectView.data?.l3 ?? undefined}
              error={save.error} onUse={commit} onReload={reload} onRetry={() => save.variables && commit(save.variables.value as Choice)} />}
      </div>
    </div>
  </Overlay>;
}

function Choices({ tab, project, state, draft, setDraft, saving, l3Only, only, lastReply, error, onUse, onReload, onRetry }: {
  tab: ModelsTab; project?: string; state: TabState & { options: ChoiceOptions }; draft: Draft | null;
  setDraft: (draft: Draft | null) => void; saving: boolean; l3Only: string | null; only: { project: string; engine: string }[];
  lastReply: Record<string, unknown> | undefined; error: Error | null;
  onUse: (value: Choice) => void; onReload: () => void; onRetry: () => void;
}) {
  const { options, engines, saved } = state;
  const shown = draft ?? draftOf(saved, options);
  const value = valueOf(shown);
  const engineLabel = (engine: string) => engines.find((e) => (e.engine ?? e.value) === engine)?.label ?? engine;
  const efforts = effortsFor(shown.engine, options);
  const typed = shown.other ? (shown.model ?? "").trim() : "";
  const invalid = shown.other && (!shown.engine || !typed || /\s/.test(typed));
  const dirty = normal(value) !== normal(saved);
  const changed = error instanceof ApiError && error.status === 409;
  const name = modelName(value, options, engines);
  const pick = (next: Partial<Draft>) => {
    const merged = { ...shown, ...next };
    const levels = effortsFor(merged.engine, options);
    setDraft({ ...merged, effort: levels.some((e) => e.value === merged.effort) ? merged.effort : null });
  };
  const optionId = (engine: string | null, model: string | null) => `${engine ?? "auto"}:${model ?? ""}`;
  const current = shown.other ? "other" : optionId(shown.engine, shown.model);
  const otherEngines = [...new Set(options.models.map((m) => m.engine))];
  const keptOnly = tab === "tasks" && shown.engine ? only.filter((row) => row.engine !== shown.engine) : [];
  const l3Kept = tab === "l3" && shown.engine && l3Only && l3Only !== shown.engine;
  const text = (key: string) => typeof lastReply?.[key] === "string" ? lastReply[key] as string : "";
  const lastModel = text("engine_model"), launched = text("launch_effort"), reported = text("engine_reasoning_effort");

  return <>
    <p className="models-in-use"><strong>In use: {choiceLabel(saved, options, engines)}</strong>{" · "}
      {tab === "l3" ? `who answers you in ${project}'s chat.` : "every project; tasks that start from now, including queued ones. Started tasks keep theirs."}</p>
    {state.unavailable && saved?.engine ? <p className="models-note" role="status">
      {modelName(saved, options, engines)} unavailable · Auto meanwhile: {state.unavailable}</p> : null}
    <fieldset className="models-list" disabled={saving} aria-label="Model">
      {[{ engine: null, model: null, label: "Auto" }, ...options.models].map((m) => {
        const id = optionId(m.engine, m.model);
        return <label key={id} className="models-option" data-checked={current === id || undefined}>
          <input type="radio" name={`models-${tab}`} checked={current === id} data-autofocus={current === id || undefined}
            onChange={() => pick({ engine: m.engine, model: m.model, other: false })} />
          <span>{m.label}{!m.engine ? <small> · {tab === "l3" ? "project routing and defaults" : "each project's defaults"}</small> : null}</span>
          {m.engine ? <small className="models-engine">{engineLabel(m.engine)}</small> : null}
        </label>;
      })}
      <label className="models-option" data-checked={shown.other || undefined}>
        <input type="radio" name={`models-${tab}`} checked={shown.other} data-autofocus={shown.other || undefined}
          onChange={() => pick({ engine: shown.engine ?? otherEngines[0] ?? null, model: "", other: true })} />
        <span>Other model…</span>
      </label>
      {shown.other ? <div className="models-other">
        <label>Engine
          <select value={shown.engine ?? ""} onChange={(event) => pick({ engine: event.target.value })}>
            {otherEngines.map((engine) => <option key={engine} value={engine}>{engineLabel(engine)}</option>)}
          </select>
        </label>
        <label>Model id
          <input value={shown.model ?? ""} spellCheck={false} autoCapitalize="off" autoComplete="off" placeholder="Exact model id"
            onChange={(event) => pick({ model: event.target.value })} />
        </label>
      </div> : null}
    </fieldset>
    {efforts.length ? <fieldset className="models-effort" disabled={saving}>
      <legend>Effort</legend>
      <div className="segmented">
        {[{ value: null, label: "Default" }, ...efforts].map((e) => <label key={e.value ?? "default"} data-checked={shown.effort === e.value || undefined}>
          <input type="radio" name={`effort-${tab}`} checked={shown.effort === e.value} onChange={() => pick({ effort: e.value })} />
          {e.label}
        </label>)}
      </div>
    </fieldset> : null}
    {tab === "l3" ? <p className="text-meta text-muted">
      Applies from L3's next reply.{text("engine_last") ? ` Last reply: ${[lastModel || "model not reported", launched ? effortLabel(launched, options) : "default effort",
        reported ? `reported ${effortLabel(reported, options)}` : "effort not reported"].join(" · ")}.` : ""}
    </p> : value?.engine ? <p className="text-meta text-muted">When {name} is unavailable, each project's Auto picks instead.</p> : null}
    {l3Kept ? <p className="models-kept">This project keeps L3 only on {engineLabel(l3Only)}, so {name} can't be used here.{" "}
      <Link to={`/settings/projects/${project}#routing`}>Change in Routing</Link></p> : null}
    {keptOnly.map((row) => <p key={row.project} className="models-kept">
      <strong>{row.project}</strong> runs tasks only on {engineLabel(row.engine)}, so it keeps its {engineLabel(row.engine)} model.{" "}
      <Link to={`/settings/projects/${row.project}#routing`}>Change</Link></p>)}
    {changed ? <p role="alert" className="text-danger">Changed in another window. <button type="button" className="link" onClick={onReload}>Reload</button></p>
      : error ? <p role="alert" className="text-danger">{error.message} <button type="button" className="link" onClick={onRetry}>Retry</button></p> : null}
    <div className="models-actions">
      {saved ? <button type="button" className="btn" disabled={saving} onClick={() => onUse(null)}>Back to Auto</button> : null}
      <button type="button" className="btn btn-primary" disabled={saving || !dirty || invalid} onClick={() => onUse(value)}>
        {saving ? "Saving…" : tab === "l3" ? `Use for L3 in ${project}` : "Use for all new tasks"}
      </button>
    </div>
    {tab === "tasks" ? <p className="models-hint">For one task, tell L3: “use Opus at Max for this”.</p> : null}
  </>;
}

/** The message box's L3 button: who answers what you type here. */
export function L3ModelButton({ project }: { project: string }) {
  const [open, setOpen] = useState(false);
  const defaults = useProjectDefaults(project);
  const data = defaults.data;
  const text = data ? closedLabel(data.l3_choice, data.l3_unavailable, data, data.engines) : "…";
  return <>
    <button type="button" className="models-pill" aria-haspopup="dialog" disabled={!data} onClick={() => setOpen(true)}
      aria-label={`L3 model: ${text}`}>L3 · <strong>{text}</strong></button>
    {open ? <ModelsDialog project={project} tab="l3" onClose={() => setOpen(false)} /> : null}
  </>;
}

/** New tasks beside the quota: the rail under the engine meters, the top of Monitor and Work. It reads the page's overview. */
export function NewTasksButton({ overview, project, wide }: { overview: Overview | undefined; project?: string; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const tasks = overview?.new_tasks;
  const text = tasks ? closedLabel(tasks.value, tasks.unavailable, tasks, overview?.engines) : "…";
  return <>
    <button type="button" className="new-tasks" data-wide={wide || undefined} data-active={Boolean(tasks?.value) || undefined}
      aria-haspopup="dialog" disabled={!tasks} onClick={() => setOpen(true)}>
      <span>New tasks · {text}{wide && tasks?.value ? " · every project" : ""}</span><span aria-hidden>›</span>
    </button>
    {open ? <ModelsDialog project={project} tab="tasks" onClose={() => setOpen(false)} /> : null}
  </>;
}
