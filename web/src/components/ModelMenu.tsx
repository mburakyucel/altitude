import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { ApiError, useOverview, useProjectDefaults, useSaveNewTasks, useSetDefault } from "../data/api";
import type { Choice, ChoiceOptions } from "../data/api";
import { effortLabel, modelName } from "./Models";
import type { Transfer } from "./TaskTransfer";
import { BusyLabel, StatusMark } from "./StatusMark";
import "./model-menu.css";

type Engines = { engine?: string; value?: string; label: string }[];
const engineId = (row: Engines[number]) => row.engine ?? row.value ?? "";

/** The panel's place against its button inside the visible screen: the screen's width on a phone, scrolling inside when short. */
function placeNear(rect: DOMRect, placement: "up" | "down"): CSSProperties {
  const view = window.visualViewport;
  const screen = view?.width ?? window.innerWidth;
  const width = screen < 640 ? screen - 24 : Math.min(440, screen - 24);
  const height = view?.height ?? window.innerHeight;
  const left = Math.max(12, Math.min(rect.left, screen - width - 12));
  const room = placement === "up" ? rect.top - 18 : height - rect.bottom - 18;
  return { left, width, maxHeight: Math.max(160, Math.min(room, Math.max(height * 0.6, 560))),
    ...(placement === "up" ? { top: "auto", bottom: window.innerHeight - rect.top + 6 } : { bottom: "auto", top: rect.bottom + 6 }) };
}

/**
 * One button and its panel (SPEC.md §3.6.1). Opening only shows choices: nothing is saved or requested until a
 * row is pressed. Escape, a press outside or the button closes it and returns focus to the button.
 */
function useMenu(placement: "up" | "down") {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<CSSProperties>({});
  const anchor = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback((refocus = false) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  }, []);
  const place = useCallback(() => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) setStyle(placeNear(rect, placement));
  }, [placement]);
  useLayoutEffect(() => { if (open) place(); }, [open, place]);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => { if (!anchor.current?.contains(event.target as Node)) close(); };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close(anchor.current?.contains(document.activeElement) ?? false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", place);
    window.visualViewport?.addEventListener("resize", place);
    return () => {
      document.removeEventListener("pointerdown", onPointer); document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", place); window.visualViewport?.removeEventListener("resize", place);
    };
  }, [open, close, place]);
  // Opening enters the checked model, so the current choice is where the eye and focus land.
  useEffect(() => {
    if (!open) return;
    const menu = anchor.current?.querySelector<HTMLElement>('[role="menu"]');
    (menu?.querySelector<HTMLElement>('[aria-checked="true"]') ?? menu?.querySelector<HTMLElement>('[role^="menuitem"]'))?.focus({ preventScroll: true });
    menu?.querySelectorAll<HTMLElement>('[aria-checked="true"]').forEach((row) => row.scrollIntoView({ block: "nearest" }));
  }, [open]);
  const toggle = () => open ? close() : setOpen(true);
  return { open, setOpen, close, toggle, anchor, button, style };
}
type Menu = ReturnType<typeof useMenu>;

/** Up and down move within a column; left and right move to the other column's checked row. */
function onMenuKey(event: ReactKeyboardEvent<HTMLDivElement>) {
  const active = document.activeElement as HTMLElement | null;
  const columns = [...event.currentTarget.querySelectorAll<HTMLElement>("[data-column]")];
  const at = columns.findIndex((column) => column.contains(active));
  if (at < 0) return;
  const rows = (column: HTMLElement) => [...column.querySelectorAll<HTMLElement>('[role^="menuitem"]')];
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    const other = columns[(at + (event.key === "ArrowRight" ? 1 : columns.length - 1)) % columns.length];
    if (!other || other === columns[at]) return;
    event.preventDefault();
    (other.querySelector<HTMLElement>('[aria-checked="true"]') ?? rows(other)[0])?.focus();
    return;
  }
  const items = rows(columns[at]!);
  const index = items.indexOf(active!);
  const next = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: items.length - 1 }[event.key];
  if (next === undefined || !items.length) return;
  event.preventDefault();
  items[(next + items.length) % items.length]!.focus();
}

