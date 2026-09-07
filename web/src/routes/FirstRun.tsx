import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { ChatViewSchema, api, useProjectAdd } from "../data/api";
import type { ChatView, Overview } from "../data/api";
import { unmanagedFolders } from "../shell/projects";
import { setSelectedProject } from "../shell/scope";
import { setStarting, useStarting } from "../shell/starting";

/** The last name in a path: what the project is called. */
export function folderName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

const outcomeKey = (name: string | null) => ["chat", name, "start"];

/** Watch the new project's conversation for L3's first reply, or the error that stands in for it. */
function useStartOutcome(name: string | null) {
  return useQuery({
    queryKey: outcomeKey(name),
    queryFn: async () => ChatViewSchema.parse(await api(`/api/chat/${name}?limit=10`)),
    refetchInterval: 1_500,
    enabled: Boolean(name),
  });
}

/**
 * First run (SPEC.md §3.12): the folders under the configured roots with Start L3 on each, and a path
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
  const queryClient = useQueryClient();
  const add = useProjectAdd();
  const [path, setPath] = useState("");
  const starting = useStarting();
  const outcome = useStartOutcome(starting && !starting.failed && starting.seen !== null ? starting.name : null);

  // The overview lists a project as managed as soon as it is added; its row stays here until L3 replies.
  const unmanaged = unmanagedFolders(overview.data);
  const rows = (
    starting && !unmanaged.some((row) => row.name === starting.name)
      ? [{ name: starting.name, path: starting.path, managed: false }, ...unmanaged]
      : unmanaged
  ).sort((a, b) => a.name.localeCompare(b.name));
  const roots = overview.data?.roots ?? [];
  const root = roots.join(" and ") || "the configured root";

  const start = async (name: string, folder: string) => {
    const seen = queryClient.getQueryData<ChatView>(outcomeKey(name))?.history.length ?? 0;
    setStarting({ name, path: folder, failed: null, seen: null });
    try {
      const result = await add.mutateAsync({ name, path: folder });
      if (result.restored) {
        setSelectedProject(name);
        setStarting(null);
        onStarted?.();
        navigate(`/projects/${name}`);
      } else {
        setStarting({ name, path: folder, failed: null, seen });
      }
    } catch (error) {
      setStarting({ name, path: folder, failed: (error as Error).message, seen });
    }
  };

  // The project page opens on L3's first reply; a failed turn leaves an error row instead.
  const history = outcome.data?.history;
  useEffect(() => {
    if (!starting || starting.failed || starting.seen === null || !history) return;
    const reply = history
      .slice(starting.seen)
      .reverse()
      .find((row) => row.role === "assistant" || row.role === "error");
    if (!reply) return;
    if (reply.role === "assistant") {
      setSelectedProject(starting.name);
      setStarting(null);
      onStarted?.();
      navigate(`/projects/${starting.name}`);
    } else {
      // The row already says the turn failed; the sentence around it says so once.
      setStarting({ ...starting, failed: reply.text.replace(/^L3 turn failed:\s*/, "") });
    }
  }, [history, starting, onStarted, navigate]);

  const busy = Boolean(starting && !starting.failed);
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
            const mine = starting?.name === row.name;
            return (
              <li key={row.name} className="first-run-row">
                <div className="min-w-0">
                  <div className="truncate font-medium">{row.name}</div>
                  <div className="truncate text-meta text-muted">{folder}</div>
                </div>
                {mine && busy ? (
                  <span className="ml-auto flex items-center gap-2 text-meta text-muted" role="status">
                    <span className="spinner" aria-hidden /> L3 is starting…
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary ml-auto whitespace-nowrap"
                    disabled={busy}
                    onClick={() => start(row.name, folder)}
                  >
                    {mine && starting?.failed ? "Retry" : "Start L3"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
      {starting?.failed ? (
        <p className="text-meta text-danger" role="alert">
          {`L3 could not start for ${starting.name}: ${starting.failed}`}
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
            {busy && starting?.path === path.trim() ? "L3 is starting…" : "Start L3"}
          </button>
        </div>
      </form>
    </section>
  );
}
