import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, Ref } from "react";
import { useBlocker, useNavigate, useParams, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { pressId, sendProjectAgent, useProjectAgent, useProjectAgents } from "../data/api";
import type { AgentClock, ProjectAgentDefinition } from "../data/api";
import { DAY_NAMES, LIMITS, NEW_DEFINITION, NEW_SCHEDULE, TELL, refusal } from "../data/projectAgents";
import { AgentFrame } from "../components/ProjectAgentParts";
import { ChoiceButton } from "../components/ModelMenu";
import { BusyLabel, StatusMark } from "../components/StatusMark";
import { Overlay } from "../shell/Overlay";

/*
 * The agent form (design/wireframes/project-agents/PROPOSAL.md, "The form"), new at /agents/new and changing one at
 * /agents/<id>/edit; `?offer=<id>` fills it from L3's offer, which the save then names. A new agent is saved paused and
 * its page opens. Leaving with unsaved changes asks first; a refusal keeps the edit with its reason and Retry.
 */

type Schedule = ProjectAgentDefinition["schedules"][number];

function ScheduleRow({ index, schedule, clock, onChange, onRemove }: {
  index: number; schedule: Schedule; clock?: AgentClock; onChange: (schedule: Schedule) => void; onRemove: () => void;
}) {
  const toggle = (day: number) => onChange({ ...schedule, days: schedule.days.includes(day) ? schedule.days.filter((d) => d !== day) : [...schedule.days, day].sort() });
  const otherZone = clock && clock.offset !== -new Date().getTimezoneOffset();
  return <div className="agent-trigger" role="group" aria-label={`Schedule ${index + 1}`}>
    <div className="agent-trigger-head">
      <svg aria-hidden viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" />
      </svg>
      <span>On a schedule</span>
      <button type="button" className="icon-btn agent-trigger-remove" aria-label="Remove this trigger" onClick={onRemove}>{REMOVE}</button>
    </div>
    <div className="agent-days">
      {DAY_NAMES.map((day, i) => <button key={day} type="button" className="agent-day" aria-label={day} aria-pressed={schedule.days.includes(i)}
        data-first-day={i === 0 || undefined} onClick={() => toggle(i)}>{day[0]}</button>)}
      <span className="agent-time">
        <input type="time" className="field" aria-label="Time" value={schedule.time} required onChange={(event) => onChange({ ...schedule, time: event.target.value || schedule.time })} />
        {otherZone ? <small className="text-muted">{clock.zone}</small> : null}
      </span>
    </div>
    {schedule.days.length ? null : <p className="text-meta text-danger">Pick at least one day.</p>}
  </div>;
}

const REMOVE = <svg aria-hidden viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M7 7l10 10M17 7L7 17" /></svg>;
const EVENT = "When it’s sent an event";

/** The event trigger's row: where events come from, and Remove. */
function EventRow({ remove, onRemove }: { remove: Ref<HTMLButtonElement>; onRemove: () => void }) {
  return <div className="agent-trigger" role="group" aria-label={EVENT}>
    <div className="agent-trigger-head">
      <svg aria-hidden viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
        <path d="M13 3L5 13h6l-1 8 8-10h-6l1-8z" />
      </svg>
      <span className="agent-trigger-words">{EVENT}<small className="text-muted">You send it from this computer; its page shows how.</small></span>
      <button type="button" className="icon-btn agent-trigger-remove" aria-label="Remove this trigger" ref={remove} onClick={onRemove}>{REMOVE}</button>
    </div>
  </div>;
}

/**
 * + Add a trigger: a menu of the two triggers under the button, as the project menu opens, until the list has the
 * event trigger; then it adds a schedule at once. Escape or a press outside closes the menu and returns focus.
 */
function AddTrigger({ events, onSchedule, onEvent }: { events: boolean; onSchedule: () => void; onEvent: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { setOpen(false); opener.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointer = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) close(); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  const onMenuKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    items[(next + items.length) % items.length]!.focus();
  };
  return <div className="agent-trigger agent-trigger-add relative" ref={ref}>
    <button type="button" className="btn" ref={opener} aria-haspopup={events ? undefined : "menu"} aria-expanded={events ? undefined : open}
      onClick={() => events ? onSchedule() : setOpen(!open)}>+ Add a trigger</button>
    {open ? <div role="menu" className="menu agent-trigger-menu" aria-label="Add a trigger" onKeyDown={onMenuKey}>
      <button type="button" role="menuitem" className="menu-item" onClick={() => { close(); onSchedule(); }}>On a schedule</button>
      <button type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); onEvent(); }}>{EVENT}</button>
    </div> : null}
  </div>;
}