/** One button showing model and effort together; its panel lists models beside effort levels. */
function Picker({ menu, label, menuLabel, title, className, disabled, text, heads = true, children, top, bottom }: {
  menu: Menu; label: string; menuLabel: string; title: string; className?: string; disabled?: boolean; text: ReactNode;
  heads?: boolean; children: ReactNode; top?: ReactNode; bottom?: ReactNode;
}) {
  // A confirmation appearing at the foot shortens the columns; the picked rows stay in view.
  const footed = Boolean(bottom);
  useLayoutEffect(() => {
    if (menu.open) menu.anchor.current?.querySelectorAll<HTMLElement>('[role="menu"] [aria-checked="true"]').forEach((row) => row.scrollIntoView({ block: "nearest" }));
  }, [footed, menu.open, menu.anchor]);
  return <div className="model-menu-anchor" ref={menu.anchor}>
    <button ref={menu.button} type="button" className={`models-pill${className ? ` ${className}` : ""}`} aria-haspopup="menu" aria-expanded={menu.open}
      aria-label={label} disabled={disabled} onClick={menu.toggle}>
      {text}<span aria-hidden className="model-menu-chevron">⌄</span>
    </button>
    {menu.open ? <div role="menu" aria-label={menuLabel} className="menu model-menu" style={menu.style} onKeyDown={onMenuKey}>
      <p className="model-menu-title">{title}</p>
      {top}
      {heads ? <div className="model-menu-column-titles" aria-hidden><span>Model</span><span>Effort</span></div> : null}
      <div className="model-menu-columns">{children}</div>
      {bottom}
    </div> : null}
  </div>;
}

interface Row { key: string; engine: string | null; model: string | null; value?: string; label: string; meta?: string; reason?: string | null }
interface Group { engine: string | null; label: string; rows: Row[] }
const rowKey = (engine: string | null | undefined, model: string | null | undefined) => engine ? `${engine}:${model ?? ""}` : "auto";

/** One column of the panel: its rows, under engine headings where they apply. */
/** In a task, `current` names what runs: a checked row that differs from it is a pick still to confirm, marked apart. */
function Rows({ name, groups, checked, pending, current, onPick }: { name: string; groups: Group[]; checked: string; pending: string; current?: string; onPick: (row: Row) => void }) {
  const [why, setWhy] = useState("");
  const proposed = current !== undefined && checked !== current;
  return <div role="group" aria-label={name} data-column className="model-menu-column">
    {groups.map((group) => <div key={group.engine ?? "auto"} role="group" aria-label={group.engine ? group.label : undefined}>
      {group.engine ? <p className="model-menu-heading" aria-hidden>{group.label}</p> : null}
      {group.rows.map((row) => {
        const on = row.key === checked;
        const draft = on && proposed;
        return <div key={row.key}>
          <button type="button" role="menuitemradio" aria-checked={on} className="menu-item model-menu-row" data-checked={on && !draft || undefined} data-pending={draft || undefined}
            aria-disabled={row.reason ? true : undefined}
            onClick={() => { if (row.reason) { setWhy(why === row.key ? "" : row.key); return; } setWhy(""); onPick(row); }}>
            <span className="model-menu-label"><span>{row.label}</span>{row.meta ? <span className="menu-item-meta">{row.meta}</span> : null}</span>
            <span className="model-menu-mark" aria-hidden>{row.key === pending ? <span className="spinner" /> : draft ? <Ring /> : on ? <Check /> : null}</span>
          </button>
          {why === row.key && row.reason ? <p className="model-menu-why" role="status">{row.reason}</p> : null}
        </div>;
      })}
    </div>)}
  </div>;
}

const effortRows = (efforts: { value: string | null; label: string; meta?: string }[]): Group[] =>
  [{ engine: null, label: "", rows: efforts.map((e) => ({ key: e.value ?? "default", engine: null, model: null, value: e.value ?? undefined, label: e.label, meta: e.meta })) }];

/** The closed button's text: model, then effort; on a phone the role prefix and the word "effort" give way first. */
function PairText({ role, model, effort, unpinned }: { role?: string; model: ReactNode; effort?: string; unpinned?: boolean }) {
  return <>
    {role ? <span className="model-pair-hint">{role} · </span> : null}
    <strong className="model-pair-model">{model}</strong>
    {effort ? <span className="model-pair-effort"> · {effort}{unpinned ? <span className="model-pair-hint"> effort</span> : null}</span> : null}
  </>;
}

