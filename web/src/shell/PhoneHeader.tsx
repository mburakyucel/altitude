import { useState } from "react";
import { Link, useLocation, useMatch, useNavigate } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import { useTask } from "../data/api";
import type { Overview } from "../data/api";
import FirstRun from "../routes/FirstRun";
import { Overlay } from "./Overlay";
import { decisionsFor, dotFor, managedProjects, unmanagedFolders } from "./projects";
import { setSelectedProject } from "./scope";

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
 * and the task's title on a pushed task page (§3.10), "Altitude" on the global tabs. */
export function PhoneHeader({ overview }: { overview: UseQueryResult<Overview> }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const projectMatch = useMatch("/projects/:name/*");
  const taskMatch = useMatch("/projects/:name/tasks/:slug/*");
  const name = projectMatch?.params.name ?? "";
  // The same cache entry the task page reads: no request of the header's own.
  const task = useTask(taskMatch?.params.name ?? "", taskMatch?.params.slug ?? "");
  const data = overview.data;
  const managed = managedProjects(data);
  const isProject = Boolean(name) && managed.some((p) => p.name === name);
  // Hidden chevron and no sheet when exactly one project is managed and no folder is unmanaged.
  const switchable = isProject && (managed.length > 1 || unmanagedFolders(data).length > 0);

  const back = () => {
    if (location.key !== "default") navigate(-1);
    else navigate(`/projects/${name}?tab=work`);
  };

  return (
    <header className="phone-header">
      {taskMatch ? (
        <button type="button" className="icon-btn" aria-label="Back" onClick={back}>
          <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
            <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      ) : (
        <span className="icon-btn invisible" aria-hidden />
      )}
      {switchable && !taskMatch ? (
        <button
          type="button"
          className="phone-title phone-title-button"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(true)}
        >
          <span className="truncate">{name}</span>
          <svg aria-hidden viewBox="0 0 20 20" width="16" height="16">
            <path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      ) : (
        <h1 className="phone-title">
          <span className="truncate">
            {taskMatch ? task.data?.title || taskMatch.params.slug : isProject ? name : "Altitude"}
          </span>
        </h1>
      )}
      <span className="icon-btn invisible" aria-hidden />
      {open ? <Switcher overview={overview} current={name} onClose={() => setOpen(false)} /> : null}
    </header>
  );
}