/** The discard question: Keep editing is first and focused. */
function DiscardDialog({ detail, onKeep, onDiscard }: { detail: string; onKeep: () => void; onDiscard: () => void }) {
  return <Overlay label="Discard your changes?" side="center" onClose={onKeep}>
    <div className="remove-dialog">
      <h2>Discard your changes?</h2>
      <p>{detail}</p>
      <div className="remove-actions">
        <button type="button" className="btn" data-autofocus onClick={onKeep}>Keep editing</button>
        <button type="button" className="btn btn-outline-danger" onClick={onDiscard}>Discard</button>
      </div>
    </div>
  </Overlay>;
}

function Form({ project, id, read, latest, start, offer, clock }: {
  project: string; id?: string; read: { version?: number; definition: ProjectAgentDefinition }; latest?: number; start: ProjectAgentDefinition;
  offer: string | null; clock?: AgentClock;
}) {
  // The version and definition the form opened on: a save names that version, so a change from another window is refused.
  const [{ version, definition: initial }] = useState(read);
  const [draft, setDraft] = useState(start);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [incomplete, setIncomplete] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  // Adding the event trigger moves focus to its row's Remove.
  const eventRemove = useRef<HTMLButtonElement>(null);
  const eventAdded = useRef(false);
  useEffect(() => {
    if (draft.events && eventAdded.current) eventRemove.current?.focus();
    eventAdded.current = false;
  }, [draft.events]);
  const saved = useRef(false);
  // One press until the draft changes: Retry repeats it, so a save whose answer was lost is not made twice.
  const press = useRef<string | null>(null);
  const navigate = useNavigate();
  const client = useQueryClient();
  const back = id ? `/projects/${project}/agents/${id}` : `/projects/${project}/agents`;
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && !saved.current && currentLocation.pathname !== nextLocation.pathname);
  const set = (change: Partial<ProjectAgentDefinition>) => { setDraft({ ...draft, ...change }); setIncomplete(false); press.current = null; };
  const missing = !draft.name.trim() || !draft.brief.trim();
  const noDay = draft.schedules.findIndex((schedule) => !schedule.days.length);
  const save = async () => {
    if (missing) {
      setIncomplete(true);
      form.current?.querySelector<HTMLElement>(draft.name.trim() ? "#agent-brief" : "#agent-name")?.focus();
      return;
    }
    if (noDay >= 0) {
      form.current?.querySelectorAll<HTMLElement>("[data-first-day]")[noDay]?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    press.current ??= pressId();
    try {
      const result = await sendProjectAgent<{ agent: string }>(project, id ? "set" : "create",
        { ...(id ? { agent: id, version } : {}), definition: { ...draft, name: draft.name.trim() }, ...(offer ? { offer } : {}) }, press.current);
      saved.current = true;
      void client.invalidateQueries({ queryKey: ["project", project] });
      navigate(`/projects/${project}/agents/${result.agent}`, { replace: true });
    } catch (failure) {
      setError((failure as Error).message);
      setSaving(false);
      // A refusal may mean the agent moved on; reading it again tells whether Retry can still succeed.
      if (id) void client.invalidateQueries({ queryKey: ["project", project, "agents", id] });
    }
  };
  const primary = id ? "Save" : "Create agent";
  return <form ref={form} className="settings-card agent-form" noValidate onSubmit={(event) => { event.preventDefault(); if (!saving) void save(); }}>
    <label className="agent-label" htmlFor="agent-name">Name</label>
    <input id="agent-name" className="field" value={draft.name} placeholder="For example: Dependency check" aria-invalid={incomplete && !draft.name.trim() || undefined}
      onChange={(event) => set({ name: event.target.value })} />
    <label className="agent-label" htmlFor="agent-brief">What it does</label>
    <textarea id="agent-brief" className="field agent-brief-field" rows={5} value={draft.brief} aria-describedby="agent-brief-hint"
      placeholder="For example: check whether the build got slower this week, and say which step." aria-invalid={incomplete && !draft.brief.trim() || undefined}
      onChange={(event) => set({ brief: event.target.value })} />
    <p id="agent-brief-hint" className="text-meta text-muted">Write it as you would brief a task. It can use anything in the repository.</p>
    <fieldset className="agent-fieldset">
      <legend className="agent-label">Runs when</legend>
      <div className="agent-triggers">
        {draft.schedules.map((schedule, index) => <ScheduleRow key={index} index={index} schedule={schedule} clock={clock}
          onChange={(next) => set({ schedules: draft.schedules.map((s, i) => i === index ? next : s) })}
          onRemove={() => set({ schedules: draft.schedules.filter((_, i) => i !== index) })} />)}
        {draft.events ? <EventRow remove={eventRemove} onRemove={() => set({ events: false })} /> : null}
        {draft.schedules.length || draft.events ? null : <p className="agent-trigger agent-trigger-none text-muted">Only when you run it</p>}
        <AddTrigger events={draft.events} onSchedule={() => set({ schedules: [...draft.schedules, NEW_SCHEDULE] })}
          onEvent={() => { eventAdded.current = true; set({ events: true }); }} />
      </div>
      {draft.events ? <p className="text-meta text-muted">Events that arrive during a run go to its next run together.</p> : null}
    </fieldset>
    <fieldset className="agent-fieldset">
      <legend className="agent-label">Tell me in chat</legend>
      <div className="agent-tell">
        {TELL.map((option) => <label key={option.value} className="agent-tell-option">
          <input type="radio" name="agent-tell" value={option.value} checked={draft.tell === option.value} onChange={() => set({ tell: option.value })} />
          <span>{option.label}</span>
        </label>)}
      </div>
      <p className="text-meta text-muted">Suggested tasks wait in Needs you whatever you choose.</p>
    </fieldset>
    <div className="agent-form-pair">
      <div><span className="agent-label">Model</span><ChoiceButton label="Model and effort for this agent" value={draft.model} onChange={(model) => set({ model })} /></div>
      <div><label className="agent-label" htmlFor="agent-limit">Each run stops after</label>
        <select id="agent-limit" className="settings-select agent-limit" value={draft.limit} onChange={(event) => set({ limit: Number(event.target.value) })}>
          {LIMITS.map((limit) => <option key={limit.minutes} value={limit.minutes}>{limit.label}</option>)}
        </select></div>
    </div>
    <div className="agent-form-actions">
      <button type="submit" className="btn btn-primary" aria-disabled={missing || noDay >= 0 || undefined} disabled={saving}>
        <BusyLabel busy={saving} label={primary} working="Saving…" />
      </button>
      <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => navigate(back)}>Cancel</button>
    </div>
    {incomplete && missing ? <p role="alert" className="text-meta text-danger">Give it a name and a brief.</p> : null}
    {error ? <p role="alert" className="text-meta text-danger">{refusal("Not saved", error)}
      {/* Once the agent changed in another window, this edit cannot be saved over it; Cancel opens the new version. */}
      {id && latest !== version ? null : <>{" "}<button type="button" className="link" onClick={() => void save()}>Retry</button></>}</p> : null}
    {blocker.state === "blocked" ? <DiscardDialog detail={id ? `Your edits to ${initial.name} haven’t been saved.` : "Your new agent hasn’t been saved."}
      onKeep={() => blocker.reset()} onDiscard={() => blocker.proceed()} /> : null}
  </form>;
}