/** A pick waiting for its confirmation: an open ring where a saved or running choice has a check. */
function Ring() {
  return <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10" cy="10" r="5.5" /></svg>;
}

function Check() {
  return <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 10.5 8 14l7.5-8" /></svg>;
}

// ---- Coordinator and New tasks: a pick saves at once ------------------------------------------

type Role = "l3" | "tasks";

/** One role's saved choice, its catalog and its save, read from the views that already carry them. */
function useRoleChoice(role: Role, project: string | undefined) {
  // The shell already polls the overview; mounting a button reads that copy rather than fetching it again.
  const overview = useOverview(false);
  const defaults = useProjectDefaults(role === "l3" ? project ?? "" : "");
  const saveL3 = useSetDefault(project ?? "");
  const saveTasks = useSaveNewTasks();
  const tasks = overview.data?.new_tasks;
  const state = role === "l3"
    ? { saved: defaults.data?.l3_choice ?? null, options: defaults.data as ChoiceOptions | undefined, engines: (defaults.data?.engines ?? []) as Engines,
        unavailable: defaults.data?.l3_unavailable, failed: defaults.isError, reload: () => void defaults.refetch() }
    : { saved: tasks?.value ?? null, options: tasks ?? undefined, engines: (overview.data?.engines ?? []) as Engines,
        unavailable: tasks?.unavailable, failed: overview.isError || (Boolean(overview.data) && !tasks), reload: () => void overview.refetch() };
  const save = role === "l3" ? saveL3 : saveTasks;
  // The pick this role's latest save carried: its row turns while it saves, and a failure names it.
  const picked = (role === "l3" ? saveL3.variables?.value : saveTasks.variables?.new_tasks) as Choice | undefined;
  const submit = (value: Choice) => {
    if (save.isPending) return;
    if (role === "l3") saveL3.mutate({ setting: "l3_choice", value, expected: state.saved });
    else saveTasks.mutate({ new_tasks: value, expected: state.saved });
  };
  const projectEfforts = role === "l3" ? defaults.data?.roles.find((r) => r.role === "l3")?.engines : undefined;
  return { ...state, save, picked, submit, projectEfforts, only: tasks?.only ?? [] };
}

/** What a role's button and panel show and how a pick saves; effort lists only what the chosen engine runs. */
function useRoleControls(role: Role, project: string | undefined) {
  const choice = useRoleChoice(role, project);
  // The saved choice already shows a pick on its way (SPEC.md §1.1); the picked row keeps its spinner until it lands.
  const { saved, options, engines, save, submit } = choice;
  const pendingValue = save.isPending ? choice.picked : undefined;
  const label = (engine: string) => engines.find((e) => engineId(e) === engine)?.label ?? engine;
  const order = engines.length ? engines.map(engineId) : [...new Set((options?.models ?? []).map((m) => m.engine))];
  // Each model appears once, under its own name; a saved engine-only or custom choice keeps its own row.
  const groups: Group[] = [{ engine: null, label: "", rows: [{ key: "auto", engine: null, model: null, label: "Auto", meta: "Picks by quota" }] },
    ...order.map((engine) => {
      const rows: Row[] = (options?.models ?? []).filter((m) => m.engine === engine && m.model).map((m) => ({ key: rowKey(engine, m.model), engine, model: m.model, label: m.label }));
      if (saved?.engine === engine && !rows.some((r) => r.key === rowKey(engine, saved.model))) rows.push({ key: rowKey(engine, saved.model), engine, model: saved.model ?? null, label: modelName(saved, options, engines) });
      return { engine, label: label(engine), rows };
    })];
  // Auto lists the levels every engine runs, so no pick is impossible.
  const levels = (engine: string | null | undefined) => {
    const efforts = options?.efforts ?? {};
    if (engine) return efforts[engine] ?? [];
    const lists = Object.values(efforts);
    return (lists[0] ?? []).filter((e) => lists.every((list) => list.some((x) => x.value === e.value)));
  };
  const pickModel = (row: Row) => {
    if (row.key === rowKey(saved?.engine, saved?.model)) return;
    const effort = saved?.effort && levels(row.engine).some((e) => e.value === saved.effort) ? saved.effort : null;
    submit(row.engine ? { engine: row.engine, ...(row.model ? { model: row.model } : {}), ...(effort ? { effort } : {}) } : effort ? { effort } : null);
  };
  const pickEffort = (effort: string | null) => {
    if (effort === (saved?.effort ?? null)) return;
    const base = { ...(saved?.engine ? { engine: saved.engine } : {}), ...(saved?.model ? { model: saved.model } : {}) };
    submit(base.engine || effort ? { ...base, ...(effort ? { effort } : {}) } : null);
  };
  const modelText = !options ? "…" : choice.unavailable && saved?.engine ? `${modelName(saved, options, engines)} unavailable · Auto meanwhile` : modelName(saved, options, engines);
  const effortText = !options ? "…" : saved?.effort ? effortLabel(saved.effort, options) : "Default";
  const choiceText = (value: Choice) => `${modelName(value, options, engines)} · ${value?.effort ? effortLabel(value.effort, options) : "Default effort"}`;
  // Default means "not pinned": its row says what that runs, so a project setting is never hidden behind it.
  const projectEfforts = (choice.projectEfforts ?? []).filter((f) => !saved?.engine || f.engine === saved.engine);
  const set = projectEfforts.find((f) => f.effort.value);
  const defaultMeta = role === "tasks" ? "Project setting or built-in"
    : set ? `Project · ${saved?.engine ? effortLabel(set.effort.value!, options) : "setting"}`
    : saved?.engine ? `${label(saved.engine)}’s own` : undefined;
  return { ...choice, shown: saved, pendingValue, groups, levels, pickModel, pickEffort, modelText, effortText, choiceText, defaultMeta, label };
}
type RoleControls = ReturnType<typeof useRoleControls>;

