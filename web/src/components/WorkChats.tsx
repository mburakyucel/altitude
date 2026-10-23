import { Link } from "react-router";
import { useAgentChats, useOverview } from "../data/api";
import type { AgentChatRow, Overview } from "../data/api";
import { agoText, modelName, when } from "../data/observed";
import "./chats.css";

/*
 * Work's Agent chats (SPEC.md §3.17): the project's chats in task rows, newest activity first, with New chat on the
 * heading and the archived ones folded at the end. While the list loads the section is absent; a failed refresh keeps
 * the rows it has.
 */

/** "Opus 5 on Claude": the model a chat's agent runs, on its engine's label. */
export function chatEngine(chat: Pick<AgentChatRow, "engine" | "model">, overview: Overview | undefined) {
  if (!chat.engine) return "";
  const engine = overview?.engines.find((row) => row.engine === chat.engine)?.label ?? chat.engine;
  return chat.model ? `${modelName(chat.model)} on ${engine}` : engine;
}

/** A chat's dot, in the task dot's states. */
export function chatDot(state: AgentChatRow["state"]) {
  return state === "answering" || state === "starting" ? "running" : state === "failed" ? "danger" : "idle";
}

function archivedOn(value: string | null) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

function rowMeta(row: AgentChatRow, overview: Overview | undefined) {
  const engine = chatEngine(row, overview);
  if (row.state === "starting" || row.state === "answering") {
    return [row.state === "starting" ? "Starting" : "Answering", engine].filter(Boolean).join(" · ");
  }
  if (row.state === "archived") return `Archived ${archivedOn(row.archived)}`;
  if (row.state === "failed") return `Couldn’t answer · ${agoText(row.latest.at)}`;
  return row.latest.by === "agent" ? `Replied ${agoText(row.latest.at)}` : `You wrote ${agoText(row.latest.at)}`;
}

const GO = <svg className="task-card-go" aria-hidden viewBox="0 0 20 20" width="16" height="16">
  <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

function ChatRowLink({ project, row, overview }: { project: string; row: AgentChatRow; overview: Overview | undefined }) {
  const meta = rowMeta(row, overview);
  return <div className="wp-row" data-archived={row.archived ? true : undefined}>
    <Link className="task-card chat-row" data-variant="row" to={`/projects/${project}/chats/${row.id}`} state={{ from: "project", tab: "work" }}
      aria-label={`${row.name} · ${meta}`}>
      <span className="dot task-card-dot" data-state={chatDot(row.state)} aria-hidden />
      <span className="task-card-body">
        <span className="task-card-title chat-row-name">{row.name}</span>
        <span className="task-card-meta" data-tone={row.state === "failed" ? "danger" : undefined}>{meta}</span>
      </span>
      {GO}
    </Link>
  </div>;
}

export function WorkChats({ project }: { project: string }) {
  const chats = useAgentChats(project);
  const overview = useOverview(false);
  if (!chats.data) return null;
  const open = chats.data.chats.filter((row) => !row.archived);
  const archived = chats.data.chats.filter((row) => row.archived);
  return <section className="wp-section work-chats" aria-label="Agent chats">
    <div className="work-chats-head">
      <h3 className="wp-section-title">Agent chats{open.length ? <> <span className="text-muted">({open.length})</span></> : null}</h3>
      <Link className="chat-new" to={`/projects/${project}/chats/new`} state={{ from: "project", tab: "work" }}>
        <svg aria-hidden viewBox="0 0 20 20" width="14" height="14"><path d="M10 4v12M4 10h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
        New chat
      </Link>
    </div>
    {open.length ? <div className="wp-rows">{open.map((row) => <ChatRowLink key={row.id} project={project} row={row} overview={overview.data} />)}</div> : null}
    {archived.length ? <details className="wp-fold">
      <summary className="wp-fold-summary">Archived · {archived.length}</summary>
      <div className="wp-rows">{archived.map((row) => <ChatRowLink key={row.id} project={project} row={row} overview={overview.data} />)}</div>
    </details> : null}
  </section>;
}
