import { useCallback, useSyncExternalStore } from "react";
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
  uncertain?: boolean;
  replay?: ImageSubmission;
  delivery?: ChatMessage["delivery"];
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
  snapshots: Map<string, { entries: Outgoing[]; read: number }>;
}
const tracked = new WeakMap<QueryClient, Reads>();

/** One cache listener per client numbers the reads, whether or not a conversation is on screen. */
function reads(client: QueryClient): Reads {
  let state = tracked.get(client);
  if (state) return state;
  const fresh: Reads = { count: new Map(), of: new WeakMap(), snapshots: new Map() };
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

/** Start an entry, replacing one with the same send id (an image Retry keeps its identity). */
export function startOutgoing(client: QueryClient, project: string, entry: Outgoing) {
  reads(client);
  client.setQueryData<Outgoing[]>(outboxKey(project), (entries = NONE) =>
    [...entries.filter((current) => current.id !== entry.id), entry]);
}

/** Change an entry that still exists; `null` removes it. A missing entry stays missing. */
export function updateOutgoing(client: QueryClient, project: string, id: string, change: (current: Outgoing) => Outgoing | null) {
  const entries = client.getQueryData<Outgoing[]>(outboxKey(project));
  if (!entries?.some((entry) => entry.id === id)) return;
  client.setQueryData<Outgoing[]>(outboxKey(project), entries.flatMap((entry) => {
    if (entry.id !== id) return [entry];
    const next = change(entry);
    return next ? [next] : [];
  }));
}

/** Mark an entry finished. In-flight reads are cancelled first, so only a read started after it can retire it. */
export async function settleOutgoing(client: QueryClient, project: string, id: string, change: (current: Outgoing) => Outgoing) {
  await client.cancelQueries({ queryKey: ["chat", project], exact: true });
  const read = reads(client).count.get(project) ?? 0;
  updateOutgoing(client, project, id, (current) => ({ ...change(current), done: true, settledAt: read }));
}

export function dropOutgoing(client: QueryClient, project: string, ids: Set<string>) {
  const entries = client.getQueryData<Outgoing[]>(outboxKey(project));
  if (entries?.some((entry) => ids.has(entry.id))) {
    client.setQueryData<Outgoing[]>(outboxKey(project), entries.filter((entry) => !ids.has(entry.id)));
  }
}

/**
 * Whether the server's record has taken over an entry, judged on the snapshot the page renders: its
 * queued row is listed, its turn's answer or error is stored, or it finished before this read began.
 */
export function retired(entry: Outgoing, view: ChatView | undefined, read: number): boolean {
  if (!view) return false;
  if (entry.done && entry.settledAt !== undefined && read > entry.settledAt) return true;
  if (view.queued?.some((row) => row.id === entry.id || row.id === entry.queueId)) return true;
  const turns = new Set(view.history.filter((row) => carries(row, entry)).map((row) => row.turn_id));
  return view.history.some((row) => (row.role === "assistant" || row.role === "error") && row.turn_id && turns.has(row.turn_id));
}

/** A stored row is this entry's: it carries the send id, or it belongs to the turn the stream named. */
export function carries(row: ChatMessage, entry: Outgoing): boolean {
  return row.request_id === entry.id || Boolean(row.queue_ids?.includes(entry.id))
    || Boolean(entry.queueId && row.queue_ids?.includes(entry.queueId)) || Boolean(entry.turnId && row.turn_id === entry.turnId);
}

/** The project's entries and the read count, re-rendering with both the conversation's reads and the entries. */
export function useOutbox(project: string): Outgoing[] {
  const client = useQueryClient();
  const subscribe = useCallback((changed: () => void) => {
    const notify = notifyManager.batchCalls(changed);
    return client.getQueryCache().subscribe((event) => {
      const [kind, name] = event.query.queryKey;
      if (name === project && (kind === "chat-outbox" || kind === "chat")) notify();
    });
  }, [client, project]);
  const snapshot = useCallback(() => {
    const state = reads(client);
    const entries = client.getQueryData<Outgoing[]>(outboxKey(project)) ?? NONE;
    const read = state.count.get(project) ?? 0;
    const cached = state.snapshots.get(project);
    if (cached?.entries === entries && cached.read === read) return cached;
    const next = { entries, read };
    state.snapshots.set(project, next);
    return next;
  }, [client, project]);
  return useSyncExternalStore(subscribe, snapshot).entries;
}
