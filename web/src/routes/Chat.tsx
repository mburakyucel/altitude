import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Bubble, DayDivider, Reply } from "../components/Bubbles";
import Composer from "../components/Composer";
import { MessageImages, sentBy } from "../components/MessageImages";
import { ChoiceButton } from "../components/ModelMenu";
import { Prose, ProseScope } from "../components/Prose";
import { BusyLabel } from "../components/StatusMark";
import { StepRow, SysLine } from "../components/Steps";
import { TaskCards } from "../components/TaskCard";
import { chatDot, chatEngine } from "../components/WorkChats";
import { ApiError, agentChatRequest, deleteAgentChat, newRequest, taskMap, useAgentChat, useAgentChatAction, useOverview, useProject } from "../data/api";
import type { AgentChat, AgentChatItem, Choice, Overview, TaskRow } from "../data/api";
import { dayText, when } from "../data/observed";
import { Overlay } from "../shell/Overlay";
import { useViewport } from "../shell/breakpoints";
import "../components/chats.css";

/*
 * One agent chat (SPEC.md §3.18, design/wireframes/chats/PROPOSAL.md): a task page's header with the Work crumb, its
 * name, Stop while it answers and ⋯ (Rename, Make a task…, Archive); one conversation of your messages, each turn's
 * steps folded under one line, and its reply, with the card of each task made from it; the composer with the model
 * pill. `/chats/new` is the empty chat that the first message makes.
 */

type Turn = Extract<AgentChatItem, { kind: "turn" }>;
type ChatEvent = Extract<AgentChatItem, { kind: "event" }>;
type Step = Turn["steps"][number];

