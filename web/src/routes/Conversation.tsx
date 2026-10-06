import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { imageSendRefused, sendImageChat, streamChat, useChatDequeue, useSendNow } from "../data/api";
import { SendNow } from "../components/SendNow";
import type { ChatMessage, ChatSent, ChatView, EngineReadout, ProjectView, TaskRow } from "../data/api";
import { ProseScope } from "../components/Prose";
import { ProseTerminal } from "../components/CodeBlock";
import { requestCommand } from "../data/terminalCommand";
import { when } from "../data/observed";
import { Bubble, DayDivider, Reply, Typing, dayLabel } from "../components/Bubbles";
import Composer from "../components/Composer";
import { L3EngineSelect } from "../components/L3EngineSelect";
import { useViewport } from "../shell/breakpoints";
import type { ImageSubmission } from "../components/ImageDraft";
import { MessageImages, PendingImages } from "../components/MessageImages";
import type { ImagePreview } from "../components/MessageImages";
import { SystemGroup, SystemLine, subjectOf } from "../components/SystemLine";
import { TaskCard } from "../components/TaskCard";
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
    headsUp: turn.fyi && turn.trigger === "fyi" && turn.user?.heads_up === true,
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
    if (system.inProgress || system.headsUp) {
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
  const [draft, updateDraft] = useState(() => queryClient.getQueryData<string>(["project-draft", name]) ?? "");
  const setDraft = useCallback((text: string) => {
    queryClient.setQueryDefaults(["project-draft", name], { gcTime: Infinity });
    queryClient.setQueryData(["project-draft", name], text);
    updateDraft(text);
  }, [name, queryClient]);
  const [local, setLocal] = useState<Local | null>(null);
  const dequeue = useChatDequeue(name);
  const sendNow = useSendNow(name);
  const { phone } = useViewport();
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
      const request = Symbol();
      const update = (change: (current: Local) => Local | null) => setLocal((current) => current?.request === request ? change(current) : current);
      setLocal({ request, text, reply: "", accepted: false, turnId: null, error: null, done: false, images: images?.previews, replay: images?.image_ids ? images : undefined });
      let result: ChatSent;
      try {
        result = images ? await sendImageChat(name, text, { request_id: images.request_id, images: images.images, image_ids: images.image_ids }) : await streamChat(name, text, {
          onAccepted: () => { onAccepted?.(); update((cur) => ({ ...cur, accepted: true })); },
          onTurn: (turn) => update((cur) => ({ ...cur, turnId: turn.id })),
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
          {turn.user ? <Bubble text={turn.user.text} at={turn.user.at} images={<MessageImages project={name} images={turn.user.images} />} /> : null}
          {turn.assistant ? (
            <Reply text={turn.assistant.text} at={turn.assistant.at} role="assistant">
              {turn.assistant.tasks?.length ? <TurnTasks project={name} slugs={turn.assistant.tasks} titles={tasks} /> : null}
            </Reply>
          ) : turn.error ? (
            <p className="turn-failed text-muted">
              L3 could not answer this turn.{" "}
              {turn.user ? (
                <button type="button" className="link" disabled={Boolean(local && !local.done)} onClick={() => void send(turn.user!.text, undefined, turn.user!.images?.length ? { request_id: crypto.randomUUID(), image_ids: turn.user!.images.map((image) => image.id), previews: [] } : undefined).catch(() => undefined)}>
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
        turn={{ id: view.active.id, trigger: view.active.trigger, at: view.active.started_at, prompt: "", reply: null, error: null, inProgress: true, slug: null, fyi: false, headsUp: false }}
        project={name}
        titles={titles}
      />,
    );
  }
  if (local) {
    divide(new Date().toISOString());
    rows.push(
      <div key="local" className="turn" data-local>
        <Bubble text={local.text} at={new Date().toISOString()} pending={!local.accepted} images={<PendingImages images={local.images} />} />
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
          {queued.length > 0 ? (
            <ul className="queued" aria-label="Queued messages">
              {queued.map((row, index) => (
                <li key={row.id} className="queued-row">
                  <div className="queued-text"><span>{row.text}</span><MessageImages project={name} images={row.images} /><span className="queued-status text-muted">{row.send_now ? "Sending now" : index === 0 ? "Queued · runs next" : `Queued · ${index + 1} in line`}</span></div>
                  {!row.trigger || row.trigger === "chat" ? (
                    <div className="queued-actions">
                    <SendNow visible pending={Boolean(row.send_now || (sendNow.isPending && sendNow.variables === row.id))}
                      disabled={chat.isError || chat.isPending || dequeue.isPending || sendNow.isPending || Boolean(view?.send_now_reason)}
                      reason={view?.send_now_reason || (row.send_now ? row.send_now_reason : null)}
                      error={sendNow.variables === row.id ? sendNow.error : null} onClick={() => sendNow.mutate(row.id)} />
                    <button type="button" className="link" disabled={chat.isError || chat.isPending || dequeue.isPending || sendNow.isPending} onClick={() => dequeue.mutate(row.id)}>
                      {dequeue.isPending && dequeue.variables === row.id ? "Removing…" : "Remove"}
                    </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
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
          pill={phone ? undefined : <L3EngineSelect name={name} engine={view?.engine ?? ""} engines={engines} />}
          hint="L3 answers or creates one task. Shift + Enter for a new line."
        />
      </div>
    </section>
    </ProseTerminal>
    </ProseScope>
  );
}