export default function ProjectAgentForm() {
  const { name = "", id } = useParams();
  const [search] = useSearchParams();
  const offerId = search.get("offer");
  const agents = useProjectAgents(name);
  const agent = useProjectAgent(id ? name : "", id ?? "");
  const offer = offerId ? agents.data?.offers.find((row) => row.id === offerId) : undefined;
  const reads = [...(id ? [agent] : []), ...(offerId ? [agents] : [])];
  const failed = reads.find((read) => read.isError && !read.data);
  const ready = reads.every((read) => read.data);
  const initial = id ? agent.data?.definition : NEW_DEFINITION;
  const title = id ? "Edit" : "New agent";
  return <AgentFrame back={{ to: id ? `/projects/${name}/agents/${id}` : `/projects/${name}/agents`, label: id ? agent.data?.name ?? "Agent" : name }} title={title}>
    {failed ? <div className="settings-card" role="alert"><p>Could not load this agent.</p>
      <button type="button" className="btn agents-retry" onClick={() => void failed.refetch()}>Retry</button></div>
      : !ready || !initial ? <div className="settings-card"><StatusMark label="Loading this agent…" /></div>
        : <Form key={`${id ?? "new"}:${offerId ?? ""}`} project={name} id={id} read={{ version: agent.data?.version, definition: initial }} latest={agent.data?.version}
          start={offer?.definition ?? initial} offer={offer ? offer.id : null} clock={agents.data?.clock} />}
  </AgentFrame>;
}
