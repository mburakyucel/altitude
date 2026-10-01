import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import FolderBrowser from "../components/FolderBrowser";
import Onboarding from "../components/Onboarding";
import { addProject, saveProjectsFolder, useProjectAdd } from "../data/api";
import type { Overview } from "../data/api";
import { useToast } from "../data/Toast";
import { unmanagedFolders } from "../shell/projects";
import { setSelectedProject } from "../shell/scope";

type AddAll = { status: "idle" } | { status: "adding"; done: number; total: number } | { status: "failed"; name: string; error: Error };

/**
 * First run (SPEC.md §3.12). The page shown while nothing is managed is the onboarding flow, ending in the
 * projects step: the projects folder with Change…, the folders directly inside it with Add project and Add all,
 * and the folder browser for a folder elsewhere. A dialog from the rail and the tail of the phone's switcher
 * sheet (`compact`) show the folder list alone.
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
  if (compact || onStarted) return <Folders overview={overview} compact={compact} onStarted={onStarted} />;
  return <Onboarding projects={<Folders overview={overview} onboarding />} />;
}

function Folders({
  overview,
  compact = false,
  onboarding = false,
  onStarted,
}: {
  overview: UseQueryResult<Overview>;
  compact?: boolean;
  onboarding?: boolean;
  onStarted?: () => void;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const toast = useToast();
  const open = (name: string) => {
    setSelectedProject(name);
    onStarted?.();
    navigate(`/projects/${name}?setup=1`);
  };
  const add = useProjectAdd(open);
  const [browsing, setBrowsing] = useState(false);
  const [changing, setChanging] = useState(false);
  const [folderSave, setFolderSave] = useState<{ status: "idle" | "saving" } | { status: "failed"; error: Error }>({ status: "idle" });
  const [addAll, setAddAll] = useState<AddAll>({ status: "idle" });
  const rows = unmanagedFolders(overview.data).sort((a, b) => a.name.localeCompare(b.name));
  const roots = overview.data?.roots ?? [];
  const root = roots.join(" and ") || "the projects folder";
  const inContainer = overview.data?.deployment === "container";

  const start = (name: string, folder: string) => {
    add.mutate({ name, path: folder });
  };
  const chooseFolder = async (path: string) => {
    setFolderSave({ status: "saving" });
    try {
      await saveProjectsFolder(path);
      await client.invalidateQueries({ queryKey: ["overview"] });
      setFolderSave({ status: "idle" });
      setChanging(false);
    } catch (error) {
      setFolderSave({ status: "failed", error: error as Error });
    }
  };
  // Adds the folders in order, then opens the first one's Setup. A refusal before anything was added stays here
  // with Retry; after that, the added projects open and a toast names the folder Add a folder still offers.
  const addEvery = async () => {
    let opened: string | null = null;
    for (const [i, row] of rows.entries()) {
      setAddAll({ status: "adding", done: i, total: rows.length });
      try {
        await addProject({ name: row.name, path: row.path ?? "" });
        opened ??= row.name;
      } catch (error) {
        if (!opened) return setAddAll({ status: "failed", name: row.name, error: error as Error });
        toast.show({ severity: "failure", message: `Could not add ${row.name}: ${(error as Error).message}. Add it again from Add a folder.` });
        break;
      }
    }
    await client.invalidateQueries({ queryKey: ["overview"] });
    setAddAll({ status: "idle" });
    if (opened) open(opened);
  };
  const busy = add.isPending || addAll.status === "adding" || folderSave.status === "saving";
  const empty = !overview.isPending && !overview.isError && rows.length === 0;
  const heading = overview.isPending
    ? "Looking for folders…"
    : rows.length === 0
      ? `No folders in ${root} yet`
      : `Altitude found ${rows.length} folder${rows.length === 1 ? "" : "s"} in ${root}`;

  const list = <>
    {empty ? (
      <p className="text-meta text-muted">
        {compact ? `No folders in ${root} yet. ` : null}
        {onboarding ? "Change the projects folder, or choose a folder elsewhere below." : <>Choose a folder below, or{" "}
          <Link className="link" to="/settings/projects-folder" onClick={() => onStarted?.()}>change the projects folder in Settings</Link>.</>}
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
    {onboarding && rows.length > 1 ? (
      <div className="flex items-center gap-3">
        <span className="text-meta text-muted">{rows.length} folders</span>
        {addAll.status === "adding" ? (
          <span className="ml-auto flex items-center gap-2 text-meta text-muted" role="status">
            <span className="spinner" aria-hidden /> Adding {addAll.done + 1} of {addAll.total}…
          </span>
        ) : (
          <button type="button" className="btn ml-auto" disabled={busy} onClick={() => void addEvery()}>
            {addAll.status === "failed" ? "Retry Add all" : `Add all ${rows.length}`}
          </button>
        )}
      </div>
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
              {mine && add.isPending ? (
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
    {addAll.status === "failed" ? (
      <p className="text-meta text-danger" role="alert">{`Could not add ${addAll.name}: ${addAll.error.message}`}</p>
    ) : null}
    {browsing || (empty && !onboarding) ? (
      <FolderBrowser
        action="Add"
        busy={add.isPending}
        busyLabel="Adding project…"
        onChoose={(folder, name) => start(name, folder)}
        onCancel={empty && !onboarding ? undefined : () => setBrowsing(false)}
      />
    ) : onboarding ? (
      <p className="text-meta text-muted">
        {inContainer ? "Choose another folder in the container projects volume:" : "A project can live anywhere:"}{" "}
        <button type="button" className="link" disabled={busy} onClick={() => setBrowsing(true)}>Choose a folder elsewhere…</button>{" "}
        {inContainer ? "lets you browse or type its container path." : "lets you browse or type any absolute path."}
      </p>
    ) : (
      <button type="button" className="btn self-start" disabled={busy} onClick={() => setBrowsing(true)}>
        Choose a folder elsewhere…
      </button>
    )}
  </>;

  if (onboarding) {
    return <div className="onboarding-projects">
      <div className="settings-card onboarding-folder">
        <div className="min-w-0">
          <div className="text-meta text-muted">Projects folder</div>
          <strong className="block truncate">{roots.join(" and ") || "…"}</strong>
        </div>
        {changing ? null : <button type="button" className="btn" disabled={busy} onClick={() => setChanging(true)}>Change…</button>}
      </div>
      {changing ? <>
        <FolderBrowser action="Use" allowHome busy={folderSave.status === "saving"} busyLabel="Saving…"
          onChoose={(path) => void chooseFolder(path)} onCancel={() => { setChanging(false); setFolderSave({ status: "idle" }); }} />
        {folderSave.status === "failed" ? <p role="alert" className="text-meta text-danger">{folderSave.error.message}</p> : null}
      </> : <>
        {empty ? <div className="settings-card"><strong>{heading}</strong></div> : null}
        {list}
      </>}
    </div>;
  }
  return (
    <section className={compact ? "first-run first-run-compact" : "first-run"} aria-label="First run">
      {compact ? <h2 className="label">Add a folder</h2> : <h1 className="text-card-title font-semibold">{heading}</h1>}
      {compact && rows.length > 0 ? <p className="text-meta text-muted">{heading}</p> : null}
      {list}
    </section>
  );
}
