import { useCallback, useRef, useSyncExternalStore } from "react";
import { notifyManager, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import type { ChatMessage, ChatView } from "./api";
import type { ImageSubmission } from "../components/ImageDraft";
import type { ImagePreview } from "../components/MessageImages";

/*
 * A project's outbox (SPEC.md §3.6 Sending, §4.2): each operator message from Send until the canonical
 * GET /api/chat snapshot shows it, so leaving and returning to the conversation never loses it from view.
 * It lives in the query cache beside the conversation reads, for this document only: pairing clears it with
 * every query, and removing the project drops it with that project's reads (forgetProject). Only the send
 * that starts an entry adds it; its later callbacks update the entry while it exists and never recreate it.
 */

/** One message on its way: the bubble, then the reply it streams, until the server's record takes over. */
export interface Outgoing {
  /** The send id: the stored row's `request_id` or the queue row's `id`. */
  id: string;
  /** The bubble's identity on screen: its first send id, kept when Retry sends it again under a new one. */
  row?: string;
  text: string;
  reply: string;
  accepted: boolean;
  turnId: string | null;
  error: string | null;
  done: boolean;
  /** The project's read count when the send finished: only a read started after it retires the entry. */
  settledAt?: number;
  queueId?: string;
  images?: ImagePreview[];
  /** The images as sent, kept for Retry and Edit until the server accepts the message. */
  replay?: ImageSubmission;
  /** The composer's recovery copy of this send (Composer.tsx), released once the message needs it no more. */
  copy?: string;
  delivery?: ChatMessage["delivery"];
  /** Why the message stands on its own bubble: the server refused it, or nothing confirmed its delivery. */
  failure?: "refused" | "unconfirmed";
  /** The refusal's own words. */
  reason?: string;
  /** Exchanges this turn completed before a Send now message joined it, shown until the history has them. */
  earlier?: { turnId: string | null; text: string; reply: string; delivery?: ChatMessage["delivery"] }[];
}

const NONE: Outgoing[] = [];
const outboxKey = (project: string) => ["chat-outbox", project] as const;

interface Reads {
  /** Successful server reads of each project's conversation, counted in order. */
  count: Map<string, number>;
  /** The read count each conversation snapshot came from; a page's own cache edit inherits its source's. */
  of: WeakMap<object, number>;
}
const tracked = new WeakMap<QueryClient, Reads>();

/** One cache listener per client numbers the reads, whether or not a conversation is on screen. */
function reads(client: QueryClient): Reads {
  let state = tracked.get(client);
  if (state) return state;
  const fresh: Reads = { count: new Map(), of: new WeakMap() };
  state = fresh;
  tracked.set(client, fresh);
  client.setQueryDefaults(["chat-outbox"], { gcTime: Infinity });
  client.getQueryCache().subscribe((event) => {
    const [kind, project] = event.query.queryKey;
    if (kind !== "chat" || typeof project !== "string" || event.query.queryKey.length !== 2
      || event.type !== "updated" || event.action.type !== "success") return;
    const data: unknown = event.query.state.data;
    if (!data || typeof data !== "object") return;
    const count = fresh.count.get(project) ?? 0;
    // Engine selection and mutation rollback write this cache too; they are not reads.
    if (event.action.manual) {
      if (!fresh.of.has(data)) fresh.of.set(data, count);
      return;
    }
    fresh.count.set(project, count + 1);
    fresh.of.set(data, count + 1);
  });
  return state;
}

/** Which read a conversation snapshot came from; 0 for none. */
export function readOf(client: QueryClient, view: ChatView | undefined): number {
  return view ? reads(client).of.get(view) ?? 0 : 0;
}

type Entry = { id: string; row?: string };
type OutboxKey = readonly unknown[];

/** Start an entry, replacing one with the same send id, or taking the place of `replaces` in its position. */
function startEntry<E extends Entry>(client: QueryClient, key: OutboxKey, entry: E, replaces?: string) {
  client.setQueryData<E[]>(key, (entries = []) => {
    const at = entries.findIndex((current) => current.id === replaces);
    const rest = entries.filter((current) => current.id !== entry.id && current.id !== replaces);
    if (at < 0) return [...rest, entry];
    const replaced = entries[at]!;
    return [...rest.slice(0, at), { ...entry, row: replaced.row ?? replaced.id }, ...rest.slice(at)];
  });
}

/** Change an entry that still exists; `null` removes it. A missing entry stays missing. Returns the change. */
function updateEntry<E extends Entry>(client: QueryClient, key: OutboxKey, id: string, change: (current: E) => E | null) {
  const entries = client.getQueryData<E[]>(key);
  const current = entries?.find((entry) => entry.id === id);
  if (!entries || !current) return undefined;
  const next = change(current);
  client.setQueryData<E[]>(key, entries.flatMap((entry) => entry !== current ? [entry] : next ? [next] : []));
  return next;
}

function dropEntries(client: QueryClient, key: OutboxKey, ids: Set<string>) {
  const entries = client.getQueryData<Entry[]>(key);
  if (entries?.some((entry) => ids.has(entry.id))) client.setQueryData<Entry[]>(key, entries.filter((entry) => !ids.has(entry.id)));
}

/**
 * Start an entry, replacing one with the same send id (an image Retry keeps its identity), or taking the
 * place of `replaces` (a refused message's Retry, in its position).
 */
export function startOutgoing(client: QueryClient, project: string, entry: Outgoing, replaces?: string) {
  reads(client);
  startEntry(client, outboxKey(project), entry, replaces);
}

export function updateOutgoing(client: QueryClient, project: string, id: string, change: (current: Outgoing) => Outgoing | null) {
  return updateEntry(client, outboxKey(project), id, change);
}

/** Mark an entry finished. In-flight reads are cancelled first, so only a read started after it can retire it. */
export async function settleOutgoing(client: QueryClient, project: string, id: string, change: (current: Outgoing) => Outgoing) {
  await client.cancelQueries({ queryKey: ["chat", project], exact: true });
  const read = reads(client).count.get(project) ?? 0;
  updateOutgoing(client, project, id, (current) => ({ ...change(current), done: true, settledAt: read }));
}

export function dropOutgoing(client: QueryClient, project: string, ids: Set<string>) {
  dropEntries(client, outboxKey(project), ids);
}

/**
 * Whether the server's record has taken over an entry, judged on the snapshot the page renders: its
 * queued row is listed, its turn's answer or error is stored, or it finished before this read began
 * and the read holds the turn its stream named.
 */
export function retired(entry: Outgoing, view: ChatView | undefined, read: number): boolean {
  if (!view) return false;
  if (entry.done && entry.settledAt !== undefined && read > entry.settledAt
    && (!entry.turnId || view.history.some((row) => row.turn_id === entry.turnId))) return true;
  if (view.queued?.some((row) => row.id === entry.id || row.id === entry.queueId)) return true;
  // An unconfirmed message turns into the stored one once the server's record shows it.
  if (entry.failure === "unconfirmed" && view.history.some((row) => carries(row, entry))) return true;
  const turns = new Set(view.history.filter((row) => carries(row, entry)).map((row) => row.turn_id));
  return view.history.some((row) => (row.role === "assistant" || row.role === "error") && row.turn_id && turns.has(row.turn_id));
}

/**
 * A stored row is this entry's: it belongs to the turn the stream named or, before the stream names one,
 * it carries the send id. After Send now joins the turn, the turn id alone names the current exchange.
 */
export function carries(row: ChatMessage, entry: Outgoing): boolean {
  if (entry.turnId) return row.turn_id === entry.turnId;
  return row.request_id === entry.id || Boolean(row.queue_ids?.includes(entry.id))
    || Boolean(entry.queueId && row.queue_ids?.includes(entry.queueId));
}

/** Still waiting on its request: a finished, refused or unconfirmed send no longer holds anything. */
export function inFlight(entry: Outgoing): boolean {
  return !entry.done && !entry.failure;
}

/**
 * The project's entries, re-rendering with both the conversation's reads and the entries. The last snapshot
 * belongs to the mounted view alone, so only the query cache holds an entry once its view is gone.
 */
export function useOutbox(project: string): Outgoing[] {
  const client = useQueryClient();
  const last = useRef<{ entries: Outgoing[]; read: number } | null>(null);
  const subscribe = useCallback((changed: () => void) => {
    const notify = notifyManager.batchCalls(changed);
    return client.getQueryCache().subscribe((event) => {
      const [kind, name] = event.query.queryKey;
      if (name === project && (kind === "chat-outbox" || kind === "chat")) notify();
    });
  }, [client, project]);
  const snapshot = useCallback(() => {
    const entries = client.getQueryData<Outgoing[]>(outboxKey(project)) ?? NONE;
    const read = reads(client).count.get(project) ?? 0;
    if (last.current?.entries !== entries || last.current.read !== read) last.current = { entries, read };
    return last.current;
  }, [client, project]);
  return useSyncExternalStore(subscribe, snapshot).entries;
}

/*
 * A task conversation's outbox (SPEC.md §3.10): each operator message to the L2 from Send until the task's
 * record lists it, kept in the query cache like the project's, so leaving the task and returning keeps it in view.
 * Pairing clears it with every query, and removing the project drops it (forgetProject).
 */

/** One message to the L2 on its way, or one that failed and stands on its own bubble. */
export interface TaskOutgoing {
  /** The send id, which the stored row keeps as its id. */
  id: string;
  /** The bubble's identity on screen: its first send id, kept when Retry sends it again under a new one. */
  row?: string;
  text: string;
  images?: ImagePreview[];
  /** The images as sent, kept for Retry and Edit until the server accepts the message. */
  replay?: ImageSubmission;
  /** The composer's recovery copy of this send (Composer.tsx), released once the message needs it no more. */
  copy?: string;
  failure?: "refused" | "unconfirmed";
  reason?: string;
}

const taskOutboxKey = (project: string, slug: string) => ["task-outbox", project, slug] as const;

export const startTaskOutgoing = (client: QueryClient, project: string, slug: string, entry: TaskOutgoing, replaces?: string) => {
  client.setQueryDefaults(["task-outbox"], { gcTime: Infinity });
  startEntry(client, taskOutboxKey(project, slug), entry, replaces);
};
export const updateTaskOutgoing = (client: QueryClient, project: string, slug: string, id: string, change: (current: TaskOutgoing) => TaskOutgoing | null) =>
  updateEntry(client, taskOutboxKey(project, slug), id, change);
export const dropTaskOutgoing = (client: QueryClient, project: string, slug: string, ids: Set<string>) =>
  dropEntries(client, taskOutboxKey(project, slug), ids);

const NO_TASK_ENTRIES: TaskOutgoing[] = [];
/** The task's entries, re-rendering when they change; the task's own reads re-render its page. */
export function useTaskOutbox(project: string, slug: string): TaskOutgoing[] {
  const client = useQueryClient();
  const subscribe = useCallback((changed: () => void) => {
    const notify = notifyManager.batchCalls(changed);
    return client.getQueryCache().subscribe((event) => {
      const [kind, name, task] = event.query.queryKey;
      if (kind === "task-outbox" && name === project && task === slug) notify();
    });
  }, [client, project, slug]);
  const snapshot = useCallback(() => client.getQueryData<TaskOutgoing[]>(taskOutboxKey(project, slug)) ?? NO_TASK_ENTRIES, [client, project, slug]);
  return useSyncExternalStore(subscribe, snapshot);
}