/** "3 min", "1 h 4 min": how long a turn worked. */
function span(from: string, to: string | null) {
  const minutes = Math.max(1, Math.round(((when(to) ?? Date.now()) - (when(from) ?? Date.now())) / 60_000));
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}` : `${minutes} min`;
}

function StepItem({ step }: { step: Step }) {
  return step.kind === "say" ? <div className="chat-say"><Prose text={step.text} /></div> : <StepRow item={step} />;
}

/** A turn's steps folded to one line; while it works the line counts them and the latest shows under it. */
function Worked({ turn, engine }: { turn: Turn; engine: string }) {
  const [open, setOpen] = useState(false);
  const live = !turn.ended;
  const count = turn.steps.filter((step) => step.kind === "step").length;
  const steps = `${count} step${count === 1 ? "" : "s"}`;
  if (!live && !turn.steps.length) return null;
  const line = turn.state === "starting" ? ["Starting", engine].filter(Boolean).join(" · ")
    : live ? `Working · ${span(turn.started, null)} · ${steps}` : `Worked ${span(turn.started, turn.ended)} · ${steps}`;
  const latest = live && !open ? turn.steps.at(-1) : undefined;
  return <div className="chat-worked" data-live={live || undefined}>
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        {live ? <span className="spinner" aria-hidden /> : <svg className="chat-worked-caret" aria-hidden viewBox="0 0 20 20" width="14" height="14">
          <path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>}
        <span>{line}</span>
      </summary>
      {turn.steps.length ? <div className="agent-steps">{turn.steps.map((step, i) => <StepItem key={i} step={step} />)}</div> : null}
    </details>
    {latest ? <div className="agent-steps chat-latest"><StepItem step={latest} /></div> : null}
  </div>;
}

function reason(text: string | null) {
  return (text ?? "").trim().replace(/\.+$/, "");
}

/** A turn: its folded steps, its reply, and how it ended when it did not answer. Retry is on the last turn only. */
function TurnRows({ turn, chat, engine, last, onRetry, retrying }: {
  turn: Turn; chat: AgentChat; engine: string; last: boolean; onRetry: () => void; retrying: boolean;
}) {
  const retry = last && !chat.archived ? <> <button type="button" className="link" disabled={retrying} onClick={onRetry}>
    <BusyLabel busy={retrying} label="Retry" working="Retrying…" /></button></> : null;
  const cut = turn.steps.some((step) => step.kind === "step" && step.verb === "cut");
  return <>
    <Worked turn={turn} engine={engine} />
    {turn.answer ? <Reply text={turn.answer} at={turn.ended} /> : null}
    {turn.state === "stopped" ? <SysLine at={turn.ended}>{cut ? "Stopped by you · the command it was running was cut off" : "Stopped by you"}</SysLine> : null}
    {turn.state === "failed" ? <p className="conversation-status" data-tone="danger" role={last ? "alert" : undefined}>
      <span className="dot" data-state="danger" aria-hidden />
      <span>Couldn’t answer: {reason(turn.error) || "the agent ended without a reply"}. Choose another model below, or{retry ?? " Retry"} after it resets.</span>
    </p> : null}
    {turn.state === "cut" ? <p className="conversation-status" data-tone="danger" role={last ? "alert" : undefined}>
      <span className="dot" data-state="danger" aria-hidden />
      <span>The reply was cut off: Altitude stopped while it ran.{retry}</span>
    </p> : null}
  </>;
}

const EVENTS: Record<string, (engine: string, event: ChatEvent) => string> = {
  started: (engine) => `Started · ${engine} · its own copy of main`,
  new_session: (engine) => `New session on ${engine} · it was given this conversation so far`,
  restarted: () => "Altitude restarted · the reply continued",
  task: (_, event) => event.moved ? "Task made by you · its changes moved to the task; a fresh copy of main" : "Task made by you",
  archived: () => "Archived by you",
  reopened: () => "Reopened · a fresh copy of main",
};

/** The conversation in order, with a day divider whenever the day changes. */
function Timeline({ project, chat, overview, tasks, onRemove, removing, onRetry, retrying }: {
  project: string; chat: AgentChat; overview: Overview | undefined; tasks: Map<string, TaskRow>; onRemove: (message: string) => void;
  removing: string | null; onRetry: () => void; retrying: boolean;
}) {
  const rows: ReactNode[] = [];
  let day = "";
  const lastTurn = [...chat.items].reverse().find((item) => item.kind === "turn");
  chat.items.forEach((item, i) => {
    const at = item.kind === "turn" ? item.started : item.at;
    const today = dayText(at);
    if (today && today !== day) {
      day = today;
      rows.push(<DayDivider key={`day-${i}`} label={today} />);
    }
    if (item.kind === "event") {
      const engine = chatEngine({ engine: item.engine ?? null, model: item.model ?? null }, overview);
      rows.push(<SysLine key={`event-${i}`} at={item.at}>{EVENTS[item.event]!(engine, item)}</SysLine>);
      if (item.slug) rows.push(<TaskCards key={`task-${item.slug}`} project={project} slugs={[item.slug]} tasks={tasks} />);
    } else if (item.kind === "you") {
      const images = item.images.length ? <MessageImages project={project} images={item.images} context={sentBy("You", item.at)} /> : null;
      rows.push(item.waiting ? <Bubble key={item.id} text={item.text} state="queued" images={images}>
        Waits for the reply ·{" "}
        <button type="button" className="link" disabled={removing === item.id} onClick={() => onRemove(item.id)}>
          <BusyLabel busy={removing === item.id} label="Remove" working="Removing…" /></button>
      </Bubble> : <Bubble key={item.id} text={item.text} at={item.at} images={images} />);
    } else {
      if (item.state === "starting" && chat.changes == null) rows.push(<SysLine key={`copy-${item.n}`}>Making its copy of main…</SysLine>);
      rows.push(<TurnRows key={`turn-${item.n}`} turn={item} chat={chat} last={item === lastTurn} onRetry={onRetry} retrying={retrying}
        engine={chatEngine({ engine: item.engine, model: item.engine_model ?? item.model }, overview)} />);
    }
  });
  return <>{rows}</>;
}

function changedFiles(count: number) {
  return `${count} changed file${count === 1 ? "" : "s"}`;
}

function started(value: string) {
  const then = when(value);
  return then == null ? "" : new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

/** ⋯: Rename, Make a task… and Archive, then what the chat runs on, when it started and what its copy holds. A bottom
 * sheet on the phone, a popover on desktop. */
function ChatMenu({ chat, overview, answering, onRename, onTask, onArchive }: {
  chat: AgentChat; overview: Overview | undefined; answering: boolean; onRename: () => void; onTask: () => void; onArchive: () => void;
}) {
  const { phone } = useViewport();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback((refocus = false) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  }, []);
  useEffect(() => {
    if (!open || phone) return;
    anchor.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
    const onPointer = (event: MouseEvent) => { if (!anchor.current?.contains(event.target as Node)) close(); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(true); };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open, phone, close]);
  const onMenuKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
    if (next === undefined || !items.length) return;
    event.preventDefault();
    items[(next + items.length) % items.length]!.focus();
  };
  const pick = (action: () => void) => { setOpen(false); action(); };
  const meta = [chatEngine(chat, overview), `started ${started(chat.created)}`,
    chat.changes == null ? "" : `its copy: ${chat.changes ? changedFiles(chat.changes) : "no changes"}`].filter(Boolean).join(" · ");
  const items = <div role="menu" className={phone ? "chat-menu" : "menu chat-menu"} aria-label="Chat actions" onKeyDown={onMenuKey}>
    <button type="button" role="menuitem" className="menu-item" data-autofocus onClick={() => pick(onRename)}>Rename</button>
    <button type="button" role="menuitem" className="menu-item" disabled={answering} onClick={() => pick(onTask)}>
      Make a task…{answering ? <span className="menu-item-meta">Available when the reply ends</span> : null}
    </button>
    <button type="button" role="menuitem" className="menu-item" disabled={answering} onClick={() => pick(onArchive)}>
      Archive{answering ? <span className="menu-item-meta">Available when the reply ends</span> : null}
    </button>
    <p className="chat-menu-meta text-meta text-muted">{meta}</p>
  </div>;
  return <div className="relative" ref={anchor}>
    <button ref={button} type="button" className="icon-btn" aria-label="Chat actions" aria-haspopup="menu" aria-expanded={open}
      onClick={() => open ? close() : setOpen(true)}>
      <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
        <circle cx="4" cy="10" r="1.6" fill="currentColor" /><circle cx="10" cy="10" r="1.6" fill="currentColor" /><circle cx="16" cy="10" r="1.6" fill="currentColor" />
      </svg>
    </button>
    {open ? phone ? <Overlay label="Chat actions" side="bottom" onClose={() => close(true)}>
      <div className="chat-sheet"><div className="repair-sheet-grab" aria-hidden />{items}</div>
    </Overlay> : items : null}
  </div>;
}

/** The name, edited in place of the title: Enter or Save saves, Escape or Cancel restores. */
function Rename({ chat, project, onDone }: { chat: AgentChat; project: string; onDone: () => void }) {
  const [name, setName] = useState(chat.name);
  const rename = useAgentChatAction(project, (value: string) => agentChatRequest(project, `/${chat.id}/rename`, { name: value }, newRequest()));
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => { field.current?.select(); }, []);
  const trimmed = name.trim();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!trimmed || trimmed.length > 80) return;
    if (trimmed === chat.name) { onDone(); return; }
    rename.mutate(trimmed, { onSuccess: onDone });
  };
  return <form className="chat-rename" onSubmit={submit}>
    <input ref={field} className="chat-rename-field" aria-label="Chat name" value={name} maxLength={80} disabled={rename.isPending}
      onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onDone(); } }} />
    <button type="submit" className="btn btn-primary" disabled={!trimmed || rename.isPending}><BusyLabel busy={rename.isPending} label="Save" working="Saving…" /></button>
    <button type="button" className="btn btn-ghost" disabled={rename.isPending} onClick={onDone}>Cancel</button>
    {rename.isError ? <p className="text-meta text-danger chat-rename-error" role="alert">{rename.error.message}</p> : null}
  </form>;
}

/** Make a task from the chat: its title (the chat's name to start) and what it should deliver. Its changes move to the
 * task and the chat goes on in a fresh copy of main. One request identity per sheet makes a double tap, or a retry
 * after a lost reply, make one task. */
function TaskSheet({ chat, project, onClose }: { chat: AgentChat; project: string; onClose: () => void }) {
  const { phone } = useViewport();
  const client = useQueryClient();
  const [title, setTitle] = useState(chat.name);
  const [text, setText] = useState("");
  const [request] = useState(newRequest);
  const field = useRef<HTMLTextAreaElement>(null);
  const make = useAgentChatAction(project, (input: { title: string; text: string }) => agentChatRequest(project, `/${chat.id}/task`, input, request));
  useLayoutEffect(() => {
    const node = field.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [text]);
  const ready = Boolean(title.trim() && text.trim()) && !make.isPending;
  const submit = () => make.mutate({ title: title.trim(), text: text.trim() }, {
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["project", project] }); onClose(); },
  });
  const files = chat.changes ? `${chat.changes} file${chat.changes === 1 ? "" : "s"}` : "";
  return <Overlay label="Make a task from this chat" side={phone ? "bottom" : "center"} onClose={() => { if (!make.isPending) onClose(); }}>
    <form className="repair-sheet chat-task-sheet" onSubmit={(event) => { event.preventDefault(); if (ready) submit(); }}>
      {phone ? <span className="repair-sheet-grab" aria-hidden /> : null}
      <h2>Make a task from this chat</h2>
      <label className="repair-sheet-label" htmlFor="chat-task-title">Title</label>
      <input id="chat-task-title" className="chat-task-title" value={title} maxLength={120} disabled={make.isPending}
        onChange={(event) => setTitle(event.target.value)} />
      <label className="repair-sheet-label" htmlFor="chat-task-text">What should it deliver?</label>
      <textarea id="chat-task-text" ref={field} rows={2} value={text} data-autofocus maxLength={20000} disabled={make.isPending}
        placeholder="For example: ship the faster export with a test" onChange={(event) => setText(event.target.value)} />
      <p className="text-meta text-muted">{files ? `Its changes (${files}) move to the task, with this conversation as its context. The chat goes on in a fresh copy of main.`
        : "It starts from main, with this conversation as its context."}</p>
      <div className="repair-sheet-buttons">
        <button type="submit" className="btn btn-primary" disabled={!ready}><BusyLabel busy={make.isPending} label="Make task" working="Making…" /></button>
        <button type="button" className="btn btn-ghost" disabled={make.isPending} onClick={onClose}>Cancel</button>
      </div>
      {make.isError ? <p className="text-meta text-danger" role="alert">{make.error.message}</p> : null}
    </form>
  </Overlay>;
}

/** Archive asks only when the copy has changes no task has, offering Make a task first; otherwise it archives at once. */
function ArchiveSheet({ chat, pending, error, onArchive, onTask, onClose }: {
  chat: AgentChat; pending: boolean; error: string; onArchive: () => void; onTask: () => void; onClose: () => void;
}) {
  const { phone } = useViewport();
  return <Overlay label="Archive this chat?" side={phone ? "bottom" : "center"} onClose={() => { if (!pending) onClose(); }}>
    <div className="repair-sheet">
      {phone ? <div className="repair-sheet-grab" aria-hidden /> : null}
      <h2>Archive this chat?</h2>
      <p>Its copy of the code has changes no task has: <b>{changedFiles(chat.changes ?? 0)}</b>. Archiving deletes them. The conversation
        stays, and Reopen starts a fresh copy from main.</p>
      {error ? <p className="text-meta text-danger" role="alert">{error}</p> : null}
      <div className="repair-sheet-buttons chat-sheet-stack">
        <button type="button" className="btn" disabled={pending} onClick={onTask}>Make a task first</button>
        <button type="button" className="btn btn-danger" disabled={pending} onClick={onArchive}>
          <BusyLabel busy={pending} label="Archive and delete changes" working="Archiving…" /></button>
        <button type="button" className="btn btn-ghost" data-autofocus disabled={pending} onClick={onClose}>Cancel</button>
      </div>
    </div>
  </Overlay>;
}

function DeleteDialog({ pending, error, onDelete, onClose }: { pending: boolean; error: string; onDelete: () => void; onClose: () => void }) {
  const { phone } = useViewport();
  return <Overlay label="Delete this chat?" side={phone ? "bottom" : "center"} onClose={() => { if (!pending) onClose(); }}>
    <div className="repair-sheet">
      {phone ? <div className="repair-sheet-grab" aria-hidden /> : null}
      <h2>Delete this chat?</h2>
      <p>Its conversation and session are removed for good.</p>
      {error ? <p className="text-meta text-danger" role="alert">{error}</p> : null}
      <div className="repair-sheet-buttons">
        <button type="button" className="btn btn-danger" disabled={pending} onClick={onDelete}>
          <BusyLabel busy={pending} label="Delete chat" working="Deleting…" /></button>
        <button type="button" className="btn btn-ghost" data-autofocus disabled={pending} onClick={onClose}>Cancel</button>
      </div>
    </div>
  </Overlay>;
}

/** An archived chat reads only: when it was archived, Reopen and Delete…. */
function ArchivedDock({ chat, project }: { chat: AgentChat; project: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const reopen = useAgentChatAction(project, () => agentChatRequest(project, `/${chat.id}/reopen`, {}, newRequest()));
  const [asking, setAsking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const remove = async () => {
    setDeleting(true);
    setError("");
    try {
      await deleteAgentChat(project, chat.id, newRequest());
      client.removeQueries({ queryKey: ["agent-chat", project, chat.id] });
      void client.invalidateQueries({ queryKey: ["agent-chats", project] });
      void navigate(`/projects/${project}?tab=work`, { replace: true });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      setDeleting(false);
    }
  };
  return <div className="convo-dock chat-archived">
    {reopen.isError ? <p className="text-meta text-danger" role="alert">{reopen.error.message}</p> : null}
    <div className="chat-archived-row">
      <span className="text-meta text-muted">Archived · {started(chat.archived ?? "")}</span>
      <button type="button" className="btn" disabled={reopen.isPending} onClick={() => reopen.mutate(undefined)}>
        <BusyLabel busy={reopen.isPending} label="Reopen" working="Reopening…" /></button>
      <button type="button" className="btn btn-ghost" disabled={reopen.isPending} onClick={() => { setError(""); setAsking(true); }}>Delete…</button>
    </div>
    {asking ? <DeleteDialog pending={deleting} error={error} onDelete={() => void remove()} onClose={() => setAsking(false)} /> : null}
  </div>;
}

/** Stop in the header: it ends the reply at once, and Escape stops it on desktop as on a task's page. */
function StopButton({ project, chat, escape, voiceOwnsEscape }: { project: string; chat: AgentChat; escape: boolean; voiceOwnsEscape: boolean }) {
  const stop = useAgentChatAction(project, () => agentChatRequest(project, `/${chat.id}/stop`, {}, newRequest()));
  const stopping = stop.isPending || chat.items.some((item) => item.kind === "turn" && !item.ended && item.stop);
  const keys = escape && !stopping;
  const mutate = stop.mutate;
  useEffect(() => {
    if (!keys || voiceOwnsEscape) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.key !== "Escape" || event.repeat || event.isComposing || event.defaultPrevented ||
          target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") ||
          document.querySelector('[role="dialog"], [role="menu"], [aria-haspopup][aria-expanded="true"], .overlay')) return;
      event.preventDefault();
      mutate(undefined);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [keys, voiceOwnsEscape, mutate]);
  return <>
    <button type="button" className="btn task-steering" disabled={stopping} onClick={() => stop.mutate(undefined)}
      aria-keyshortcuts={keys ? "Escape" : undefined}>
      <BusyLabel busy={stopping} label="Stop" working="Stopping…" />
      {keys ? <kbd aria-hidden>Esc</kbd> : null}
    </button>
    {stop.isError ? <span className="text-danger text-meta" role="alert">{stop.error.message}</span> : null}
  </>;
}

/** The conversation opens at its newest line and follows new lines while you are at the bottom; scrolled up to read,
 * it stays where you are. */
function useFollowBottom(content: unknown) {
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useLayoutEffect(() => {
    const node = scroller.current;
    if (node && following.current) node.scrollTop = node.scrollHeight;
  }, [content]);
  const onScroll = () => {
    const node = scroller.current;
    if (node) following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
  };
  return { scroller, onScroll };
}

const BACK_ICON = <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
  <path d="M12 4l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

export default function ChatRoute() {
  const params = useParams();
  const project = params.name ?? "";
  const id = params.id;
  const { phone } = useViewport();
  const navigate = useNavigate();
  const client = useQueryClient();
  const overview = useOverview(false);
  const query = useAgentChat(project, id);
  const chat = query.data;
  const follow = useFollowBottom(chat);
  const [draft, setDraft] = useState("");
  const [newChoice, setNewChoice] = useState<Choice>(null);
  const [renaming, setRenaming] = useState(false);
  const [archiveAsk, setArchiveAsk] = useState(false);
  const [taskAsk, setTaskAsk] = useState(false);
  const [voiceOwnsEscape, setVoiceOwnsEscape] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const home = `/projects/${project}?tab=work`;
  const back = () => navigate(home);

  const send = useAgentChatAction(project, (input: { text: string; images?: unknown; image_ids?: unknown; request: string }) =>
    agentChatRequest(project, id ? `/${id}/message` : "", { text: input.text, images: input.images, image_ids: input.image_ids,
      ...(id ? {} : { choice: newChoice }) }, input.request));
  const model = useAgentChatAction(project, (choice: Choice) => agentChatRequest(project, `/${id}/model`, { choice }, newRequest()));
  const retry = useAgentChatAction(project, () => agentChatRequest(project, `/${id}/retry`, {}, newRequest()));
  const remove = useAgentChatAction(project, (message: string) => agentChatRequest(project, `/${id}/remove`, { message }, newRequest()));
  const archive = useAgentChatAction(project, (discard: boolean) => agentChatRequest(project, `/${id}/archive`, { discard }, newRequest()));

  // The task cards' rows, read only once a task was made from the chat.
  const made = Boolean(chat?.items.some((item) => item.kind === "event" && item.slug));
  const projectView = useProject(project, made);
  const tasks = useMemo(() => taskMap(projectView.data), [projectView.data]);

  const answering = chat?.state === "answering" || chat?.state === "starting";
  const archived = Boolean(chat?.archived);
  const engine = chat ? chatEngine(chat, overview.data) : "";
  const title = id ? chat?.name ?? "Agent chat" : "New chat";
  const doArchive = (discard: boolean) => archive.mutate(discard, { onSuccess: () => setArchiveAsk(false) });
  const onArchive = () => { if (chat?.changes) setArchiveAsk(true); else doArchive(false); };
  const state = chat?.state === "starting" ? "Starting" : chat?.state === "answering" ? "Answering"
    : chat?.state === "failed" ? "Couldn’t answer" : "";
  const action = chat && !archived ? answering ? <>
    <StopButton project={project} chat={chat} escape={!phone} voiceOwnsEscape={voiceOwnsEscape} />
    {phone ? null : <ChatMenu chat={chat} overview={overview.data} answering onRename={() => setRenaming(true)} onTask={() => setTaskAsk(true)}
      onArchive={onArchive} />}
  </> : <ChatMenu chat={chat} overview={overview.data} answering={false} onRename={() => setRenaming(true)} onTask={() => setTaskAsk(true)}
    onArchive={onArchive} /> : null;

  let body: ReactNode;
  if (!id) body = <p className="chat-empty text-muted">Works in its own copy of {project}’s code, made from main when you send.</p>;
  else if (query.isPending) body = <div className="convo-skeleton" aria-label="Loading"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>;
  else if (query.isError && !chat) {
    body = query.error instanceof ApiError && query.error.status === 404 ? <p className="text-meta text-muted">{query.error.message}</p>
      : <p role="alert" className="text-danger">Couldn’t load this chat. <button type="button" className="link" onClick={() => void query.refetch()}>Retry</button></p>;
  } else if (chat) {
    body = <Timeline project={project} chat={chat} overview={overview.data} tasks={tasks} removing={removing} retrying={retry.isPending}
      onRetry={() => retry.mutate(undefined)}
      onRemove={(message) => { setRemoving(message); remove.mutate(message, { onSettled: () => setRemoving(null) }); }} />;
  }
  const failure = [send, model, retry, remove, archive].find((mutation) => mutation.isError)?.error;
  const pill = <ChoiceButton label="Model" placement="up" value={id ? chat?.choice ?? null : newChoice} disabled={Boolean(id) && (!chat || model.isPending)}
    onChange={(choice) => id ? model.mutate(choice) : setNewChoice(choice)} />;
  const foot = id && !chat ? null : archived && chat ? <ArchivedDock chat={chat} project={project} /> : <div className="convo-dock">
    {failure && !archiveAsk ? <p className="text-danger text-meta" role="alert">{failure.message}</p> : null}
    <Composer conversation={`chat/${project}/${id ?? "new"}`} value={draft} onChange={setDraft} placeholder="Message the agent" ariaLabel="Message the agent"
      imageScope={{ project, engine: chat?.engine ?? newChoice?.engine ?? null }} pill={pill} onEscapeOwnership={setVoiceOwnsEscape} autoFocus={!id && !phone}
      onSubmit={async (text, onAccepted, images, copy) => {
        const sent = await send.mutateAsync({ text, images: images?.images, image_ids: images?.image_ids, request: images?.request_id ?? copy ?? newRequest() });
        onAccepted();
        if (!id) {
          client.setQueryData(["agent-chat", project, sent.id], sent);
          void navigate(`/projects/${project}/chats/${sent.id}`, { replace: true });
        }
      }} />
  </div>;
  const conversation = <section className="convo" aria-label="Chat conversation">
    {query.isError && chat ? <p className="conversation-notice" role="status">Altitude is not answering · the reply continues and this page catches up when it is back</p> : null}
    <div className="convo-scroll" ref={follow.scroller} onScroll={follow.onScroll}><ProseScope project={project}><div className="convo-col">{body}</div></ProseScope></div>
    {foot}
  </section>;
  const dot = chat ? <span className="dot" data-state={chatDot(chat.state)} aria-hidden /> : null;
  const sheets = chat && taskAsk ? <TaskSheet chat={chat} project={project} onClose={() => setTaskAsk(false)} />
    : chat && archiveAsk ? <ArchiveSheet chat={chat} pending={archive.isPending} error={archive.error?.message ?? ""}
      onArchive={() => doArchive(true)} onTask={() => { setArchiveAsk(false); setTaskAsk(true); }} onClose={() => setArchiveAsk(false)} /> : null;
  const rename = chat && renaming ? <Rename chat={chat} project={project} onDone={() => setRenaming(false)} /> : null;

  if (phone) {
    return <>
      <header className="phone-header" data-task>
        {rename ? null : <button type="button" className="icon-btn" aria-label="Back" onClick={back}>{BACK_ICON}</button>}
        {rename ?? <h1 className="phone-title" aria-label={title} tabIndex={-1}>
          <span className="phone-heading">
            <span className="truncate">{title}</span>
            {chat && (state || engine) ? <span className="phone-status" aria-live="polite"><span className="task-state-line" role="status">
              {dot}<span data-tone={chat.state === "failed" ? "danger" : undefined}>{[state, engine].filter(Boolean).join(" · ")}</span>
            </span></span> : null}
          </span>
        </h1>}
        {rename ? null : action ?? <span className="icon-btn invisible" aria-hidden />}
      </header>
      <div className="task-page" data-phone>{conversation}</div>
      {sheets}
    </>;
  }
  return <div className="task-page">
    <header className="task-header">
      <div className="task-crumb-row">
        <button type="button" className="task-crumb" onClick={back}>‹ Work</button>
        {rename ?? <h1 className="task-title" tabIndex={-1}>{dot}<span>{title}</span></h1>}
        <div className="task-actions">{rename ? null : action}</div>
      </div>
      {chat && !archived ? <div className="task-chips">
        {answering ? <span className="chip" data-tone="accent">{state}</span> : null}
        {engine ? <span className="chip">{engine}</span> : null}
      </div> : null}
    </header>
    <div className="task-body"><div className="task-main">{conversation}</div></div>
    {sheets}
  </div>;
}
