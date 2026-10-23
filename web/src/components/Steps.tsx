import type { ReactNode } from "react";
import type { StepItem } from "../data/api";
import { clockText } from "../data/observed";
import { StatusMark } from "./StatusMark";
import "./steps.css";

/*
 * What an agent's session did, in a conversation (SPEC.md §3.15, §3.18): a system line, and each step as one of Live
 * session's rows. A repair's page and an agent chat's turns show them.
 */

/** A centred line about the conversation itself: a time when it has one, red for a fault. */
export function SysLine({ children, at, fault = false }: { children: ReactNode; at?: string | null; fault?: boolean }) {
  return <div className="sys-line agent-sys">
    <span className="sys-dot" data-tone={fault ? "danger" : undefined} aria-hidden />
    {at ? <span className="agent-sys-time">{clockText(at)}</span> : null}
    <span className="sys-text">{children}</span>
  </div>;
}

function stepHint(item: StepItem): ReactNode {
  if (item.verb === "running") return <><span className="spinner" aria-hidden /> running</>;
  if (item.verb === "failed") return item.exit != null ? `exit ${item.exit}` : "error";
  if (item.verb === "cut") return "cut off";
  if (item.verb === "changed" || item.verb === "fetched") return item.verb;
  const lines = item.output.split("\n").filter(Boolean).length;
  return lines ? `${lines} line${lines === 1 ? "" : "s"}` : "no output";
}

/** One step as a Live session row: what ran on one line, opening its whole command and what it printed. */
export function StepRow({ item }: { item: StepItem }) {
  const name = item.verb === "changed" ? "Edit" : item.verb === "fetched" ? "Fetch" : "$";
  const bad = item.verb === "failed" || item.verb === "cut";
  return <details className="session-tool agent-step" data-kind={name === "$" ? "command" : "file"} data-error={bad ? "true" : undefined}>
    <summary>
      <b className="session-tool-name">{name}</b>
      <code className="session-call">{item.text}</code>
      <span className="session-hint">{stepHint(item)}</span>
      {item.at ? <span className="session-time">{clockText(item.at)}</span> : null}
    </summary>
    {item.detail && item.detail !== item.text ? <pre className="session-out">{item.detail}</pre> : null}
    {item.output ? <pre className="session-out" data-output="true">{item.output}</pre>
      : <p className="session-notice">{item.verb === "running" ? <StatusMark label="Waiting for output…" /> : "No output."}</p>}
  </details>;
}
