import { useState } from "react";
import { Link, useNavigate } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import { useProjectAdd } from "../data/api";
import type { Overview } from "../data/api";
import { unmanagedFolders } from "../shell/projects";
import { setSelectedProject } from "../shell/scope";

/**
 * First run (SPEC.md §3.12): the folders directly inside the projects folder with Add project on each, and
 * the folder browser for a folder elsewhere. The page when nothing is managed, a dialog from the rail, and the tail
 * of the phone's switcher sheet (`compact`) are the same component.
 */
export default function FirstRun({
  overview,
  compact = false,
  onStarted,
}: {
  overview: UseQueryResult<Overview>;
  compact?: boolean;
  onStarted?: () => void;
}) {
  const navigate = useNavigate();
  const add = useProjectAdd((name) => {
    setSelectedProject(name);
    onStarted?.();
    navigate(`/projects/${name}?setup=1`);
  });
  const [browsing, setBrowsing] = useState(false);
  const rows = unmanagedFolders(overview.data).sort((a, b) => a.name.localeCompare(b.name));
  const roots = overview.data?.roots ?? [];
  const root = roots.join(" and ") || "the projects folder";

  const start = (name: string, folder: string) => {
    add.mutate({ name, path: folder });
  };
  const busy = add.isPending;
  const empty = !overview.isPending && !overview.isError && rows.length === 0;
  const heading = overview.isPending
    ? "Looking for folders…"
    : rows.length === 0
      ? `No folders in ${root} yet`
      : `Altitude found ${rows.length} folder${rows.length === 1 ? "" : "s"} in ${root}`;

  return (
    <section className={compact ? "first-run first-run-compact" : "first-run"} aria-label="First run">
      {compact ? <h2 className="label">Add a folder</h2> : <h1 className="text-card-title font-semibold">{heading}</h1>}
      {compact && rows.length > 0 ? <p className="text-meta text-muted">{heading}</p> : null}
      {empty ? (
        <p className="text-meta text-muted">
          {compact ? `No folders in ${root} yet. ` : null}Choose a folder below, or{" "}
          <Link className="link" to="/settings/projects-folder" onClick={() => onStarted?.()}>change the projects folder in Settings</Link>.
        </p>
      ) : null}
      {overview.isError ? (
        <p className="text-meta text-danger">
          Could not list the projects folder. <button type="button" className="link" onClick={() => overview.refetch()}>Retry</button>
        </p>
      ) : null}
      {overview.isPending ? (
        <ul className="flex flex-col gap-2" aria-hidden>
          <li className="skeleton h-10" />
          <li className="skeleton h-10" />
          <li className="skeleton h-10" />
        </ul>
      ) : null}
      {rows.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {rows.map((row) => {
            const folder = row.path ?? "";
            const mine = add.variables?.name === row.name;
            return (
              <li key={row.name} className="first-run-row">
                <div className="min-w-0">
                  <div className="truncate font-medium">{row.name}</div>
                  <div className="truncate text-meta text-muted">{folder}</div>
                </div>
                {mine && busy ? (
                  <span className="ml-auto flex items-center gap-2 text-meta text-muted" role="status">
                    <span className="spinner" aria-hidden /> Adding project…
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary ml-auto whitespace-nowrap"
                    disabled={busy}
                    onClick={() => start(row.name, folder)}
                  >
                    {mine && add.isError ? "Retry" : "Add project"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
      {add.isError ? (
        <p className="text-meta text-danger" role="alert">
          {`Could not add ${add.variables.name}: ${add.error.message}`}
        </p>
      ) : null}
      {browsing || empty ? (
        <FolderBrowser
          action="Add"
          busy={busy}
          busyLabel="Adding project…"
          onChoose={(folder, name) => start(name, folder)}
          onCancel={empty ? undefined : () => setBrowsing(false)}
        />
      ) : (
        <button type="button" className="btn self-start" disabled={busy} onClick={() => setBrowsing(true)}>
          Choose a folder elsewhere…
        </button>
      )}
    </section>
  );
}
