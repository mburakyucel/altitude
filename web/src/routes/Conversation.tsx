import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { streamChat, useChatDequeue } from "../data/api";
import type { ChatMessage, ChatView, EngineReadout, ProjectView, TaskRow } from "../data/api";
import { ProseRepository } from "../components/Prose";
import { when } from "../data/observed";
import { Bubble, DayDivider, Reply, Typing, dayLabel } from "../components/Bubbles";
import Composer from "../components/Composer";
import { L3EngineSelect } from "../components/L3EngineSelect";
import { useViewport } from "../shell/breakpoints";
import { SystemGroup, SystemLine, subjectOf } from "../components/SystemLine";
import { TaskCard } from "../components/TaskCard";
import type { SystemTurn } from "../components/SystemLine";

/*
 * The project conversation (SPEC.md §3.3, §3.4, §4.1, §4.2): one column, newest last, operator bubbles
 * right, L3 prose left, every non-chat turn a system line, runs of them one line. GET /api/chat is the
 * authority for what ran, what runs, and what waits; the page keeps only the turn it is streaming.
 */

interface Turn {
  id: string;
  trigger: string;
  at: string | null;
  user: ChatMessage | null;
  assistant: ChatMessage | null;
  error: ChatMessage | null;
  fyi: boolean;
}

/** Group the chat rows into turns: by turn id, and for rows written before ids by adjacency. */
export function turnsOf(history: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  const byId = new Map<string, Turn>();
  let open: Turn | null = null;
  const fresh = (id: string, row: ChatMessage, fyi = false): Turn => {
    const turn: Turn = { id, trigger: row.trigger || (fyi ? "fyi" : "chat"), at: row.at ?? null, user: null, assistant: null, error: null, fyi };
    byId.set(id, turn);
    turns.push(turn);
    return turn;
  };
  history.forEach((row, index) => {
    const id = row.turn_id || "";
    if (row.role === "system") {
      const turn = fresh(id || `row-${index}`, row, true);
      turn.user = row;
      open = null;
      return;
    }
    if (row.role === "user") {
      const turn = (id && byId.get(id)) || fresh(id || `row-${index}`, row);
      turn.user = turn.user ? { ...turn.user, text: `${turn.user.text}\n\n${row.text}` } : row;
      open = turn;
      return;
    }
    let turn = id ? byId.get(id) : undefined;
    if (!turn) turn = open ?? fresh(id || `row-${index}`, row);
    if (row.role === "assistant") turn.assistant = row;
    else turn.error = row;
    open = null;
  });
  return turns;
}

function systemTurn(turn: Turn, project: string, activeId: string | null): SystemTurn {
  const row = turn.user ?? turn.assistant ?? turn.error;
  return {
    id: turn.id,
    trigger: turn.trigger,
    at: turn.at,
    prompt: turn.user?.text ?? "",
    reply: turn.assistant?.text ?? null,
    error: turn.error?.text ?? null,
    inProgress: !turn.assistant && !turn.error && turn.id === activeId,
    slug: row ? subjectOf(row, project) : null,
    fyi: turn.fyi,
  };
}

type Item =
  | { kind: "chat"; turn: Turn; inProgress: boolean }
  | { kind: "system"; turn: SystemTurn }
  | { kind: "group"; turns: SystemTurn[]; at: string | null };

/** Chat turns stay single; consecutive completed system turns fold into one group (SPEC.md §4.1). */
export function itemsOf(turns: Turn[], project: string, activeId: string | null): Item[] {
  const items: Item[] = [];
  let run: SystemTurn[] = [];
  const flush = () => {
    const [first] = run;
    if (!first) return;
    if (run.length === 1) items.push({ kind: "system", turn: first });
    else items.push({ kind: "group", turns: run, at: first.at });
    run = [];
  };
  for (const turn of turns) {
    if (turn.trigger === "chat") {
      flush();
      items.push({ kind: "chat", turn, inProgress: !turn.assistant && !turn.error && turn.id === activeId });
      continue;
    }
    const system = systemTurn(turn, project, activeId);
    if (system.inProgress) {
      flush();
      items.push({ kind: "system", turn: system });
    } else run.push(system);
  }
  flush();
  return items;
}

function itemAt(item: Item): string | null {
  return item.kind === "group" ? item.at : item.turn.at;
}

/** The turn the page is streaming: its bubble first, then the reply as it arrives (SPEC.md §4.2). */
interface Local {
  text: string;
  reply: string;
  accepted: boolean;
  turnId: string | null;
  error: string | null;
  done: boolean;
  /** When the stream finished, so a poll from after it can retire the local copy. */
  finishedAt: number | null;
}

