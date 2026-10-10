import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError, imageSendRefused, sendCreateTask, sendImageChat, streamChat, useChatDequeue, useSendNow } from "../data/api";
import { SendNow, SendNowError } from "../components/SendNow";
import type { ChatMessage, ChatSent, ChatView, ProjectView, TaskRow } from "../data/api";
import { ProseScope } from "../components/Prose";
import { ProseTerminal } from "../components/CodeBlock";
import { requestCommand } from "../data/terminalCommand";
import { when } from "../data/observed";
import { Bubble, DayDivider, RemoveMessage, Reply, Typing, dayLabel } from "../components/Bubbles";
import Composer from "../components/Composer";
import { L3ModelButton } from "../components/Models";
import type { ImageSubmission } from "../components/ImageDraft";
import { MessageImages, PendingImages } from "../components/MessageImages";
import type { ImagePreview } from "../components/MessageImages";
import { SystemGroup, SystemLine, subjectOf } from "../components/SystemLine";
import { TaskCard } from "../components/TaskCard";
import { CreateTask, CreateTaskError } from "../components/CreateTask";
import type { CreateTaskState } from "../components/CreateTask";
import type { SystemTurn } from "../components/SystemLine";

/*
 * The project conversation (SPEC.md §3.3, §3.4, §4.1, §4.2): one column, newest last, operator bubbles
 * right, L3 prose left, non-chat turns as compact lines with routine runs grouped. GET /api/chat is the
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
      const suppliedTo = row.project_message?.supplied_turn_id;
      const receiving = suppliedTo ? byId.get(suppliedTo) : undefined;
      if (receiving) {
        turns.pop();
        turns.splice(turns.indexOf(receiving), 0, turn);
      }
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
    slug: row && !row.project_message ? subjectOf(row, project) : null,
    fyi: turn.fyi,
    headsUp: turn.fyi && (turn.trigger === "project-message-error" || (turn.trigger === "fyi" && turn.user?.heads_up === true)),
    projectMessage: row?.project_message,
  };
}

type Item =
  | { kind: "chat"; turn: Turn; inProgress: boolean }
  | { kind: "system"; turn: SystemTurn }
  | { kind: "group"; turns: SystemTurn[]; at: string | null };

/** Chat, selected L3 heads-ups and active turns split runs of routine system events (SPEC.md §4.1). */
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
    if (system.inProgress || system.headsUp || system.projectMessage) {
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
  request: symbol;
  text: string;
  reply: string;
  accepted: boolean;
  turnId: string | null;
  error: string | null;
  done: boolean;
  queueId?: string;
  images?: ImagePreview[];
  uncertain?: boolean;
  replay?: ImageSubmission;
  /** Exchanges this turn completed before a Send now message joined it, shown until the history has them. */
  delivery?: ChatMessage["delivery"];
  earlier?: { turnId: string | null; text: string; reply: string; delivery?: ChatMessage["delivery"] }[];
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
}: {
  name: string;
  chat: UseQueryResult<ChatView>;
  project: UseQueryResult<ProjectView>;
}) {
  const queryClient = useQueryClient();
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const viewportHeight = useRef(0);
  const [draft, updateDraft] = useState(() => queryClient.getQueryData<string>(["project-draft", name]) ?? "");
  const setDraft = useCallback((text: string) => {
    queryClient.setQueryDefaults(["project-draft", name], { gcTime: Infinity });
    queryClient.setQueryData(["project-draft", name], text);
    updateDraft(text);
  }, [name, queryClient]);
  const [local, setLocal] = useState<Local | null>(null);
  // A Create task press (SPEC.md §3.3): the reply being pressed while it saves, a reason it was not sent, the spoken receipt.
  const [press, setPress] = useState<string | null>(null);
  const [pressError, setPressError] = useState<{ turnId: string; message: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const dequeue = useChatDequeue(name);
  const sendNow = useSendNow(name);
  const navigate = useNavigate();
  const location = useLocation();
  // A `run` block in project chat opens the project folder's terminal with its command typed (SPEC.md §3.3).
  const runTarget = useMemo(() => ({ open: (command: string) => {
    const path = `/projects/${name}/terminal`;
    requestCommand(name, undefined, command);
    if (location.pathname !== path) void navigate(path);
  } }), [name, location.pathname, navigate]);

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
    if (!local) return;
    const stored = turns.find((turn) => turn.id === local.turnId);
    const queued = local.queueId && view?.queued?.some((row) => row.id === local.queueId);
    const submitted = local.queueId && view?.history.some((row) => row.request_id === local.queueId);
    if (stored?.assistant || stored?.error || queued || submitted || (local.done && stored)) {
      setLocal((current) => current?.request === local.request ? null : current);
    }
  }, [local, turns, view]);

  // Only a successful server read can retire a receipt whose current queue/history state was
  // unknown. Engine selection and mutation rollback also write this cache; those are not reads.
  useEffect(() => queryClient.getQueryCache().subscribe((event) => {
    if (event.query.queryKey[0] !== "chat" || event.query.queryKey[1] !== name || event.type !== "updated"
      || event.action.type !== "success" || event.action.manual) return;
    setLocal((current) => current?.done && !current.turnId ? null : current);
  }), [name, queryClient]);

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
    async (text: string, onAccepted?: () => void, images?: ImageSubmission) => {
      following.current = true;
      setPressError(null);
      const request = Symbol();
      const update = (change: (current: Local) => Local | null) => setLocal((current) => current?.request === request ? change(current) : current);
      setLocal({ request, text, reply: "", accepted: false, turnId: null, error: null, done: false, images: images?.previews, replay: images?.image_ids ? images : undefined });
      let result: ChatSent;
      try {
        result = images ? await sendImageChat(name, text, { request_id: images.request_id, images: images.images, image_ids: images.image_ids }) : await streamChat(name, text, {
          onAccepted: () => { onAccepted?.(); update((cur) => ({ ...cur, accepted: true })); },
          onTurn: (turn, user, delivery) => {
            if (user === undefined) return update((cur) => ({ ...cur, turnId: turn.id }));
            void queryClient.invalidateQueries({ queryKey: ["chat", name] });
            update((cur) => ({ ...cur, turnId: turn.id, text: user, reply: "", images: undefined, delivery,
              earlier: [...(cur.earlier ?? []), { turnId: cur.turnId, text: cur.text, reply: cur.reply, delivery: cur.delivery }] }));
          },
          onText: (chunk) => update((cur) => ({ ...cur, reply: cur.reply + chunk })),
        });
      } catch (error) {
        // Refused: the bubble leaves and the composer brings the draft back with "Not sent. Retry."
        if (images && !imageSendRefused(error)) update((cur) => ({ ...cur, uncertain: true }));
        else if (images?.image_ids) update((cur) => ({ ...cur, done: true, error: error instanceof Error ? error.message : "Could not resend images." }));
        else update(() => null);
        void queryClient.invalidateQueries({ queryKey: ["chat", name] });
        throw error;
      }
      onAccepted?.();
      if (result.queued) {
        await queryClient.cancelQueries({ queryKey: ["chat", name] });
        // A receipt proves acceptance, not that the row still waits: it may already have run or
        // been removed. Only a fresh canonical snapshot can project its current state.
        update((cur) => ({ ...cur, accepted: true, done: true, replay: undefined, queueId: result.queued!.id }));
      } else if (images) update(() => null);
      else {
        update((cur) => ({ ...cur, done: true, error: result.error ?? null, turnId: cur.turnId ?? result.turn_id ?? null }));
      }
      void queryClient.invalidateQueries({ queryKey: ["chat", name], refetchType: "all" });
      void queryClient.invalidateQueries({ queryKey: ["project", name] });
    },
    [name, queryClient],
  );

  // Create task is the operator's next message under the hood: Altitude writes the instruction and the
  // queue or a new turn carries it, while the button under the reply shows where it stands.
  const pressCreateTask = useCallback(async (turnId: string) => {
    following.current = true;
    setPressError(null);
    setPress(turnId);
    const pressed = (data?: ChatView) => [...(data?.queued ?? []), ...(data?.history ?? [])].filter((row) => row.offer_turn === turnId).length;
    const before = pressed(queryClient.getQueryData<ChatView>(["chat", name]));
    const sent = () => setAnnouncement("Create task sent");
    try {
      await sendCreateTask(name, turnId);
      sent();
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        setPressError({ turnId, message: error.status === 409 ? error.message : `Not sent: ${error.message}` });
      } else {
        // The response was lost: the conversation itself says whether the message was saved.
        const read = queryClient.getQueryState(["chat", name])?.dataUpdatedAt ?? 0;
        await queryClient.refetchQueries({ queryKey: ["chat", name] }).catch(() => undefined);
        const state = queryClient.getQueryState<ChatView>(["chat", name]);
        if (state && state.dataUpdatedAt > read && pressed(state.data) > before) sent();
        else setPressError({ turnId, message: state && state.dataUpdatedAt > read ? "Not sent" : "Not confirmed" });
      }
    } finally {
      setPress(null);
      void queryClient.invalidateQueries({ queryKey: ["chat", name], refetchType: "all" });
      void queryClient.invalidateQueries({ queryKey: ["project", name] });
    }
  }, [name, queryClient]);

  const neverStarted = project.isSuccess && !project.data.l3?.session_id && view && view.history.length === 0 && !view.active;
  const queued = view?.queued ?? [];
  // A kept message is already in the conversation: its queued state shows under its own bubble, or in the list
  // when that bubble is older than the loaded history.
  const shown = new Set(turns.map((turn) => turn.id));
  const kept = new Map(queued.flatMap((row) => row.turn_id && shown.has(row.turn_id) ? [[row.turn_id, row] as const] : []));
  const waiting = queued.filter((row) => !row.turn_id || !shown.has(row.turn_id));
  const operatorQueue = queued.filter((row) => !row.project_message && (!row.trigger || row.trigger === "chat"));
  const actionable = operatorQueue.filter((row) => !row.offer_turn);
  const lastWaiting = actionable.filter((row) => row.send_now).at(-1) ?? actionable.at(-1);
  const sendingNow = sendNow.isPending || operatorQueue.some((row) => row.send_now);
  const busy = Boolean(view?.busy || view?.active || (local && !local.done));
  const empty = Boolean(view && view.history.length === 0 && !view.active && !local && queued.length === 0);
  // Only the latest reply offers Create task, while nothing of the operator's waits or runs after it.
  const lastChat = [...turns].reverse().find((turn) => turn.trigger === "chat");
  const offerTurn = lastChat?.assistant?.offer && !lastChat.assistant.tasks?.length && !local
    && !queued.some((row) => !row.project_message && (!row.trigger || row.trigger === "chat"))
    && !(view?.active?.trigger === "chat" && view.active.id !== lastChat.id) ? lastChat : null;
  // A press is a turn or queued row naming the reply it answers; the latest one says where Create task stands.
  // Once that reply has left the loaded history, the press reads as its message with the ordinary controls.
  const offering = new Set(turns.filter((turn) => turn.assistant?.offer).map((turn) => turn.id));
  const pressTurns = new Map(turns.filter((turn) => turn.user?.offer_turn).map((turn) => [turn.user!.offer_turn!, turn]));
  const queuedPresses = new Map(queued.filter((row) => row.offer_turn).map((row) => [row.offer_turn!, row]));
  const createTaskState = (turn: Turn): CreateTaskState | null => {
    if (press === turn.id) return "busy";
    if (queuedPresses.has(turn.id)) return "wait";
    const answer = pressTurns.get(turn.id);
    if (answer) return answer.assistant ? (answer.assistant.tasks?.length ? "done" : "sent") : answer.error ? "fail" : "busy";
    return offerTurn === turn ? "ready" : null;
  };
  const listed = waiting.filter((row) => !row.offer_turn || !offering.has(row.offer_turn));

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
  const stored = (id: string | null) => turns.some((turn) => turn.id === id && (turn.assistant || turn.error));
  const earlier = local?.earlier?.filter((segment) => !stored(segment.turnId)) ?? [];
  for (const item of items) {
    if (item.kind === "chat" && local?.turnId && item.turn.id === local.turnId) continue;
    if (item.kind === "chat" && earlier.some((segment) => segment.turnId === item.turn.id)) continue;
    divide(itemAt(item));
    if (item.kind === "group") {
      rows.push(<SystemGroup key={item.turns[0]?.id ?? item.at ?? "group"} turns={item.turns} project={name} titles={titles} />);
    } else if (item.kind === "system") {
      rows.push(<SystemLine key={item.turn.id} turn={item.turn} project={name} titles={titles} />);
    } else {
      const { turn } = item;
      const pressed = Boolean(turn.user?.offer_turn && offering.has(turn.user.offer_turn));
      const refused = pressError?.turnId === turn.id ? pressError.message : null;
      const offer = turn.assistant?.offer ? createTaskState(turn) : null;
      const pressRow = queuedPresses.get(turn.id);
      // A saved interrupted reply said nothing: the operator's next message follows directly (SPEC.md §4.2).
      const silent = turn.assistant?.interrupted === true && !turn.assistant.text.trim() && !turn.assistant.tasks?.length;
      const keptRow = kept.get(turn.id);
      rows.push(
        <div key={turn.id} className="turn" data-turn={turn.id} data-joined={silent || undefined}>
          {turn.user && !pressed ? <Bubble text={turn.user.text} at={turn.user.at} state={keptRow ? keptRow.sending ? "sending" : "queued" : turn.user.delivery?.state} images={<MessageImages project={name} images={turn.user.images} />} /> : null}
          {turn.assistant ? silent ? null : (
            <Reply text={turn.assistant.text} at={turn.assistant.at} role="assistant">
              {turn.assistant.tasks?.length ? <TurnTasks project={name} slugs={turn.assistant.tasks} titles={tasks} /> : null}
              {offer ? (
                <CreateTask title={turn.assistant.offer!} state={offer} error={refused} onPress={() => void pressCreateTask(turn.id)}
                  onRemove={pressRow && !pressRow.turn_id ? () => dequeue.mutate(pressRow.id) : undefined} removing={dequeue.isPending} />
              ) : refused ? <CreateTaskError message={refused} /> : null}
            </Reply>
          ) : turn.error && !pressed ? (
            <p className="turn-failed text-muted">
              L3 could not answer this turn.{" "}
              {turn.user?.offer_turn ? (
                <button type="button" className="link" disabled={Boolean(press)} onClick={() => void pressCreateTask(turn.user!.offer_turn!)}>Retry</button>
              ) : turn.user ? (
                <button type="button" className="link" disabled={Boolean(local && !local.done)} onClick={() => void send(turn.user!.text, undefined, turn.user!.images?.length ? { request_id: crypto.randomUUID(), image_ids: turn.user!.images.map((image) => image.id), previews: [] } : undefined).catch(() => undefined)}>
                  Retry
                </button>
              ) : null}
              {pressError && pressError.turnId === turn.user?.offer_turn ? <> <CreateTaskError message={pressError.message} /></> : null}
            </p>
          ) : !keptRow && item.inProgress ? (
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
        turn={{ id: view.active.id, trigger: view.active.trigger, at: view.active.started_at, prompt: "", reply: null, error: null, inProgress: true, slug: null, fyi: false, headsUp: false }}
        project={name}
        titles={titles}
      />,
    );
  }
  if (local) {
    divide(new Date().toISOString());
    for (const segment of earlier) {
      rows.push(
        <div key={`local-${segment.turnId}`} className="turn" data-local>
          <Bubble text={segment.text} at={new Date().toISOString()} state={segment.delivery?.state} />
          {segment.reply ? <Reply text={segment.reply} role="assistant" /> : null}
        </div>,
      );
    }
    rows.push(
      <div key="local" className="turn" data-local>
        <Bubble text={local.text} at={new Date().toISOString()} state={!local.accepted ? "pending" : local.delivery?.state} images={<PendingImages images={local.images} />} />
        {local.replay ? <p className={`turn-failed ${local.error || local.uncertain ? "text-danger" : "text-muted"}`} role={local.error || local.uncertain ? "alert" : "status"}>
          {local.error ? `Not sent. ${local.error}` : local.uncertain ? "Could not confirm send." : "Sending images…"}{" "}
          {local.error || local.uncertain ? <button type="button" className="link" onClick={() => void send(local.text, undefined, local.error ? { ...local.replay!, request_id: crypto.randomUUID() } : local.replay).catch(() => undefined)}>Retry</button> : null}
        </p> : local.reply ? (
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

  const queueAction = lastWaiting ? <div className="queued-actions">
    <SendNow pending={sendingNow} disabled={chat.isError || chat.isPending || dequeue.isPending || sendNow.isPending}
      reason={view?.send_now_reason || operatorQueue.find((row) => row.send_now)?.send_now_reason}
      onClick={() => sendNow.mutate(lastWaiting.id)} />
  </div> : null;

  return (
    <ProseScope project={name} repository={project.data?.repository}>
    <ProseTerminal value={runTarget}>
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
          <p className="visually-hidden" role="status">{announcement}</p>
          {lastWaiting && !listed.some((row) => row.id === lastWaiting.id) ? queueAction : null}
          {listed.length > 0 ? (
            <ul className="queued" aria-label="Queued messages">
              {listed.map((row) => (
                <li key={row.id} className="queued-row">
                  {row.project_message ? (
                    <SystemLine project={name} titles={titles} turn={{ id: row.id, at: row.at ?? null,
                      trigger: "project-message", prompt: row.text, reply: null, error: null,
                      inProgress: false, slug: null, fyi: true, headsUp: false, projectMessage: row.project_message }} />
                  ) : !row.trigger || row.trigger === "chat" ? (
                    <Bubble text={row.text} at={row.at} state={row.sending ? "sending" : "queued"} images={<MessageImages project={name} images={row.images} />}
                      side={row.sending || row.turn_id ? undefined : <RemoveMessage message={row.text} removing={dequeue.isPending && dequeue.variables === row.id}
                        disabled={chat.isError || chat.isPending || dequeue.isPending || sendNow.isPending} onClick={() => dequeue.mutate(row.id)} />} />
                  ) : <p className="queued-text text-meta text-muted">{row.text}</p>}
                  {row.id === lastWaiting?.id ? queueAction : null}
                </li>
              ))}
            </ul>
          ) : null}
          {sendNow.isError && operatorQueue.some((row) => row.id === sendNow.variables) ? <SendNowError error={sendNow.error} /> : null}
          <p className="sr-only" role="status">{dequeue.isSuccess ? "Message removed" : ""}</p>
        </div>
      </div>
      <div className="convo-dock">
        <Composer
          conversation={`project/${name}`}
          value={draft}
          onChange={setDraft}
          onSubmit={send}
          imageScope={{ project: name, engine: view?.engine }}
          disabled={Boolean(local?.replay && !local.done)}
          placeholder={`Message L3 about ${name}`}
          ariaLabel={`Message L3 about ${name}`}
          busy={busy}
          pill={<L3ModelButton project={name} />}
          hint="L3 answers or creates one task. Shift + Enter for a new line."
        />
      </div>
    </section>
    </ProseTerminal>
    </ProseScope>
  );
}