/** The lines in a role's panel: why it cannot run now, and a failed save with its way out. */
function RoleNotes({ c, role }: { c: RoleControls; role: Role }) {
  const { save, saved } = c;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const denied = save.error instanceof ApiError && [401, 403].includes(save.error.status);
  const kept = role === "tasks" && c.shown?.engine ? c.only.filter((row) => row.engine !== c.shown!.engine) : [];
  return <>
    {c.unavailable && saved?.engine ? <p className="model-menu-note" data-tone="warn" role="status">Unavailable · Auto meanwhile: {c.unavailable}</p> : null}
    {kept.map((row) => <p key={row.project} className="model-menu-note">{row.project} runs tasks only on {c.label(row.engine)}.</p>)}
    {save.error ? <p role="alert" className="model-menu-note" data-tone="danger">{conflict ? "Changed in another window." : `Could not save ${c.choiceText(c.picked ?? null)}. ${save.error.message}`}{" "}
      {denied ? null : <button type="button" className="link" onClick={() => {
        if (conflict) { save.reset(); c.reload(); } else if (c.picked !== undefined) c.submit(c.picked);
      }}>{conflict ? "Reload" : "Retry"}</button>}</p> : null}
  </>;
}

/**
 * One place per setting, one button each showing model and effort: under the project chat's message box it picks
 * that project's coordinator; the New tasks button beside quota picks what every project's new tasks start on.
 * The panel lists models beside effort levels; a pick saves at once and the panel stays open for the other.
 */
