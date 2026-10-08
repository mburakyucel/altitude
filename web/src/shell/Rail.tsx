import { NavLink, useLocation } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import type { EngineReadout, Overview } from "../data/api";
import { RESERVE_PERCENT, age } from "../data/observed";
import { decisionsFor, dotFor, managedProjects, unmanagedFolders } from "./projects";
import { BrandMark } from "./BrandMark";
import { ThemeToggle } from "./theme";
import { attentionCount } from "../data/decisions";
import { NewTasksButton } from "../components/Models";

/** "78% of week", "no reading", "52% of week · reading 3h old" (SPEC.md §3.1). */
export function readoutText(row: EngineReadout): string {
  if (row.week == null) return "no reading";
  const share = `${Math.round(row.week)}% of week`;
  const old = age(row.at);
  return row.stale && old ? `${share} · reading ${old} old` : share;
}

function EngineRow({ row }: { row: EngineReadout }) {
  const week = Math.max(0, Math.min(100, row.week ?? 0));
  return (
    <li className="rail-engine">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium text-ink">{row.label}</span>
        <span className="min-w-0 text-right text-muted">{readoutText(row)}</span>
      </div>
      <div className="meter" aria-hidden>
        <div className="meter-fill" data-danger={week >= RESERVE_PERCENT} style={{ width: `${week}%` }} />
      </div>
    </li>
  );
}

/** The desktop rail (SPEC.md §3.1), fed by the one overview read every page shares. */
export function Rail({
  overview,
  onAddFolder,
}: {
  overview: UseQueryResult<Overview>;
  onAddFolder: () => void;
}) {
  const data = overview.data;
  const location = useLocation();
  const managed = managedProjects(data);
  const unmanaged = unmanagedFolders(data);
  const attention = attentionCount(data, overview.isError);

  return (
    <aside className="rail">
      <div className="rail-brand"><BrandMark size={24} />Altitude</div>
      <nav className="rail-nav" aria-label="Rail">
        <NavLink to="/" end className="rail-item">
          Needs you
          {attention ? <span className="badge" aria-label={attention.label}>{attention.text}</span> : null}
        </NavLink>
      <div className="rail-head">
        <span className="label">Projects</span>
        <button type="button" className="icon-btn" aria-label="Add a folder" onClick={onAddFolder}>
          <svg aria-hidden viewBox="0 0 20 20" width="18" height="18">
            <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="rail-nav" aria-label="Projects">
        {overview.isPending ? (
          <>
            <div className="skeleton mx-3 my-2 h-4" />
            <div className="skeleton mx-3 my-2 h-4" />
          </>
        ) : null}
        {overview.isError ? <p className="px-3 text-meta text-danger">Could not read the projects.</p> : null}
        {managed.map((row) => {
          const decisions = decisionsFor(data, row.name);
          return (
            <NavLink key={row.name} to={`/projects/${row.name}`} className="rail-item">
              <span className="dot" data-state={dotFor(row, decisions)} aria-hidden />
              <span className="truncate">{row.name}</span>
            </NavLink>
          );
        })}
        {data && managed.length === 0 ? <p className="px-3 text-meta text-muted">No project yet.</p> : null}
      </div>
      {unmanaged.length > 0 ? (
        <button type="button" className="rail-item text-meta" onClick={onAddFolder}>
          {unmanaged.length} folder{unmanaged.length === 1 ? "" : "s"} not managed
        </button>
      ) : null}
      {data && data.engines.length > 0 ? (
        <ul className="rail-engines" aria-label="Engines">
          {data.engines.map((row) => (
            <EngineRow key={row.engine} row={row} />
          ))}
        </ul>
      ) : null}
      {data ? <div className="rail-new-tasks"><NewTasksButton overview={data} project={/^\/projects\/([^/]+)/.exec(location.pathname)?.[1]} /></div> : null}
        <NavLink to="/monitor" className="rail-item">
          Monitor
        </NavLink>
      </nav>
      <div className="rail-operator">
        <NavLink to="/settings" className="rail-item min-w-0 flex-1" aria-label="Settings" state={location.pathname.startsWith("/settings") ? location.state : { settingsFrom: location.pathname + location.search }}>
          <span className="truncate font-medium">{data?.operator || "You"}</span><span aria-hidden>⚙</span>
        </NavLink>
        <span className="ml-auto">
          <ThemeToggle />
        </span>
      </div>
    </aside>
  );
}
