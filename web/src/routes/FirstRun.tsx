import { useState } from "react";
import { useNavigate } from "react-router";
import type { UseQueryResult } from "@tanstack/react-query";
import { useProjectAdd } from "../data/api";
import type { Overview } from "../data/api";
import { unmanagedFolders } from "../shell/projects";
import { setSelectedProject } from "../shell/scope";

/** The last name in a path: what the project is called. */
export function folderName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

/**
 * First run (SPEC.md §3.12): the folders under the configured roots with Add project on each, and a path
 * field for a folder elsewhere. The page when nothing is managed, a dialog from the rail, and the tail
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
  const [path, setPath] = useState("");
  const rows = unmanagedFolders(overview.data).sort((a, b) => a.name.localeCompare(b.name));
  const roots = overview.data?.roots ?? [];
  const root = roots.join(" and ") || "the configured root";

  const start = (name: string, folder: string) => {
    add.mutate({ name, path: folder });
  };
  const busy = add.isPending;
  const heading = overview.isPending
    ? "Scanning for folders…"
    : rows.length === 0
      ? `Altitude found no folders under ${root}.`
      : `Altitude found ${rows.length} folder${rows.length === 1 ? "" : "s"} under ${root}`;

  return (
    <section className={compact ? "first-run first-run-compact" : "first-run"} aria-label="First run">
      {compact ? <h2 className="label">Add a folder</h2> : <h1 className="text-card-title font-semibold">{heading}</h1>}
      {compact && rows.length > 0 ? <p className="text-meta text-muted">{heading}</p> : null}
      {overview.isError ? (
        <p className="text-meta text-danger">
          Could not scan for folders. <button type="button" className="link" onClick={() => overview.refetch()}>Retry</button>
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
      <form
        className="first-run-path"
        onSubmit={(event) => {
          event.preventDefault();
          const folder = path.trim();
          if (folder && folderName(folder)) start(folderName(folder), folder);
        }}
      >
        <label className="text-meta text-muted" htmlFor="first-run-path">
          A folder elsewhere
        </label>
        <div className="flex gap-2">
          <input
            id="first-run-path"
            className="field min-w-0 flex-1"
            placeholder="~/work/my-project"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            disabled={busy}
          />
          <button type="submit" className="btn" disabled={busy || !path.trim()}>
            {busy && add.variables?.path === path.trim() ? "Adding project…" : "Add project"}
          </button>
        </div>
      </form>
    </section>
  );
}