export function RoleModelButton({ project, variant = "pill" }: { project?: string; variant?: "pill" | "row" }) {
  const role: Role = variant === "pill" && project ? "l3" : "tasks";
  const c = useRoleControls(role, project);
  const row = variant === "row";
  const menu = useMenu(row ? "down" : "up");
  const ready = Boolean(c.options) || c.failed;
  const lost = c.failed && !c.options;
  const pending = (key: string) => c.pendingValue !== undefined ? key : "";
  // Notes sit at the panel's far edge from its button, so a note appearing never moves the row under the pointer.
  const notes = c.options ? <RoleNotes c={c} role={role} /> : null;
  const value = lost ? "could not load" : `${c.modelText} · ${c.effortText}${c.shown?.effort ? "" : " effort"}`;
  const button = <Picker menu={menu} disabled={!ready} heads={Boolean(c.options)}
    label={role === "l3" ? `Coordinator model and effort: ${value}` : `Model and effort for new tasks: ${value}`}
    menuLabel={role === "l3" ? `Coordinator model and effort for ${project}` : "Model and effort for new tasks"}
    title={role === "l3" ? `Coordinator (L3) · ${project}` : "New tasks (L2) · all projects"}
    text={lost ? <PairText role={row ? undefined : "L3"} model={<span className="model-pair-failed">Could not load</span>} />
      : <PairText role={row ? undefined : "L3"} model={c.modelText} effort={c.effortText} unpinned={!c.shown?.effort} />}
    top={row ? null : notes} bottom={row ? notes : null}>
    {lost ? <p role="alert" className="model-menu-note" data-tone="danger">Could not load models. <button type="button" className="link" onClick={c.reload}>Retry</button></p>
      : !c.options ? <p className="model-menu-note"><StatusMark label="Loading models…" /></p>
      : <>
        <Rows name="Model" groups={c.groups} checked={rowKey(c.shown?.engine, c.shown?.model)}
          pending={pending(rowKey(c.pendingValue?.engine, c.pendingValue?.model))} onPick={c.pickModel} />
        <Rows name="Effort" groups={effortRows([{ value: null, label: "Default", meta: c.defaultMeta }, ...c.levels(c.shown?.engine)])} checked={c.shown?.effort ?? "default"}
          pending={pending(c.pendingValue?.effort ?? "default")} onPick={(r) => c.pickEffort(r.value ?? null)} />
      </>}
  </Picker>;
  // Beside quota the button sits under its own name.
  return row ? <div className="model-pair-labelled"><span className="model-pair-label">New tasks</span>{button}</div> : button;
}

// ---- One task: picks are a draft until the switch is confirmed ---------------------------------

type Draft = { engine: string; value: string; model: string; effort: string };

/**
 * The task composer's model and effort button. Its panel's two columns change one pending choice; browsing
 * changes nothing. A pending choice shows its confirmation, with the switch's cost, at the foot of the same panel.
 * A switch under way lives in its conversation card, and the button leads there.
 */
