import { Suspense, lazy, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { terminalOpen, terminalSend, terminalStatus, useOverview, useTerminalStatus } from "../data/api";
import type { TerminalStatus } from "../data/api";
import "./terminal.css";

// xterm.js loads only when a terminal is on screen.
const TerminalScreen = lazy(() => import("./TerminalScreen"));

const BOOT_KEY = "altitude.terminal.boot:";

/** The altd process that held this page's last terminal here: a different one means a restart ended it. */
function rememberedBoot(key: string): string | null {
  try { return sessionStorage.getItem(BOOT_KEY + key); } catch { return null; }
}
function rememberBoot(key: string, boot: string | null) {
  try {
    if (boot) sessionStorage.setItem(BOOT_KEY + key, boot);
    else sessionStorage.removeItem(BOOT_KEY + key);
  } catch { /* Without session storage a restart reads as a fresh Ready state. */ }
}

function Card({ title, children, action, tone }: { title?: string; children?: ReactNode; action?: ReactNode; tone?: "danger" }) {
  return <div className="terminal-card" role={tone === "danger" ? "alert" : "status"}>
    {title ? <h3>{title}</h3> : null}
    {children ? <p className={tone === "danger" ? "text-danger" : "text-muted"}>{children}</p> : null}
    {action ? <div className="terminal-card-actions">{action}</div> : null}
  </div>;
}

function ended(status: TerminalStatus): { title: string; text: string; again: boolean } {
  if (status.reason === "task-finished") return { title: "Terminal closed", text: "This task finished and its worktree was removed, so its terminal ended.", again: false };
  if (status.reason === "project-removed") return { title: "Terminal closed", text: "This project is no longer managed, so its terminal ended.", again: false };
  return { title: `Terminal closed · exit code ${status.exit_code ?? "unknown"}`, text: "The last output stays readable until you leave.", again: true };
}

/**
 * The operator's terminal for one task worktree (`task`) or project folder, with every state the
 * design names (SPEC.md §3.10): off, ready, starting, running, reconnecting, closed, restarted and
 * could not start. `head` renders the view's header around the Close control; without it Close sits
 * in the folder line. Leaving keeps a running shell; leaving an ended one drops its replay.
 */
export default function Terminal({ project, task, keys, head, closeLabel, onClosed }: {
  project: string;
  task?: string;
  closeLabel?: string;
  /** The phone key row: Esc, Tab, Ctrl and arrows. */
  keys: boolean;
  head?: (close: ReactNode) => ReactNode;
  /** After the operator closes the shell here. */
  onClosed?: () => void;
}) {
  const client = useQueryClient();
  const location = useLocation();
  const overview = useOverview();
  const status = useTerminalStatus(project, task);
  const queryKey = ["terminal", project, task ?? null];
  const memory = `${project}:${task ?? ""}`;
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  const data = status.data;
  const set = (next: TerminalStatus) => client.setQueryData(queryKey, next);

  // A remembered boot only matters while nothing is open here: once read, the page moves on.
  const [restarted, setRestarted] = useState(false);
  useEffect(() => {
    if (!data) return;
    if (data.state === "none") {
      const boot = rememberedBoot(memory);
      if (boot && boot !== data.boot) setRestarted(true);
      rememberBoot(memory, null);
    } else rememberBoot(memory, data.boot);
  }, [data, memory]);

  // A shell the operator closed (here, in another tab, or by turning the terminal off) returns to Ready.
  useEffect(() => {
    if (data?.state !== "exited" || data.reason !== "closed") return;
    void terminalSend(project, "forget", { task }).catch(() => undefined)
      .then(() => { set({ ...data, state: "none" }); });
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  // Leaving an ended terminal drops its replay: the last output stays readable only until then.
  const endedHere = data?.state === "exited";
  useEffect(() => {
    if (!endedHere) return;
    return () => { void terminalSend(project, "forget", { task }).catch(() => undefined); };
  }, [endedHere, project, task]);

  // Opening replaces an ended terminal here; a running one is returned as it is.
  const open = async () => {
    setOpening(true);
    setOpenError(null);
    setRestarted(false);
    try {
      set(await terminalOpen(project, task));
    } catch (error) {
      setOpenError((error as Error).message);
    } finally {
      setOpening(false);
    }
  };

  const close = async (confirmed: boolean) => {
    setCloseError(null);
    try {
      if (!confirmed) {
        const now = await terminalStatus(project, task);
        if (now.busy) { setConfirm(now.busy); return; }
      }
      setConfirm(null);
      await terminalSend(project, "close", { task });
      onClosed?.();
    } catch (error) {
      setCloseError((error as Error).message);
    }
  };

  const running = data?.state === "running";
  const closeButton = running ? <button type="button" className="btn btn-ghost terminal-close" onClick={() => void close(false)}>
    {closeLabel ?? (head ? "Close terminal" : "Close")}
  </button> : null;
  const where = task ? "Task worktree" : "Project folder";
  const restart = overview.data?.restart;

  let content: ReactNode;
  if (status.isPending || opening || (data?.state === "exited" && data.reason === "closed")) {
    content = <div className="terminal-card" role="status" aria-label="Starting the terminal">
      <div className="skeleton h-4 w-2/3" /><div className="skeleton h-4 w-1/3" />
      <p className="text-muted">{opening ? "Starting the terminal…" : "Loading the terminal…"}</p>
    </div>;
  } else if (status.isError && !data) {
    content = <Card tone="danger" title="Couldn't read the terminal" action={<button type="button" className="btn" onClick={() => void status.refetch()}>Retry</button>}>{status.error.message}</Card>;
  } else if (openError) {
    content = <Card tone="danger" title="Couldn't open a terminal" action={<button type="button" className="btn" onClick={() => void open()}>Retry</button>}>{openError}</Card>;
  } else if (data!.state === "none") {
    content = restarted
      ? <Card title="Terminal closed" action={<button type="button" className="btn" onClick={() => void open()}>Open a new terminal</button>}>Altitude restarted, which ends open terminals. Open a new one to continue.</Card>
      : !data!.enabled
        ? <Card title="Terminal is off" action={<Link className="btn" to="/settings" state={{ settingsFrom: `${location.pathname}${location.search}` }}>Open Settings</Link>}>A terminal runs any command as you on this computer. Turn it on for this computer in Settings.</Card>
        : <Card title={task ? "Open a terminal in this task's worktree" : "Open a terminal in the project folder"}
          action={<button type="button" className="btn btn-primary" onClick={() => void open()}>Open terminal</button>}>
          {task ? "It runs as you, outside the L2's sandbox. Nothing you type is sent to the L2 or saved in the task."
            : "It runs as you on this computer. Nothing you type is sent to L3 or saved in the project."}
        </Card>;
  } else {
    const end = data!.state === "exited" ? ended(data!) : null;
    content = <>
      {confirm ? <Card title="Close the terminal?" action={<>
        <button type="button" className="btn btn-primary" onClick={() => void close(true)}>Close</button>
        <button type="button" className="btn" onClick={() => setConfirm(null)}>Cancel</button>
      </>}>{`${confirm} is still running and will be stopped.`}</Card> : null}
      {closeError ? <p role="alert" className="text-meta text-danger">{closeError}</p> : null}
      {reconnecting && running ? <p className="terminal-note" role="status">Connection lost · reconnecting. The shell keeps running; missed output appears when you're back.</p> : null}
      {end ? <Card title={end.title} action={end.again ? <button type="button" className="btn" onClick={() => void open()}>Open a new terminal</button> : null}>{end.text}</Card> : null}
      <Suspense fallback={<div className="terminal-screen" aria-label="Loading the terminal" />}>
        <TerminalScreen key={data!.id} project={project} task={task} id={data!.id!} keys={keys && running}
          onEnd={set} onReconnecting={setReconnecting} />
      </Suspense>
    </>;
  }

  return <section className="terminal-panel" aria-label="Terminal">
    {head ? head(closeButton) : null}
    <div className="terminal-body">
      <div className="terminal-where">
        <p className="text-meta text-muted">{where} · runs as you on this computer</p>
        {head ? null : closeButton}
      </div>
      {/* The folder's end is what tells worktrees apart, so a long path gives up its start. */}
      {data?.folder && data.state !== "none" ? <p className="terminal-folder" title={data.folder}><bdi>{data.folder}</bdi></p> : null}
      <p className="terminal-warning">{task ? "This is the L2's worktree. Files you change here become part of its work and its PR."
        : "Altitude lands PRs from this folder's clean main. Make code changes in a task, not here."}</p>
      {running && restart && !restart.failed ? <p className="terminal-note" role="status">Altitude restarts at its next quiet point to apply an update. This terminal will close then.</p> : null}
      {content}
    </div>
  </section>;
}
