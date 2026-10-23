import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { Link, useLocation, useMatch, useNavigate, useParams, useSearchParams } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import { BusyLabel } from "../components/StatusMark";
import { useChat, useL3Reset, useL3Start, useOverview, useProject, useWaitsForTool } from "../data/api";
import type { Attention, ChatView, Decision, EngineReadout, Overview, ProjectView, TaskRow } from "../data/api";
import { agoText } from "../data/observed";
import { attentionSummary, isTaskDecision } from "../data/decisions";
import { TaskCard, cardView } from "../components/TaskCard";
import { AgentGraph, taskActivity } from "../components/AgentGraph";
import type { GraphNode } from "../components/AgentGraph";
import { useNow } from "../components/TaskActivity";
import { useWorkView } from "../shell/workView";
import { WorkRepairs } from "../components/Repairs";
import { RoleModelButton } from "../components/ModelMenu";
import { handling } from "../components/SystemLine";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import { PhoneHeader } from "../shell/PhoneHeader";
import { decisionsFor, managedProjects } from "../shell/projects";
import { ProjectSetup, useSetupReading } from "./Setup";
import Conversation from "./Conversation";
import FirstRun from "./FirstRun";
import Terminal from "../components/Terminal";
import { settingsOpener } from "../components/visitMemory";

/** The payload's loose corners (live, l3, config) arrive as `unknown`. */
function dict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

// ---- the work panel (SPEC.md §3.7) ------------------------------------------------------------

/** The section-move fade (§3.7). */
export const MOVE_MS = 200;

/** Decisions never remove a task from the project's current work. */
function sectionOf(task: TaskRow): "active" | "done" {
  return task.state === "done" || task.state === "rejected" ? "done" : "active";
}

/** L3 has no session, history or turn yet. */
function neverStarted(project: ProjectView, chat: ChatView): boolean {
  return !str(dict(project.l3).session_id) && chat.history.length === 0 && !chat.active;
}

/** The coordinator's compact word (§3.2): Answering, Handling …, Busy or Ready. */
export function l3Word(chat: ChatView): string {
  return chat.active ? chat.active.trigger === "chat" ? "Answering" : `Handling ${handling(chat.active.trigger)}` : chat.busy ? "Busy" : "Ready";
}

const LIST_ICON = <svg aria-hidden viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
  <path d="M8 6h12M8 12h12M8 18h12" /><circle cx="4" cy="6" r="1" fill="currentColor" /><circle cx="4" cy="12" r="1" fill="currentColor" /><circle cx="4" cy="18" r="1" fill="currentColor" />
</svg>;
const GRAPH_ICON = <svg aria-hidden viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
  <circle cx="12" cy="12" r="3" /><circle cx="5" cy="5" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="18" cy="19" r="2" /><path d="M6.5 6.5l3.3 3.3M17.3 7.2l-3.2 2.9M16.6 17.6l-2.6-3.4" />
</svg>;

/** Work's Current as the graph (§3.7): the same rows, read again every few seconds so live cues age out. */
function WorkGraph({ name, project, chat, current, runs }: {
  name: string;
  project: UseQueryResult<ProjectView>;
  chat: UseQueryResult<ChatView>;
  current: TaskRow[];
  runs: NonNullable<ProjectView["project_agents"]>["runs"];
}) {
  const overview = useOverview();
  const now = useNow();
  const slugs = new Set(current.map((t) => t.slug));
  const nodes: GraphNode[] = [
    ...current.map((task) => {
      const card = cardView(name, task, overview);
      const after = task.planned_wait?.after;
      return {
        key: task.slug, title: card.title, attention: card.attention, meta: card.meta, dot: card.dot, to: card.to,
        linkState: { from: "project", tab: "work" }, questions: card.questions, run: false,
        after: after && slugs.has(after) ? after : null, ...taskActivity(task, now, project.isError),
      };
    }),
    ...runs.map((run) => ({
      key: `agent:${run.agent}:${run.n}`, title: run.name, attention: null, meta: run.line,
      dot: (run.state === "waiting" ? "idle" : "running") as GraphNode["dot"], to: `/projects/${name}/agents/${run.agent}/runs/${run.n}`,
      linkState: { from: "project", tab: "work" }, questions: 0, run: true, after: null, live: false, helpers: 0,
    })),
  ];
  return <AgentGraph project={name} nodes={nodes} saved={project.isError || chat.isError}
    l3={{ word: !chat.data || !project.data ? "" : neverStarted(project.data, chat.data) ? "Not started" : l3Word(chat.data),
      live: Boolean(chat.data?.active) }} />;
}