export function TaskModelButton({ transfer: t }: { transfer: Transfer }) {
  const info = t.info;
  const [draft, setDraft] = useState<Draft | null>(null);
  const menu = useMenu("up");
  const { setOpen, close, open } = menu;
  useEffect(() => { if (t.menuRequest) { setOpen(true); t.menuHandled(); } }, [t, setOpen]);
  // Any switch that appears, even one already finished, replaces the panel and the pending choice with its card.
  const requestId = t.request?.id;
  const seen = useRef(requestId);
  useEffect(() => { if (requestId !== seen.current) { seen.current = requestId; close(); setDraft(null); } }, [requestId, close]);
  // An unconfirmed switch is checked by reading, never sent again, so its pending choice goes with it.
  useEffect(() => { if (t.uncertain) { close(); setDraft(null); } }, [t.uncertain, close]);
  // Closing the panel drops a pending choice that was never confirmed. A switch is checked against the session the
  // panel showed when it opened, so a resume or restart meanwhile refuses it rather than redirecting it.
  const shown = useRef<unknown>(null);
  useEffect(() => { if (open) shown.current = t.info?.observed; else setDraft(null); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!info || (!info.targets.length && !t.label) || t.disabled) return null;
  if (t.active && !t.stopping) return <button type="button" className="models-pill task-model-pill"
    aria-label={`Task model: switching to ${t.switchingTo}. Show the switch`} onClick={t.focusCard}>
    <span className="model-pair-hint">L2 · </span><StatusMark label={`Switching to ${t.switchingTo}`} announce={false} /> <strong>Switching…</strong>
  </button>;
  const current = { engine: info.current_engine ?? "", model: info.current_model ?? "", effort: info.current_effort ?? "native" };
  // A task runs one concrete model, so the engine's default appears once, under its own name.
  const groups: Group[] = info.targets.map((item) => ({ engine: item.engine, label: item.label, rows: item.models
    .filter((m) => m.value !== "default" || !item.models.some((other) => other.value !== "default" && other.model === m.model))
    .map((m) => ({ key: rowKey(item.engine, m.value), engine: item.engine, model: m.model, value: m.value, label: m.label.replace(/^Default · /, ""),
      meta: item.engine === current.engine && m.model === current.model ? "Current" : undefined,
      reason: m.available ? null : m.reason || item.reason || "Not available now." })) }));
  const rows = groups.flatMap((g) => g.rows);
  const currentValue = rows.find((r) => r.engine === current.engine && r.model === current.model)?.value ?? "";
  const currentKey = rowKey(current.engine, currentValue);
  const chosen = draft ?? { ...current, value: currentValue };
  const changed = chosen.engine !== current.engine || chosen.model !== current.model || chosen.effort !== current.effort;
  const target = info.targets.find((item) => item.engine === chosen.engine);
  const modelText = rows.find((r) => r.key === rowKey(chosen.engine, chosen.value))?.label ?? (draft ? chosen.model : info.current_model_label || info.current_label);
  const effortText = target?.efforts.find((e) => e.value === chosen.effort)?.label ?? (draft ? chosen.effort : info.current_effort_label);
  const currentText = { model: rows.find((r) => r.engine === current.engine && r.model === current.model)?.label ?? info.current_model_label ?? info.current_label,
    effort: info.targets.find((item) => item.engine === current.engine)?.efforts.find((e) => e.value === current.effort)?.label ?? info.current_effort_label };
  const pickModel = (row: Row) => {
    const next = info.targets.find((item) => item.engine === row.engine);
    const keep = next?.efforts.some((e) => e.value === chosen.effort);
    setDraft({ engine: row.engine ?? "", value: row.value ?? "", model: row.model ?? "", effort: keep ? chosen.effort : next?.default_effort ?? "native" });
  };
  const pickEffort = (row: Row) => setDraft({ ...chosen, effort: row.value ?? "native" });
  const confirm = async () => { if (draft && await t.switchTo(draft, shown.current)) close(true); };
  // An unconfirmed switch is settled in its card; until then the button leads there rather than offering another.
  if (t.uncertain) return <button type="button" className="models-pill task-model-pill" data-muted
    aria-label={`Task model and effort: ${currentText.model} · ${currentText.effort}. Switch unconfirmed; show the switch`} onClick={t.focusCard}>
    <PairText role="L2" model={currentText.model} effort={currentText.effort} />
  </button>;
  const off = t.blocked || t.stopping || t.queued;
  const onlyEffort = chosen.engine === current.engine && chosen.model === current.model;
  return <Picker menu={menu} disabled={off} className="task-model-pill"
    label={`Task model and effort: ${currentText.model} · ${currentText.effort}`} menuLabel="This task’s model and effort" title="This task (L2)"
    text={<PairText role="L2" model={currentText.model} effort={currentText.effort} />}
    bottom={changed ? <div className="model-menu-confirm" role="group" aria-label="Confirm the switch">
      <p className="model-menu-confirm-title">{onlyEffort ? `Switch to ${effortText} effort?`
        : `Switch to ${modelText}${chosen.engine !== current.engine ? ` on ${target?.label ?? chosen.engine}` : ""} · ${effortText}?`}</p>
      <p className="model-menu-note">{info.preparation ? "The current session writes a handoff note, then a fresh session continues from the task’s saved work."
        : "A fresh session continues from the task’s saved work."} Some context may be lost, and catching up uses extra tokens. Work, questions and merge holds stay.</p>
      <div className="model-menu-actions">
        <button type="button" className="btn btn-primary" disabled={t.pending} onClick={() => void confirm()}><BusyLabel busy={t.pending} label="Switch" working="Switching…" /></button>
        <button type="button" className="btn" disabled={t.pending} onClick={() => close(true)}>Cancel</button>
      </div>
    </div> : null}>
    <Rows name="Model" groups={groups} checked={rowKey(chosen.engine, chosen.value)} current={currentKey} pending="" onPick={pickModel} />
    {effortText ? <Rows name="Effort" groups={effortRows((target?.efforts ?? []).map((e) => ({ value: e.value, label: e.label,
      meta: chosen.engine === current.engine && e.value === current.effort ? "Current" : undefined })))}
      checked={chosen.effort} current={chosen.engine === current.engine ? current.effort : ""} pending="" onPick={pickEffort} /> : null}
  </Picker>;
}