/** A task the turn created, under the reply: the link slice 3 grows into the §3.5 card. */
/** The task cards under a reply that created tasks (SPEC.md §3.5, §5.2 note 4). */
function TurnTasks({ project, slugs, titles }: { project: string; slugs: string[]; titles: Map<string, TaskRow> }) {
  return (
    <div className="turn-tasks">
      {slugs.map((slug) => (
        <TaskCard key={slug} project={project} task={titles.get(slug) ?? { slug }} />
      ))}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="convo-skeleton" aria-label="Loading">
      <div className="skeleton" />
      <div className="skeleton" />
      <div className="skeleton" />
    </div>
  );
}

export default function Conversation({
  name,
  chat,
  project,
  engines,
}: {
  name: string;
  chat: UseQueryResult<ChatView>;
  project: UseQueryResult<ProjectView>;
  engines: EngineReadout[];
}) {
  const queryClient = useQueryClient();
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const viewportHeight = useRef(0);
  const [draft, setDraft] = useState("");
  const [local, setLocal] = useState<Local | null>(null);
  const dequeue = useChatDequeue(name);
  const { phone } = useViewport();

  const view = chat.data;
  const activeId = view?.active?.id ?? null;
  const tasks = useMemo(() => {
    const map = new Map<string, TaskRow>();
    for (const row of [...(project.data?.tasks ?? []), ...(project.data?.archive ?? [])]) map.set(row.slug, row);
    return map;
  }, [project.data]);
  const titles = useMemo(() => {
    const map = new Map<string, string>();
    tasks.forEach((row, slug) => {
      if (row.title) map.set(slug, row.title);
    });
    return map;
  }, [tasks]);

  const turns = useMemo(() => turnsOf(view?.history ?? []), [view?.history]);
  const items = useMemo(() => itemsOf(turns, name, activeId), [turns, name, activeId]);

  // The server's rows for the streamed turn replace the local copy once a poll shows them.
  useEffect(() => {
    if (!local?.done) return;
    const stored = local.turnId ? turns.some((turn) => turn.id === local.turnId) : false;
    const refreshed = local.finishedAt != null && chat.dataUpdatedAt > local.finishedAt;
    if (stored || (refreshed && !local.turnId)) setLocal(null);
  }, [local, turns, chat.dataUpdatedAt]);

  // Stay at the bottom while the operator is there: new rows, a streamed reply growing, a card or
  // group opening, a title arriving for a line, the fonts landing. Scrolling up releases the follow.
  useEffect(() => {
    const node = scroller.current;
    const column = node?.firstElementChild;
    if (!node || !column || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (following.current) node.scrollTop = node.scrollHeight;
      viewportHeight.current = node.clientHeight;
    });
    observer.observe(column);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const send = useCallback(
    async (text: string) => {
      following.current = true;
      setLocal({ text, reply: "", accepted: false, turnId: null, error: null, done: false, finishedAt: null });
      let result;
      try {
        result = await streamChat(name, text, {
          onAccepted: () => setLocal((cur) => (cur ? { ...cur, accepted: true } : cur)),
          onTurn: (turn) => setLocal((cur) => (cur ? { ...cur, turnId: turn.id } : cur)),
          onText: (chunk) => setLocal((cur) => (cur ? { ...cur, reply: cur.reply + chunk } : cur)),
        });
      } catch (error) {
        // Refused: the bubble leaves and the composer brings the draft back with "Not sent. Retry."
        setLocal(null);
        throw error;
      }
      if (result.queued) {
        const queued = result.queued;
        setLocal(null);
        queryClient.setQueryData<ChatView>(["chat", name], (cached) =>
          cached ? { ...cached, queued: [...(cached.queued ?? []).filter((q) => q.id !== queued.id), queued] } : cached,
        );
      } else {
        setLocal((cur) =>
          cur ? { ...cur, done: true, error: result.error ?? null, turnId: cur.turnId ?? result.turn_id ?? null, finishedAt: Date.now() } : cur,
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["chat", name] });
      void queryClient.invalidateQueries({ queryKey: ["project", name] });
    },
    [name, queryClient],
  );

  const neverStarted = project.isSuccess && !project.data.l3?.session_id && view && view.history.length === 0 && !view.active;
  const queued = view?.queued ?? [];
  const busy = Boolean(view?.busy || view?.active || (local && !local.done));
  const empty = Boolean(view && view.history.length === 0 && !view.active && !local && queued.length === 0);

  const rows: ReactNode[] = [];
  let lastDay = "";
  const divide = (at: string | null | undefined) => {
    const time = when(at);
    const day = time != null ? dayLabel(time) : "";
    if (day && day !== lastDay) {
      rows.push(<DayDivider key={`day-${day}`} label={day} />);
      lastDay = day;
    }
  };
  for (const item of items) {
    if (item.kind === "chat" && local?.turnId && item.turn.id === local.turnId) continue;
    divide(itemAt(item));
    if (item.kind === "group") {
      rows.push(<SystemGroup key={item.turns[0]?.id ?? item.at ?? "group"} turns={item.turns} project={name} titles={titles} />);
    } else if (item.kind === "system") {
      rows.push(<SystemLine key={item.turn.id} turn={item.turn} project={name} titles={titles} />);
    } else {
      const { turn } = item;
      rows.push(
        <div key={turn.id} className="turn" data-turn={turn.id}>
          {turn.user ? <Bubble text={turn.user.text} at={turn.user.at} /> : null}
          {turn.assistant ? (
            <Reply text={turn.assistant.text} at={turn.assistant.at} role="assistant">
              {turn.assistant.tasks?.length ? <TurnTasks project={name} slugs={turn.assistant.tasks} titles={tasks} /> : null}
            </Reply>
          ) : turn.error ? (
            <p className="turn-failed text-muted">
              L3 could not answer this turn.{" "}
              {turn.user ? (
                <button type="button" className="link" onClick={() => void send(turn.user!.text)}>
                  Retry
                </button>
              ) : null}
            </p>
          ) : item.inProgress ? (
            <Typing />
          ) : null}
        </div>,
      );
    }
  }
  if (view?.active && !turns.some((turn) => turn.id === view.active!.id) && view.active.trigger !== "chat" && !local) {
    // The turn started before its rows reached the log: the line says what L3 is handling meanwhile.
    divide(view.active.started_at);
    rows.push(
      <SystemLine
        key={view.active.id}
        turn={{ id: view.active.id, trigger: view.active.trigger, at: view.active.started_at, prompt: "", reply: null, error: null, inProgress: true, slug: null, fyi: false }}
        project={name}
        titles={titles}
      />,
    );
  }
  if (local) {
    divide(new Date().toISOString());
    rows.push(
      <div key="local" className="turn" data-local>
        <Bubble text={local.text} at={new Date().toISOString()} pending={!local.accepted} />
        {local.reply ? (
          <Reply text={local.reply} role="assistant" />
        ) : local.done && local.error ? (
          <p className="turn-failed text-muted">
            L3 could not answer this turn.{" "}
            <button type="button" className="link" onClick={() => void send(local.text)}>
              Retry
            </button>
          </p>
        ) : local.accepted && !local.done ? (
          <Typing />
        ) : null}
      </div>,
    );
  }

  return (
    <ProseRepository value={project.data?.repository}>
    <section className="convo" aria-label="Conversation">
      <div
        className="convo-scroll"
        ref={scroller}
        onScroll={(event) => {
          const node = event.currentTarget;
          // A viewport resize can dispatch scroll before ResizeObserver restores bottom following.
          if (node.clientHeight !== viewportHeight.current) return;
          following.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
        }}
      >
        <div className="convo-col">
          {chat.isPending ? <Skeleton /> : null}
          {chat.isError ? (
            <p className="convo-error text-danger" role="alert">
              Could not load the conversation.{" "}
              <button type="button" className="link" onClick={() => void chat.refetch()}>
                Retry
              </button>
            </p>
          ) : null}
          {empty ? (
            <p className="convo-empty text-muted">
              {neverStarted ? "L3 has not started. Start L3 to begin the conversation." : "Say what you want done. L3 answers or creates one task."}
            </p>
          ) : null}
          {rows}
          {queued.length > 0 ? (
            <ul className="queued" aria-label="Queued messages">
              {queued.map((row, index) => (
                <li key={row.id} className="queued-row">
                  <span className="queued-text"><span>{row.text}</span><span className="queued-status text-muted">{index === 0 ? "Queued · runs next" : `Queued · ${index + 1} in line`}</span></span>
                  {!row.trigger || row.trigger === "chat" ? (
                    <button type="button" className="link" onClick={() => dequeue.mutate(row.id)}>
                      Remove
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
      <div className="convo-dock">
        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={send}
          placeholder={`Message L3 about ${name}`}
          ariaLabel={`Message L3 about ${name}`}
          busy={busy}
          pill={phone ? undefined : <L3EngineSelect name={name} engine={view?.engine ?? ""} engines={engines} />}
          hint="L3 answers or creates one task. Shift + Enter for a new line."
        />
      </div>
    </section>
    </ProseRepository>
  );
}
