import { useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useMatch, useNavigate } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import { useTask } from "../data/api";
import type { Overview } from "../data/api";
import { useTaskBack } from "../components/useTaskBack";
import FirstRun from "../routes/FirstRun";
import { Overlay } from "./Overlay";
import { decisionsFor, dotFor, managedProjects, unmanagedFolders } from "./projects";
import { setSelectedProject } from "./scope";

/** The tab a task page opened from the decision page keeps lit. */
function activeTabState(from: unknown): "needs" | "work" {
  return from === "needs" ? "needs" : "work";
}

/** The project switcher sheet (SPEC.md §3.11): managed projects, then the folders First run offers. */
function Switcher({
  overview,
  current,
  onClose,
}: {
  overview: UseQueryResult<Overview>;
  current: string;
  onClose: () => void;
}) {
  const data = overview.data;
  return (
    <Overlay label="Switch project" side="bottom" onClose={onClose}>
      <div className="sheet">
        <div className="sheet-handle" aria-hidden />
        <ul className="flex flex-col gap-1" aria-label="Projects">
          {managedProjects(data).map((row) => {
            const decisions = decisionsFor(data, row.name);
            return (
              <li key={row.name}>
                <Link
                  to={`/projects/${row.name}`}
                  className="rail-item"
                  aria-current={row.name === current ? "page" : undefined}
                  onClick={() => {
                    setSelectedProject(row.name);
                    onClose();
                  }}
                >
                  <span className="dot" data-state={dotFor(row, decisions)} aria-hidden />
                  <span className="truncate">{row.name}</span>
                  {decisions.length > 0 ? <span className="badge">{decisions.length}</span> : null}
                </Link>
              </li>
            );
          })}
        </ul>
        <FirstRun overview={overview} compact onStarted={onClose} />
      </div>
    </Overlay>
  );
}

/** The 54px phone header (SPEC.md §2.2): the project name with a chevron on project tabs, a back control
 * and the task's title on a pushed task page (§3.10), a back control, the crumb and Open task on a
 * pushed decision page (§3.9), "Altitude" on the global tabs. */
export function PhoneHeader({ overview, status, children, onTitleClick }: {
  overview: UseQueryResult<Overview>;
  status?: ReactNode;
  children?: ReactNode;
  onTitleClick?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const projectMatch = useMatch("/projects/:name/*");
  const taskMatch = useMatch("/projects/:name/tasks/:slug/*");
  const taskViewMatch = useMatch("/projects/:name/tasks/:slug/live?");
  const decisionMatch = useMatch("/projects/:name/decisions/:slug");
  const pushed = Boolean(taskMatch || decisionMatch);
  const from = location.state && typeof location.state === "object" ? (location.state as { from?: unknown }).from : null;
  const name = projectMatch?.params.name ?? "";
  const taskBack = useTaskBack(name);
  // The same cache entry the task page reads: no request of the header's own.
  const task = useTask(taskMatch?.params.name ?? "", taskMatch?.params.slug ?? "");
  const data = overview.data;
  const managed = managedProjects(data);
  const isProject = Boolean(name) && managed.some((p) => p.name === name);
  // Hidden chevron and no sheet when exactly one project is managed and no folder is unmanaged.
  const switchable = isProject && (managed.length > 1 || unmanagedFolders(data).length > 0);
  const title = taskMatch ? task.data?.title || taskMatch.params.slug
    : decisionMatch ? from === "needs" ? "Needs you" : name : isProject ? name : "Altitude";
  const titleContent = <span className="phone-heading">
    <span className="truncate">{title}</span>
    {status ? <span className="phone-status" aria-live="polite">{status}</span> : null}
  </span>;

  const back = () => {
    if (taskViewMatch) taskBack();
    else if (location.key !== "default") navigate(-1);
    else if (decisionMatch && from === "needs") navigate("/");
    else navigate(`/projects/${name}?tab=work`);
  };

  return (
    <header className="phone-header">
      {pushed ? (
        <button type="button" className="icon-btn" aria-label="Back" onClick={back}>
          <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
            <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      ) : (
        <span className="icon-btn invisible" aria-hidden />
      )}
      <h1 className="phone-title" aria-label={title}>
      {switchable && !pushed ? (
        <button
          type="button"
          className="phone-title phone-title-button"
          aria-label={title}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(true)}
        >
          {titleContent}
          <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
            <path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      ) : onTitleClick ? (
        <button type="button" className="phone-title phone-title-button" aria-label={`${title} details`} aria-haspopup="dialog" onClick={onTitleClick}>
          {titleContent}
        </button>
      ) : titleContent}
      </h1>
      {data?.queue.length ? <Link className="icon-btn phone-needs" to="/" aria-label={`Needs you, ${data.queue.length} pending`}>
        <span className="badge">{data.queue.length}</span>
      </Link> : null}
      {decisionMatch ? (
        <Link className="btn btn-ghost task-action" to={`/projects/${name}/tasks/${decisionMatch.params.slug}`} state={{ tab: activeTabState(from) }}>
          Open task
        </Link>
      ) : (
        children ?? <span className="icon-btn invisible" aria-hidden />
      )}
      {open ? <Switcher overview={overview} current={name} onClose={() => setOpen(false)} /> : null}
    </header>
  );
}
