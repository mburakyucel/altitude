import { useState } from "react";
import { useFolders } from "../data/api";
import "./folder-browser.css";

type Crumb = { name: string; path: string };

/** The last name in a path: what the project is called. */
export function folderName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

const folderIcon = (
  <svg aria-hidden viewBox="0 0 24 24" width="18" height="18" className="folder-icon">
    <path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h4l2 2.5h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
  </svg>
);

/**
 * Folders on the computer running Altitude (SPEC.md §3.12): starts at the home folder, lists one opened
 * folder's subfolders, and chooses the current folder with one action. "Type a path instead" names a
 * folder outside the browsable area. First run adds a project with it; Settings picks the projects folder.
 */
export default function FolderBrowser({
  action,
  busy = false,
  busyLabel,
  allowHome = false,
  onChoose,
  onCancel,
}: {
  action: string;
  busy?: boolean;
  busyLabel: string;
  allowHome?: boolean;
  onChoose: (path: string, name: string) => void;
  onCancel?: () => void;
}) {
  const [trail, setTrail] = useState<Crumb[]>([]);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const current = trail.at(-1);
  const listing = useFolders(current?.path);
  const view = listing.data;
  const inContainer = view?.location === "container";
  const rootName = inContainer ? "Projects" : "Home";
  const here = current?.name ?? rootName;
  const chooseDisabled = busy || !view || !view.readable || (!current && !allowHome);
  const cancel = onCancel ? <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>Cancel</button> : null;

  if (typing) {
    const folder = typed.trim();
    return (
      <form
        className="folder-browser folder-typed"
        onSubmit={(event) => {
          event.preventDefault();
          if (folder && folderName(folder)) onChoose(folder, folderName(folder));
        }}
      >
        <label className="text-meta text-muted" htmlFor="folder-typed-path">{inContainer ? "A folder in the container projects volume" : "A folder on the computer running Altitude"}</label>
        <input
          id="folder-typed-path"
          className="field"
          placeholder={inContainer ? "/home/altitude/Projects/my-project" : "/srv/work/my-project"}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          disabled={busy}
          autoFocus
        />
        <div className="folder-foot">
          <button type="button" className="link" onClick={() => setTyping(false)} disabled={busy}>Browse folders instead</button>
          <span className="flex-1" />
          {cancel}
          <button type="submit" className="btn btn-primary" disabled={busy || !folder}>
            {busy ? <><span className="spinner" aria-hidden /> {busyLabel}</> : action}
          </button>
        </div>
      </form>
    );
  }

  return (
    <section className="folder-browser" aria-label="Choose a folder">
      <div className="folder-top">
        <p className="text-meta text-muted">{inContainer ? "Folders in the container projects volume" : "Folders on the computer running Altitude"}</p>
        <nav aria-label="Folder path" className="folder-crumbs">
          {[{ name: rootName, path: "" }, ...trail].map((crumb, index) => index === trail.length ? (
            <span key={crumb.path} aria-current="location" className="folder-crumb-here">{crumb.name}</span>
          ) : (
            <span key={crumb.path} className="folder-crumb">
              <button type="button" className="link" disabled={busy} onClick={() => setTrail(trail.slice(0, index))}>{crumb.name}</button>
              <span aria-hidden className="text-muted">›</span>
            </span>
          ))}
        </nav>
      </div>
      {listing.isPending ? (
        <ul className="folder-list" aria-hidden>
          <li className="skeleton h-8" /><li className="skeleton h-8" /><li className="skeleton h-8" />
        </ul>
      ) : listing.isError ? (
        <div className="folder-state" role="alert">
          <p className="text-danger">{listing.error.message}</p>
          <button type="button" className="btn" onClick={() => void listing.refetch()}>Retry</button>
        </div>
      ) : !view!.readable ? (
        <div className="folder-state" role="status">
          <p>Altitude can’t open <strong>{here}</strong>: your account can’t read it.</p>
          <p className="text-meta text-muted">Go back with the path above, or choose another folder.</p>
        </div>
      ) : view!.folders.length === 0 ? (
        <div className="folder-state" role="status">
          <p>No folders inside <strong>{here}</strong>.</p>
          {current || allowHome ? <p className="text-meta text-muted">You can still choose it.</p> : null}
        </div>
      ) : (
        <ul className="folder-list">
          {view!.folders.map((folder) => (
            <li key={folder.path}>
              <button type="button" className="folder-row" disabled={busy}
                onClick={() => setTrail([...trail, { name: folder.name, path: folder.path }])}>
                {folderIcon}
                <span className="truncate">{folder.name}</span>
                <span className="folder-tags">
                  {folder.project ? <span className="chip">Project</span> : null}
                  {folder.git ? <span className="chip">git</span> : null}
                  <span aria-hidden>›</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="folder-foot">
        <button type="button" className="link" onClick={() => setTyping(true)} disabled={busy}>Type a path instead</button>
        <span className="flex-1" />
        {cancel}
        <button type="button" className="btn btn-primary" disabled={chooseDisabled}
          onClick={() => view && onChoose(view.path, current?.name ?? folderName(view.path))}>
          {busy ? <><span className="spinner" aria-hidden /> {busyLabel}</> : `${action} “${here}”`}
        </button>
      </div>
    </section>
  );
}