export function WorkPanel({
  name,
  project,
  chat,
}: {
  name: string;
  project: UseQueryResult<ProjectView>;
  chat: UseQueryResult<ChatView>;
}) {
  const overview = useOverview();
  const { phone } = useViewport();
  const [chosen, setView] = useWorkView();
  // The list is the phone's only view (§3.7).
  const view = phone ? "list" : chosen;
  const decisions = decisionsFor(overview.data, name).filter(isTaskDecision);
  const tasks = project.data?.tasks ?? [];
  // Overview and project reads can arrive in either order; retain newly published waiting tasks.
  const current = [...new Map([...tasks, ...decisions.filter((d) => !tasks.some((t) => t.slug === d.slug)
    && !project.data?.archive?.some((t) => t.slug === d.slug))]
    .map((t) => [t.slug, t])).values()].filter((t) => sectionOf(t) === "active");
  // The server sends every task finished in the last seven days, newest first.
  const doneThisWeek = project.data?.archive ?? [];
  // §3.7: a row whose task just changed section fades in where it now belongs (200ms). The mark
  // outlives the render that noticed the move so the animation finishes whatever refetches meanwhile.
  const sections = useRef(new Map<string, string>());
  const movedAt = useRef(new Map<string, number>());
  const now = new Map<string, string>();
  for (const t of current) now.set(t.slug, "active");
  for (const t of doneThisWeek) now.set(t.slug, "done");
  for (const [slug, section] of now) {
    const before = sections.current.get(slug);
    if (before && before !== section) movedAt.current.set(slug, Date.now());
  }
  useEffect(() => {
    sections.current = now;
  });
  const moved = { has: (slug: string) => Date.now() - (movedAt.current.get(slug) ?? 0) < MOVE_MS * 5 };
  const row = (task: TaskRow) => (
    <div key={`${name}:${task.slug}`} className="wp-row" data-moved={moved.has(task.slug) || undefined}>
      <TaskCard project={name} task={task} variant="row" />
    </div>
  );
  // Routine runs waiting or running sit in Current as rows of their own; they are not tasks, so nothing else counts them.
  const agents = project.data?.project_agents;
  const runs = agents?.runs ?? [];
  const inCurrent = current.length + runs.length;
  return (
    <section className="work-panel" aria-label="Work" data-view={view}>
      <div className="wp-head">
        <div className="wp-heading">
          <h2 className="wp-title">Work</h2>
          {project.data ? <p className="wp-count text-muted">
            {inCurrent} current · {doneThisWeek.length} done this week{project.isError ? " · saved" : ""}
          </p> : null}
        </div>
        {phone ? null : <div className="wp-view" role="group" aria-label="Show work as">
          <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>{LIST_ICON}List</button>
          <button type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}>{GRAPH_ICON}Graph</button>
        </div>}
      </div>
      {phone ? <RoleModelButton variant="row" /> : null}
      {project.isError ? <p className="text-meta text-danger" role="alert">
        {project.data ? "Showing saved work." : "Could not read the project's work."}{" "}
        <button type="button" className="link" onClick={() => project.refetch()}>Retry</button>
      </p> : null}
      {overview.isError ? <p className="text-meta text-danger" role="alert">
        {overview.data ? "Attention status is saved; refresh to update." : "Attention status unavailable."}{" "}
        <button type="button" className="link" onClick={() => overview.refetch()}>Refresh</button>
      </p> : null}
      {project.isPending ? view === "graph" ? <div className="skeleton ag-skeleton" aria-label="Loading" /> : (
        <div className="flex flex-col gap-3" aria-label="Loading">
          <div className="skeleton h-5" />
          <div className="skeleton h-5" />
          <div className="skeleton h-5" />
        </div>
      ) : project.data ? (
        <>
          {view === "graph" ? <WorkGraph name={name} project={project} chat={chat} current={current} runs={runs} /> : null}
          {inCurrent > 0 && view === "list" ? (
            <section className="wp-section" aria-label="Current">
              <h3 className="wp-section-title">
                Current <span className="text-muted">({inCurrent})</span>
              </h3>
              <div className="wp-rows">
                {current.map(row)}
                {runs.map((run) => (
                  <div key={`${name}:agent:${run.agent}:${run.n}`} className="wp-row">
                    <Link className="task-card" data-variant="row" to={`/projects/${name}/agents/${run.agent}/runs/${run.n}`}
                      state={{ from: "project", tab: "work" }} aria-label={`${run.name} · ${run.line}`}>
                      <span className="dot task-card-dot" data-state={run.state === "waiting" ? "idle" : "running"} aria-hidden />
                      <span className="task-card-body">
                        <span className="task-card-title">{run.name}</span>
                        <span className="task-card-meta">{run.line}</span>
                      </span>
                      <svg className="task-card-go" aria-hidden viewBox="0 0 20 20" width="16" height="16">
                        <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </Link>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {inCurrent === 0 ? (
            <p className="text-muted">No current tasks. Ask L3 to start something.</p>
          ) : null}
          <WorkRepairs project={name} />
          {doneThisWeek.length > 0 ? (
            <details className="wp-fold">
              <summary className="wp-fold-summary">Done this week ({doneThisWeek.length})</summary>
              <div className="wp-rows">{doneThisWeek.map(row)}</div>
            </details>
          ) : null}
          {agents?.count ? (
            <Link className="wp-agents" to={`/projects/${name}/agents`} state={{ from: "project", tab: "work" }}>
              Routines · {agents.count}
              <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
                <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

// ---- the project header (SPEC.md §3.2) --------------------------------------------------------

/** The status line's parts, left to right. */
export function statusParts(
  chat: ChatView | undefined,
  project: ProjectView | undefined,
  decisions: Attention[],
  engines: EngineReadout[],
): string[] {
  const parts: string[] = [];
  if (chat?.active) {
    parts.push(chat.active.trigger === "chat" ? "L3 is answering" : `L3 is handling ${handling(chat.active.trigger)}`);
  } else if (chat) {
    // A routine run's result line is Altitude's, not an answer from L3.
    const last = [...chat.history].reverse().find((row) => row.role === "assistant" && row.trigger !== "project-agent");
    if (last) {
      const label = engines.find((e) => e.engine === last.engine)?.label;
      parts.push(`L3 answered ${agoText(last.at) || "earlier"}${label ? ` on ${label}` : ""}`);
    }
  }
  if (project) {
    const inFlight = project.tasks.filter((t) => t.state === "running" || t.state === "queued").length;
    parts.push(`${inFlight} task${inFlight === 1 ? "" : "s"} in flight`);
  }
  if (decisions.length > 0) parts.push(`${attentionSummary(decisions)} ${decisions.length === 1 ? "needs" : "need"} you`);
  return parts;
}

function HeaderMenu({ name, designViewer, starting }: { name: string; designViewer: string; starting: boolean }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<"" | "reset">("");
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const shown = useRef<typeof confirm>("");
  const navigate = useNavigate();
  const reset = useL3Reset(name);
  const setup = useSetupReading(name);
  const pending = reset.isPending || starting;
  const location = useLocation();
  const settings = (path: string) => navigate(path, { state: settingsOpener(location) });

  const close = useCallback((refocus = false) => {
    if (pending) return;
    setOpen(false);
    setConfirm("");
    setError("");
    if (refocus) opener.current?.focus();
  }, [pending]);

  // Menu-button focus: opening enters the first item, a confirmation takes focus and Cancel returns it to its item.
  useEffect(() => {
    const menu = ref.current?.querySelector<HTMLElement>('[role="menu"]');
    if (!open || !menu) { shown.current = ""; return; }
    const target = confirm ? menu.querySelector<HTMLElement>('[role="group"] button')
      : menu.querySelector<HTMLElement>(shown.current ? `[data-action="${shown.current}"]` : '[role="menuitem"]');
    target?.focus();
    shown.current = confirm;
  }, [open, confirm]);

  const onMenuKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
    if (next === undefined || !items.length) return;
    event.preventDefault();
    items[(next + items.length) % items.length]!.focus();
  };

  const choose = (action: typeof confirm) => {
    setError("");
    setConfirm(action);
  };
  const failed = (error: Error) => setError(error.message);
  const completed = () => {
    setOpen(false);
    setConfirm("");
    setError("");
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(ref.current?.contains(document.activeElement) ?? false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const confirmRow = (question: string, detail: string, label: ReactNode, onConfirm: () => void) => (
    <div className="menu-confirm" role="group" aria-label={question}>
      <span className="text-meta">{question}</span>
      {detail ? <p className="text-meta text-muted">{detail}</p> : null}
      {error ? <p className="text-meta text-danger" role="alert">{error}</p> : null}
      <div className="flex gap-2">
        <button type="button" className="btn btn-primary" disabled={pending} onClick={() => { setError(""); onConfirm(); }}>
          {label}
        </button>
        <button type="button" className="btn btn-ghost" disabled={pending} onClick={() => choose("")}>
          Cancel
        </button>
      </div>
    </div>
  );

  const menu = (
        <div role="menu" className="menu project-menu" aria-label="Project actions" onKeyDown={onMenuKey}>
          <button type="button" role="menuitem" className="menu-item" disabled={pending} onClick={() => settings(`/settings/projects/${name}`)}>Project settings…</button>
          <button type="button" role="menuitem" className="menu-item" disabled={pending} onClick={() => navigate(`/projects/${name}/agents`)}>Routines…</button>
          <button type="button" role="menuitem" className="menu-item" data-action="setup" aria-label={`Setup: ${setup.label}`} aria-haspopup="dialog" disabled={pending} onClick={() => { close(true); setup.open(); }}>
            Setup…<span className="menu-item-meta">{setup.label}</span>
          </button>
          {confirm === "reset" ? (
            confirmRow(
              "Reset the L3 conversation?",
              "L3 starts a fresh session on its next turn. Saved history stays available.",
              <BusyLabel busy={reset.isPending} label="Reset" working="Resetting…" />,
              () => reset.mutate(undefined, { onSuccess: completed, onError: failed }),
            )
          ) : (
            <button type="button" role="menuitem" className="menu-item" data-action="reset" disabled={pending} onClick={() => choose("reset")}>
              Reset L3 conversation…
            </button>
          )}
          <button type="button" role="menuitem" className="menu-item" disabled={pending} onClick={() => settings("/settings")}>All settings…</button>
          {designViewer ? (
            <a role="menuitem" className="menu-item" href={designViewer} target="_blank" rel="noreferrer">
              Design boards <span aria-hidden>↗</span>
            </a>
          ) : null}
        </div>
  );

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="icon-btn"
        ref={opener}
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={pending}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
          <circle cx="4" cy="10" r="1.6" fill="currentColor" />
          <circle cx="10" cy="10" r="1.6" fill="currentColor" />
          <circle cx="16" cy="10" r="1.6" fill="currentColor" />
        </svg>
      </button>
      {open ? menu : null}
    </div>
  );
}

function ProjectHeader({
  name,
  project,
  chat,
  decisions,
  engines,
  showName,
  panelToggle,
  panelOpen,
  onTogglePanel,
  terminalOpen,
  onToggleTerminal,
  overview,
}: {
  name: string;
  project: UseQueryResult<ProjectView>;
  chat: UseQueryResult<ChatView>;
  decisions: Attention[];
  engines: EngineReadout[];
  showName: boolean;
  panelToggle: boolean;
  panelOpen: boolean;
  onTogglePanel: () => void;
  terminalOpen: boolean;
  onToggleTerminal: () => void;
  overview: UseQueryResult<Overview>;
}) {
  const start = useL3Start(name);
  const notStarted = project.isSuccess && chat.isSuccess && neverStarted(project.data, chat.data);
  const waiting = useWaitsForTool(name) && notStarted;
  const status = project.isError
    ? project.error.message
    : waiting
      ? "L3 waits for a coding tool"
      : notStarted
        ? "L3 has not started"
      : statusParts(chat.data, project.data, overview.isSuccess ? decisions : [], engines).join(" · ");
  const engine = engines.find((e) => e.engine === chat.data?.engine)?.label;
  const compactStatus = project.isError ? "Could not read project" : waiting ? "L3 · Waiting" : notStarted ? "L3 · Not started"
    : !chat.data ? "L3 · Loading…" : `L3 · ${l3Word(chat.data)}`;
  const startButton = notStarted && !waiting ? <button type="button" className="btn btn-primary" disabled={start.isPending} onClick={() => start.mutate()}>
    <BusyLabel busy={start.isPending} label="Start L3" working="Starting…" />
  </button> : null;
  const menu = <HeaderMenu key={name} name={name} designViewer={project.data?.design_viewer ?? ""} starting={start.isPending} />;

  if (!showName) return <>
    <PhoneHeader overview={overview} status={`${compactStatus}${engine ? ` · ${engine}` : ""}`}>
      {startButton}<ProjectSetup key={`setup:${name}`} name={name} />
      <button type="button" className="icon-btn" aria-label="Terminal" onClick={onToggleTerminal}>
        <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
          <rect x="2.5" y="3.5" width="15" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M6 8l2.5 2L6 12M10 12.5h4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {menu}
    </PhoneHeader>
    {project.isError ? <p className="convo-error text-danger" role="alert">{project.error.message} <button type="button" className="link" onClick={() => void project.refetch()}>Retry</button></p> : null}
    {start.isError ? <p className="convo-error text-danger" role="alert">{start.error.message}</p> : null}
  </>;

  return (
    <header className="project-header">
      <div className="min-w-0 flex-1">
        {showName ? <h1 className="truncate text-[18px] font-semibold">{name}</h1> : null}
        <p className={`truncate text-meta ${project.isError ? "text-danger" : "text-muted"}`} aria-live="polite">
          {status || " "}
        </p>
        {start.isError ? <p className="text-meta text-danger" role="alert">{start.error.message}</p> : null}
      </div>
      {startButton}
      <ProjectSetup key={`setup:${name}`} name={name} />
      <button type="button" className="btn" aria-pressed={terminalOpen} onClick={onToggleTerminal}>Terminal</button>
      {panelToggle ? (
        <button
          type="button"
          className="icon-btn"
          aria-label="Work panel"
          aria-pressed={panelOpen}
          onClick={onTogglePanel}
        >
          <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
            <rect x="3" y="4" width="14" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M12 4v12" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </button>
      ) : null}
      {menu}
    </header>
  );
}

// ---- the page ---------------------------------------------------------------------------------

/**
 * The project page (SPEC.md §2.1): the §3.2 header, the conversation (§3.3), and the work panel
 * inline, as an overlay, or as the phone's Work tab. `/terminal` shows the project folder's terminal in
 * place of the work panel, full screen on phone. With nothing managed, every project route shows First
 * run instead.
 */
export default function ProjectPage() {
  const { name = "" } = useParams();
  const overview = useOverview();
  const managed = !overview.isSuccess || managedProjects(overview.data).some((row) => row.name === name);
  const project = useProject(name, managed);
  const location = useLocation();
  // A link to an improvement review's card or line (#improvement-<run>) reads the conversation back to it, so an
  // older run opened from its page or Latest still comes into view; only a run the project lists is reached.
  const named = location.hash.startsWith("#improvement-") ? location.hash.slice("#improvement-".length) : null;
  const reach = named && project.data?.improvements?.some((run) => run.id === named) ? named : undefined;
  const chat = useChat(name, 60, managed, reach);
  const refetchChat = chat.refetch;
  useEffect(() => {
    if (reach) void refetchChat();
  }, [reach, refetchChat]);
  const { phone, panelInline } = useViewport();
  const [search] = useSearchParams();
  const tab = search.get("tab") === "work" ? "work" : "chat";
  const [panelOpen, setPanelOpen] = useState(false);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const decisions = decisionsFor(overview.data, name);
  const navigate = useNavigate();
  // `/terminal`: the project folder's terminal, in the desktop panel or full screen on phone.
  const terminalRoute = Boolean(useMatch("/projects/:name/terminal"));
  const home = `/projects/${name}`;
  const leaveTerminal = useCallback(() => {
    if (location.key !== "default") navigate(-1);
    else navigate(home, { replace: true });
  }, [home, location.key, navigate]);

  if (overview.isPending && project.isPending) {
    return (
      <>
      {phone ? <PhoneHeader overview={overview} status="L3 · Loading…" /> : null}
      <div className="page" aria-label="Loading">
        <div className="skeleton h-6 w-48" />
      </div>
      </>
    );
  }
  if (overview.isSuccess && managedProjects(overview.data).length === 0) {
    return (
      <>
      {phone ? <PhoneHeader overview={overview} /> : null}
      <div className="page first-run-page">
        <FirstRun overview={overview} />
      </div>
      </>
    );
  }

  if (phone && terminalRoute) {
    return <div className="terminal-page">
      <Terminal key={name} project={name} keys onLeave={leaveTerminal} head={(close) => <header className="phone-header">
        <button type="button" className="icon-btn" aria-label="Back" onClick={leaveTerminal}>
          <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
            <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <h1 className="phone-title"><span className="phone-heading">
          <span className="truncate">Terminal</span><span className="phone-status">{name} · project folder</span>
        </span></h1>
        {close}
      </header>} />
    </div>;
  }

  const panel = terminalRoute
    ? <Terminal key={name} project={name} keys={false} onLeave={() => void navigate(home, { replace: true })}
      head={(close) => <header className="live-head"><h2 className="live-title">Terminal</h2>{close}</header>} />
    : <WorkPanel name={name} project={project} chat={chat} />;
  const conversation = <Conversation key={name} name={name} chat={chat} project={project} />;
  return (
    <div className="project-page">
      <ProjectHeader
        overview={overview}
        name={name}
        project={project}
        chat={chat}
        decisions={decisions}
        engines={overview.data?.engines ?? []}
        showName={!phone}
        panelToggle={!phone && !panelInline}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((open) => !open)}
        terminalOpen={terminalRoute}
        onToggleTerminal={() => (terminalRoute ? navigate(home, { replace: true }) : navigate(`${home}/terminal`))}
      />
      {phone ? (
        tab === "work" ? (
          panel
        ) : (
          conversation
        )
      ) : (
        <div className="project-body">
          {conversation}
          {panelInline ? (
            panel
          ) : terminalRoute ? (
            <Overlay label="Terminal" side="right" onClose={() => navigate(home, { replace: true })}>
              {panel}
            </Overlay>
          ) : panelOpen ? (
            <Overlay label="Work" side="right" onClose={closePanel}>
              {panel}
            </Overlay>
          ) : null}
        </div>
      )}
    </div>
  );
}
