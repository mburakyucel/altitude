import { Suspense, lazy, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { terminalOpen, terminalSend, terminalStatus, useOverview, useTerminalStatus } from "../data/api";
import type { TerminalStatus } from "../data/api";
import { useToast } from "../data/Toast";
import { subscribeCommands, takeCommand } from "../data/terminalCommand";
import "./terminal.css";

// xterm.js loads only when a terminal is on screen.
const TerminalScreen = lazy(() => import("./TerminalScreen"));

function Card({ title, children, action, tone }: { title?: string; children?: ReactNode; action?: ReactNode; tone?: "danger" }) {
  return <div className="terminal-card" role={tone === "danger" ? "alert" : "status"}>
    {title ? <h3>{title}</h3> : null}
    {children ? <p className={tone === "danger" ? "text-danger" : "text-muted"}>{children}</p> : null}
    {action ? <div className="terminal-card-actions">{action}</div> : null}
  </div>;
}

/** What the operator is told after a terminal ends without their Close here; nothing for a clean exit. */
function endNotice(status: TerminalStatus): string | null {
  if (status.state !== "exited") return "The terminal closed while the connection was lost.";
  if (status.reason === "task-finished") return "The task finished, so its terminal closed.";
  if (status.reason === "project-removed") return "The project is no longer managed, so its terminal closed.";
  if (status.reason === "closed") return "The terminal was closed elsewhere.";
  return status.exit_code ? `Terminal closed · exit code ${status.exit_code}` : null;
}

/**
 * The operator's terminal for one task worktree (`task`) or project folder (SPEC.md §3.10). Showing it
 * opens the shell, or attaches to the one already running; when the shell ends, however it ends, the view
 * leaves through `onLeave` with a short notice when the end needs one. `head` renders the view's header
 * around the Close control.
 */
export default function Terminal({ project, task, keys, head, closeIcon, onLeave }: {
  project: string;
  task?: string;
  /** The phone key row: Esc, Tab, Ctrl, arrows and Paste. */
  keys: boolean;
  head: (close: ReactNode) => ReactNode;
  /** Close as a × (the phone task tab) rather than a button. */
  closeIcon?: boolean;
  /** Back to where the operator was: Live session on a task, the project otherwise. */
  onLeave: () => void;
}) {
  const client = useQueryClient();
  const location = useLocation();
  const toast = useToast();
  const overview = useOverview();
  const status = useTerminalStatus(project, task);
  const queryKey = ["terminal", project, task ?? null];
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  // Set once this view has asked to open or seen its terminal end: it never opens a second shell by itself.
  const attempted = useRef(false);
  // The terminal this view has shown: any other answer means it ended.
  const shown = useRef<string | null>(null);
  const closing = useRef(false);
  const left = useRef(false);
  const data = status.data;
  // A chat command to type at this terminal's prompt (SPEC.md §3.3): taken once, dropped when the view
  // leaves or cannot show a running shell.
  const [command, setCommand] = useState<{ text: string; at: number; seq: number } | null>(null);
  useEffect(() => {
    const pick = () => {
      const taken = takeCommand(project, task);
      if (taken) setCommand((last) => ({ ...taken, seq: (last?.seq ?? 0) + 1 }));
    };
    pick();
    return subscribeCommands(pick);
  }, [project, task]);

  const open = async () => {
    attempted.current = true;
    setOpening(true);
    setOpenError(null);
    try {
      client.setQueryData(queryKey, await terminalOpen(project, task));
    } catch (error) {
      setOpenError((error as Error).message);
    } finally {
      setOpening(false);
    }
  };

  // Showing the terminal is the request to open it. A status or open answer can also be the first to say
  // that the shell on screen ended (while the connection was down) or was replaced elsewhere.
  useEffect(() => {
    if (!data) return;
    if (data.state === "running" && (shown.current === null || shown.current === data.id)) {
      shown.current = data.id!;
      attempted.current = true;
    } else if (data.state === "exited") {
      leave(data);
    } else if (shown.current !== null) {
      leave(data.state === "running" ? { ...data, state: "exited", reason: "closed" } : data);
    } else if (data.enabled && !attempted.current) {
      void open();
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const leave = (ended: TerminalStatus) => {
    if (left.current) return;
    left.current = true;
    attempted.current = true;
    const notice = closing.current ? null : endNotice(ended);
    if (notice) toast.show({ message: notice, severity: "info" });
    client.setQueryData(queryKey, { state: "none", enabled: ended.enabled });
    onLeave();
  };

  const close = async (confirmed: boolean) => {
    setCloseError(null);
    try {
      if (!confirmed) {
        const now = await terminalStatus(project, task);
        if (now.busy) { setConfirm(now.busy); return; }
      }
      setConfirm(null);
      closing.current = true;
      await terminalSend(project, "close", { task, id: data!.id! });
      leave({ ...data!, state: "exited", reason: "closed" });
    } catch (error) {
      closing.current = false;
      setCloseError((error as Error).message);
    }
  };

  const running = data?.state === "running" && (shown.current === null || shown.current === data.id);
  const closeButton = running ? closeIcon
    ? <button type="button" className="icon-btn terminal-close-icon" aria-label="Close terminal" onClick={() => void close(false)}>
      <svg aria-hidden viewBox="0 0 20 20" width="16" height="16"><path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
    </button>
    : <button type="button" className="btn terminal-close" onClick={() => void close(false)}>Close</button> : null;
  const restart = overview.data?.restart;
  const off = data?.state === "none" && !data.enabled;
  useEffect(() => {
    if (command && (off || openError || (status.isError && !data))) setCommand(null);
  }, [command, off, openError, status.isError, data]);

  let content: ReactNode;
  if (status.isError && !data) {
    content = <Card tone="danger" title="Couldn't read the terminal" action={<button type="button" className="btn" onClick={() => void status.refetch()}>Retry</button>}>{status.error.message}</Card>;
  } else if (openError) {
    content = <Card tone="danger" title="Couldn't open a terminal" action={<button type="button" className="btn" onClick={() => void open()}>Retry</button>}>{openError}</Card>;
  } else if (off) {
    content = <Card title="Terminal is off" action={<Link className="btn" to="/settings" state={{ settingsFrom: `${location.pathname}${location.search}` }}>Open Settings</Link>}>A terminal runs any command as you on this computer. Turn it on for this computer in Settings.</Card>;
  } else if (!running) {
    content = <div className="terminal-card" role="status" aria-label="Starting the terminal">
      <div className="skeleton h-4 w-2/3" /><div className="skeleton h-4 w-1/3" />
      <p className="text-muted">{opening ? "Starting the terminal…" : "Loading the terminal…"}</p>
    </div>;
  } else {
    content = <>
      {confirm ? <Card title="Close the terminal?" action={<>
        <button type="button" className="btn btn-primary" onClick={() => void close(true)}>Close</button>
        <button type="button" className="btn" onClick={() => setConfirm(null)}>Cancel</button>
      </>}>{`${confirm} is still running and will be stopped.`}</Card> : null}
      {closeError ? <p role="alert" className="text-meta text-danger">{closeError}</p> : null}
      {restart && !restart.failed ? <p className="terminal-note" role="status">Altitude restarts at its next quiet point to apply an update. This terminal will close then.</p> : null}
      <Suspense fallback={<div className="terminal-screen" aria-label="Loading the terminal" />}>
        <TerminalScreen key={data.id} project={project} task={task} id={data.id!} keys={keys} reconnecting={reconnecting}
          intro={`Runs as you in ${data.folder}`} command={command} onCommand={() => setCommand(null)}
          onEnd={leave} onReconnecting={setReconnecting} />
      </Suspense>
    </>;
  }

  return <section className="terminal-panel" aria-label="Terminal">
    {head(closeButton)}
    <div className="terminal-body">{content}</div>
  </section>;
}
