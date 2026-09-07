import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useChat, useL3Reset, useL3Start, useOverview, useProject, useProjectRemove } from "../data/api";
import type { ChatView, Decision, EngineReadout, Overview, ProjectView, TaskRow } from "../data/api";
import { agoText, when } from "../data/observed";
import { DecisionCard } from "../components/DecisionCard";
import { TaskCard } from "../components/TaskCard";
import { handling } from "../components/SystemLine";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import { decisionsFor, managedProjects } from "../shell/projects";
import { useStarting } from "../shell/starting";
import Conversation from "./Conversation";
import FirstRun from "./FirstRun";

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

const WEEK_MS = 7 * 86_400_000;
/** The section-move fade (§3.7). */
export const MOVE_MS = 200;

/** Which section a task sits in: a card under Needs you, a row under Active, or a row under Done. */
function sectionOf(task: TaskRow, decided: Set<string>): "needs" | "active" | "done" {
  if (decided.has(task.slug)) return "needs";
  return task.state === "done" || task.state === "rejected" ? "done" : "active";
}

export function WorkPanel({
  name,
  project,
  decisions,
  selected = null,
}: {
  name: string;
  project: UseQueryResult<ProjectView>;
  decisions: Decision[];
  /** The decision whose page is open beside the panel: its card gets the accent border. */
  selected?: string | null;
}) {
  const decided = new Set(decisions.map((d) => d.slug));
  const tasks = project.data?.tasks ?? [];
  const active = tasks.filter((t) => sectionOf(t, decided) === "active");
  const doneThisWeek = (project.data?.archive ?? []).filter((t) => {
    const at = when(t.updated);
    return (t.state === "done" || t.state === "rejected") && at != null && Date.now() - at < WEEK_MS;
  });
  // §3.7: a row whose task just changed section fades in where it now belongs (200ms). The mark
  // outlives the render that noticed the move so the animation finishes whatever refetches meanwhile.
  const sections = useRef(new Map<string, string>());
  const movedAt = useRef(new Map<string, number>());
  const now = new Map<string, string>();
  for (const t of tasks) now.set(t.slug, sectionOf(t, decided));
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
      <TaskCard project={name} task={task} variant="row" decision={decisions.find((d) => d.slug === task.slug)} />
    </div>
  );

  return (
    <section className="work-panel" aria-label="Work">
      <div className="wp-head">
        <h2 className="wp-title">Work</h2>
        <p className="wp-count text-muted">
          {active.length} active · {doneThisWeek.length} done this week
        </p>
      </div>
      {project.isPending ? (
        <div className="flex flex-col gap-3" aria-label="Loading">
          <div className="skeleton h-24" />
          <div className="skeleton h-24" />
          <div className="skeleton h-5" />
          <div className="skeleton h-5" />
          <div className="skeleton h-5" />
        </div>
      ) : project.isError ? (
        <p className="text-meta text-danger">
          Could not read the project's work.{" "}
          <button type="button" className="link" onClick={() => project.refetch()}>
            Retry
          </button>
        </p>
      ) : (
        <>
          {decisions.length > 0 ? (
            <section className="wp-section" aria-label="Needs you">
              <h3 className="wp-section-title">
                Needs you <span className="text-muted">({decisions.length})</span>
              </h3>
              <div className="wp-cards">
                {decisions.map((d) => (
                  <div key={`${d.project}:${d.slug}`} className="wp-row" data-moved={moved.has(d.slug) || undefined}>
                    <DecisionCard decision={d} selected={selected === d.slug} from="project" />
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {active.length > 0 ? (
            <section className="wp-section" aria-label="Active">
              <h3 className="wp-section-title">
                Active <span className="text-muted">({active.length})</span>
              </h3>
              <div className="wp-rows">{active.map(row)}</div>
            </section>
          ) : null}
          {decisions.length === 0 && active.length === 0 ? (
            <p className="text-muted">Nothing running. Ask L3 for something.</p>
          ) : null}
          {doneThisWeek.length > 0 ? (
            <details className="wp-fold">
              <summary className="wp-fold-summary">Done this week ({doneThisWeek.length})</summary>
              <div className="wp-rows">{doneThisWeek.map(row)}</div>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

// ---- the project header (SPEC.md §3.2) --------------------------------------------------------

/** The status line's parts, left to right. */
export function statusParts(
  chat: ChatView | undefined,
  project: ProjectView | undefined,
  decisions: number,
  engines: EngineReadout[],
): string[] {
  const parts: string[] = [];
  if (chat?.active) {
    parts.push(chat.active.trigger === "chat" ? "L3 is answering" : `L3 is handling ${handling(chat.active.trigger)}`);
  } else if (chat) {
    const last = [...chat.history].reverse().find((row) => row.role === "assistant");
    if (last) {
      const label = engines.find((e) => e.engine === last.engine)?.label;
      parts.push(`L3 answered ${agoText(last.at) || "earlier"}${label ? ` on ${label}` : ""}`);
    }
  }
  if (project) {
    const inFlight = project.tasks.filter((t) => t.state === "running" || t.state === "queued").length;
    parts.push(`${inFlight} task${inFlight === 1 ? "" : "s"} in flight`);
  }
  if (decisions > 0) parts.push(`${decisions} wait${decisions === 1 ? "s" : ""} for your review`);
  return parts;
}

function HeaderMenu({ name, designViewer, starting }: {
  name: string; designViewer: string; starting: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<"" | "reset" | "remove">("");
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const reset = useL3Reset(name);
  const remove = useProjectRemove();
  const pending = reset.isPending || remove.isPending || starting;

  const close = useCallback(() => {
    if (pending) return;
    setOpen(false);
    setConfirm("");
    setError("");
  }, [pending]);

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
      if (event.key === "Escape") close();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const confirmRow = (question: string, detail: string, label: string, onConfirm: () => void) => (
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

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="icon-btn"
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
      {open ? (
        <div role="menu" className="menu" aria-label="Project actions">
          {confirm === "reset" ? (
            confirmRow(
              "Reset the L3 conversation?",
              "L3 starts a fresh session on its next turn. Saved history stays available.",
              reset.isPending ? "Resetting…" : "Reset",
              () => reset.mutate(undefined, { onSuccess: completed, onError: failed }),
            )
          ) : (
            <button type="button" role="menuitem" className="menu-item" disabled={pending} onClick={() => choose("reset")}>
              Reset L3 conversation
            </button>
          )}
          {confirm === "remove" ? (
            confirmRow(
              `Remove ${name} from Altitude?`,
              "Removing this project detaches L3 and stops Altitude management. The repository, remaining worktrees, saved history and queued messages stay on disk. Add the same folder with the same project name again to attach L3 and restore history and queued messages. Finish or reject existing tasks first; an L3 turn already running must finish.",
              remove.isPending ? "Removing…" : "Remove",
              () =>
                void remove.mutateAsync({ name }).then(() => {
                  completed();
                  const remaining = managedProjects(queryClient.getQueryData<Overview>(["overview"]));
                  navigate(remaining.length ? "/" : "/projects", { replace: true });
                }).catch(failed),
            )
          ) : (
            <button type="button" role="menuitem" className="menu-item" disabled={pending} onClick={() => choose("remove")}>
              Remove project
            </button>
          )}
          {designViewer ? (
            <a role="menuitem" className="menu-item" href={designViewer} target="_blank" rel="noreferrer">
              Design boards
            </a>
          ) : null}
        </div>
      ) : null}
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
}: {
  name: string;
  project: UseQueryResult<ProjectView>;
  chat: UseQueryResult<ChatView>;
  decisions: number;
  engines: EngineReadout[];
  showName: boolean;
  panelToggle: boolean;
  panelOpen: boolean;
  onTogglePanel: () => void;
}) {
  const start = useL3Start(name);
  const sessionId = str(dict(project.data?.l3).session_id);
  const neverStarted =
    project.isSuccess && chat.isSuccess && !sessionId && chat.data.history.length === 0 && !chat.data.active;
  const status = project.isError
    ? project.error.message
    : neverStarted
      ? "L3 has not started"
      : statusParts(chat.data, project.data, decisions, engines).join(" · ");

  return (
    <header className="project-header">
      <div className="min-w-0 flex-1">
        {showName ? <h1 className="truncate text-[18px] font-semibold">{name}</h1> : null}
        <p className={`truncate text-meta ${project.isError ? "text-danger" : "text-muted"}`} aria-live="polite">
          {status || " "}
        </p>
        {start.isError ? <p className="text-meta text-danger" role="alert">{start.error.message}</p> : null}
      </div>
      {neverStarted ? (
        <button
          type="button"
          className="btn btn-primary"
          disabled={start.isPending}
          onClick={() => start.mutate()}
        >
          {start.isPending ? "Starting…" : "Start L3"}
        </button>
      ) : null}
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
      <HeaderMenu name={name} designViewer={project.data?.design_viewer ?? ""} starting={start.isPending} />
    </header>
  );
}

// ---- the page ---------------------------------------------------------------------------------

/**
 * The project page (SPEC.md §2.1): the §3.2 header, the conversation (§3.3), and the work panel
 * inline, as an overlay, or as the phone's Work tab. With nothing managed, every project route shows
 * First run instead.
 */
export default function ProjectPage() {
  const { name = "" } = useParams();
  const overview = useOverview();
  const managed = !overview.isSuccess || managedProjects(overview.data).some((row) => row.name === name);
  const project = useProject(name, managed);
  const chat = useChat(name, 60, managed);
  const { phone, panelInline } = useViewport();
  const [search] = useSearchParams();
  const tab = search.get("tab") === "work" ? "work" : "chat";
  const [panelOpen, setPanelOpen] = useState(false);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const decisions = decisionsFor(overview.data, name);
  const starting = useStarting();

  if (overview.isPending) {
    return (
      <div className="page" aria-label="Loading">
        <div className="skeleton h-6 w-48" />
      </div>
    );
  }
  // Nothing managed, or First run is still starting a project (the overview lists it as managed
  // before L3's first reply, whatever route the operator is on): First run stays up.
  if (overview.isSuccess && (managedProjects(overview.data).length === 0 || starting)) {
    return (
      <div className="page first-run-page">
        <FirstRun overview={overview} />
      </div>
    );
  }

  const panel = <WorkPanel name={name} project={project} decisions={decisions} />;
  const conversation = <Conversation key={name} name={name} chat={chat} project={project} engines={overview.data?.engines ?? []} />;
  return (
    <div className="project-page">
      <ProjectHeader
        name={name}
        project={project}
        chat={chat}
        decisions={decisions.length}
        engines={overview.data?.engines ?? []}
        showName={!phone}
        panelToggle={!phone && !panelInline}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((open) => !open)}
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
