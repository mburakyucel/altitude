import { useCallback, useEffect, useSyncExternalStore } from "react";
import { notifyManager, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import type { Attention, Decision, Overview, ProjectView, TaskView } from "./api";

/*
 * The operator's own choices on their way (SPEC.md §1.1 Actions answer at once): answers, a merge approval or a
 * message to an L2, from the press until Altitude's record shows them. Each is an entry here; the read hooks lay
 * the entries over the server's data (`select`), so the cache itself only ever holds what the server said, and a
 * refused entry simply stops overlaying. Kept in the query cache for this document only: pairing clears it with
 * every query, and removing the project drops that project's entries (forgetProject).
 */

export type PendingAnswer = { question_id: string; revision: number; text: string };
type View = "overview" | "project" | "task";

export interface PendingWrite {
  /** The send id, which the saved message keeps as its id. */
  id: string;
  project: string;
  slug: string;
  kind: "answers" | "approval" | "message";
  /** `unconfirmed`: no answer came, so the next read of the task decides between saved and refused. */
  state: "sending" | "saved" | "unconfirmed" | "refused";
  /** The rows the press handed back to the L2: each question revision and merge review on screen at the press. */
  hides: string[];
  /** The latest of those questions' asks: the hand-back covers them, and a question asked later still shows. */
  handedBack: string;
  at: string;
  /** The saved message's words, and the responses it carries; a typed message's bubble is the task outbox's. */
  text: string;
  answers: PendingAnswer[];
  /** The card's picks as sent, keyed as its drafts are: a refused send shows them chosen again. */
  picks?: Record<string, { option?: string; text?: string }>;
  /** A merge approval's PR and hold, so its card shows the send. */
  pr?: number;
  hold?: string | null;
  /** Read counts when the outcome was settled: only a read begun later shows the record without this entry. */
  reads?: Record<View, number>;
  error?: Error;
}

const KEY = ["pending"] as const;
const NONE: PendingWrite[] = [];

interface Reads {
  count: Map<string, number>;
  of: WeakMap<object, number>;
}
const tracked = new WeakMap<QueryClient, Reads>();
const outcomes = new Map<string, (entry: PendingWrite) => void>();
/** How each unconfirmed entry reads its task again; any later read of a view tries once more until one succeeds. */
const deciders = new Map<string, () => Promise<unknown>>();

function viewOf(key: readonly unknown[]): View | null {
  if (key[0] === "overview" && key.length === 1) return "overview";
  if (key[0] === "project" && key.length === 2) return "project";
  if (key[0] === "task" && key.length === 3) return "task";
  return null;
}
const viewKey = (view: View, project: string, slug: string) =>
  view === "overview" ? ["overview"] : view === "project" ? ["project", project] : ["task", project, slug];

/** One cache listener per client numbers the reads of every view an entry changes, and settles entries on them. */
function reads(client: QueryClient): Reads {
  let state = tracked.get(client);
  if (state) return state;
  const fresh: Reads = { count: new Map(), of: new WeakMap() };
  state = fresh;
  tracked.set(client, fresh);
  // The list's identity is its change signal: a copy republished after a read must reach every view.
  client.setQueryDefaults(KEY, { gcTime: Infinity, structuralSharing: false });
  client.getQueryCache().subscribe((event) => {
    // Pairing removes every query, the entries with them, so nothing waits on them any more.
    if (event.type === "removed" && event.query.queryKey[0] === KEY[0]) { outcomes.clear(); deciders.clear(); return; }
    const view = viewOf(event.query.queryKey);
    if (!view) return;
    if (event.type === "removed") { queueMicrotask(() => settle(client)); return; }
    if (event.type !== "updated" || event.action.type !== "success") return;
    const data: unknown = event.query.state.data;
    if (!data || typeof data !== "object") return;
    const key = JSON.stringify(event.query.queryKey);
    const count = fresh.count.get(key) ?? 0;
    // A page's own cache edit is not a read; it inherits the read it was made from.
    if (event.action.manual) {
      if (!fresh.of.has(data)) fresh.of.set(data, count);
      return;
    }
    fresh.count.set(key, count + 1);
    fresh.of.set(data, count + 1);
    queueMicrotask(() => settle(client, true));
  });
  return state;
}

const countOf = (client: QueryClient, key: readonly unknown[]) => reads(client).count.get(JSON.stringify(key)) ?? 0;
const readOf = (client: QueryClient, data: object | undefined) => data ? reads(client).of.get(data) ?? 0 : 0;

/** Whether an entry still shows in a view's snapshot: until the view's first read begun after its save. */
function shows(client: QueryClient, entry: PendingWrite, view: View, data: object): boolean {
  if (entry.state === "refused") return false;
  if (entry.state !== "saved") return true;
  return readOf(client, data) <= (entry.reads?.[view] ?? 0);
}

const questionsOf = (task: TaskView) => [...task.questions ?? [], ...task.question_group?.questions ?? []];
const listed = (entry: PendingWrite, task: TaskView) => Boolean(task.messages?.some((message) => message.id === entry.id))
  || entry.kind === "answers" && entry.answers.every((answer) => questionsOf(task)
    .some((question) => question.id === answer.question_id && question.revision === answer.revision && question.response?.text === answer.text));
/** A refused send still has a card to return to: a question it answered still waits at that revision, or its approval was not saved. */
const returns = (entry: PendingWrite, task: TaskView) => entry.kind === "answers"
  ? entry.answers.some((answer) => questionsOf(task).some((question) => question.id === answer.question_id
    && question.revision === answer.revision && question.status === "open" && !question.response))
  : !listed(entry, task);

function entries(client: QueryClient): PendingWrite[] {
  return client.getQueryData<PendingWrite[]>(KEY) ?? NONE;
}

function change(client: QueryClient, id: string, update: (entry: PendingWrite) => PendingWrite | null) {
  const current = entries(client);
  const entry = current.find((row) => row.id === id);
  if (!entry) return;
  const next = update(entry);
  client.setQueryData<PendingWrite[]>(KEY, current.flatMap((row) => row !== entry ? [row] : next ? [next] : []));
  if (next?.state !== "unconfirmed") deciders.delete(id);
  if (next && next.state !== entry.state && next.state !== "unconfirmed") {
    outcomes.get(id)?.(next);
    outcomes.delete(id);
  }
  if (!next) outcomes.delete(id);
}

/**
 * Decide unconfirmed entries on the first read of their task begun after the failure, and drop entries every view
 * has caught up with. A refused answer stays for its card to show the picks with Not sent, until Retry, an edit or
 * a later read of its task that leaves it no card to return to.
 */
function settle(client: QueryClient, read = false) {
  const before = entries(client);
  for (const entry of before) {
    const task = client.getQueryData<TaskView>(["task", entry.project, entry.slug]);
    const later = Boolean(task) && readOf(client, task!) > (entry.reads?.task ?? 0);
    if (entry.state === "unconfirmed") {
      if (later) change(client, entry.id, (current) => listed(current, task!) ? { ...current, state: "saved" }
        : { ...current, state: "refused", error: new Error("Not sent") });
      // The deciding read failed too, as it does while still offline: the next read that works elsewhere tries again.
      else if (read) void deciders.get(entry.id)?.().catch(() => undefined);
      continue;
    }
    if (entry.state === "refused" && entry.kind === "message") { change(client, entry.id, () => null); continue; }
    // A send judged lost that a later read lists was saved after all; one whose questions moved on has no card left.
    if (entry.state === "refused" && later && !returns(entry, task!)) { change(client, entry.id, () => null); continue; }
    if (entry.state !== "saved") continue;
    const caughtUp = (["overview", "project", "task"] as const).every((view) => {
      const data = client.getQueryData<object>(viewKey(view, entry.project, entry.slug));
      return !data || !shows(client, entry, view, data);
    });
    if (caughtUp) change(client, entry.id, () => null);
  }
  // A view selects its new read before this listener counts it, so a saved entry it still shows is published again
  // to select the read once more, now counted, without the entry.
  const after = entries(client);
  if (read && after === before && after.some((entry) => entry.state === "saved")) client.setQueryData<PendingWrite[]>(KEY, [...after]);
}

const rowKey = (row: Attention): string | null =>
  row.slug === null ? null : row.id ? `q:${row.id}:${row.revision}` : row.kind === "review" && row.pr != null ? `review:${row.pr}` : null;

/**
 * What a reply to a task hands back to its L2 (altitude/tasks.py `operator_questions`, `review_pr`): every open
 * operator question and the merge review shown for it now. A stopped task or a fault stays until the record clears it.
 */
export function handBack(client: QueryClient, project: string, slug: string): Pick<PendingWrite, "hides" | "handedBack"> {
  const overview = client.getQueryData<Overview>(["overview"]);
  const view = client.getQueryData<ProjectView>(["project", project]);
  const task = client.getQueryData<TaskView>(["task", project, slug]);
  const rows: Attention[] = [
    ...overview?.queue.filter((row) => row.project === project && row.slug === slug) ?? [],
    ...(view?.decisions ?? []).filter((row) => row.slug === slug) as Attention[],
    ...[...task?.questions ?? [], ...task?.question_group?.questions ?? []].filter((question) =>
      question.status === "open" && question.audience === "operator" && !question.response),
  ];
  const hides = [...new Set(rows.map(rowKey).filter((key): key is string => Boolean(key)))];
  const asked = rows.map((row) => row.asked ?? "").sort().at(-1) ?? "";
  return { hides, handedBack: asked };
}

/**
 * Start an entry; it shows in every view at once. Its outcome resolves when Altitude's answer, or for an
 * unconfirmed send the next read of its task, says saved or refused.
 */
export function startPending(client: QueryClient, entry: PendingWrite): Promise<PendingWrite> {
  reads(client);
  client.setQueryData<PendingWrite[]>(KEY, (current = []) => [...current.filter((row) => row.id !== entry.id), entry]);
  return new Promise((resolve) => outcomes.set(entry.id, resolve));
}

/**
 * Record the send's outcome. Reads of the entry's views in flight are cancelled first, so only a read begun after
 * it shows the record without the entry; an unconfirmed entry also reads its task again, which decides it.
 */
export async function finishPending(client: QueryClient, id: string, state: "saved" | "refused" | "unconfirmed", error?: Error, read?: () => Promise<unknown>) {
  const entry = entries(client).find((row) => row.id === id);
  if (!entry) return;
  const views = ["overview", "project", "task"] as const;
  for (const view of views) await client.cancelQueries({ queryKey: viewKey(view, entry.project, entry.slug), exact: true });
  const counts = Object.fromEntries(views.map((view) => [view, countOf(client, viewKey(view, entry.project, entry.slug))])) as Record<View, number>;
  change(client, id, (current) => ({ ...current, state, reads: counts, ...error ? { error } : {} }));
  if (state === "unconfirmed" && read) {
    deciders.set(id, read);
    void read().catch(() => undefined);
  }
  settle(client);
}

/** Drop an entry, as Retry or an edited pick does with a refused one. */
export function dropPending(client: QueryClient, id: string) {
  outcomes.delete(id);
  change(client, id, () => null);
}

export function forgetPending(client: QueryClient, project: string) {
  for (const entry of entries(client)) if (entry.project === project) dropPending(client, entry.id);
}

/** Every entry, re-rendering when they change. */
export function usePending(): PendingWrite[] {
  const client = useQueryClient();
  const subscribe = useCallback((changed: () => void) => {
    const notify = notifyManager.batchCalls(changed);
    return client.getQueryCache().subscribe((event) => { if (event.query.queryKey[0] === KEY[0]) notify(); });
  }, [client]);
  return useSyncExternalStore(subscribe, useCallback(() => entries(client), [client]));
}

const hidden = (active: PendingWrite[], row: Attention) => {
  const key = rowKey(row);
  return Boolean(key) && active.some((entry) => entry.project === row.project && entry.slug === row.slug && entry.hides.includes(key!));
};

export function overlayOverview(client: QueryClient, data: Overview, all: PendingWrite[]): Overview {
  const active = all.filter((entry) => shows(client, entry, "overview", data));
  return active.length ? { ...data, queue: data.queue.filter((row) => !hidden(active, row)) } : data;
}

export function overlayProject(client: QueryClient, data: ProjectView, all: PendingWrite[]): ProjectView {
  const active = all.filter((entry) => entry.project === data.name && shows(client, entry, "project", data));
  return active.length && data.decisions ? { ...data, decisions: data.decisions.filter((row) => !hidden(active, { ...row, project: data.name } as Attention)) } : data;
}

/**
 * The task as the record will show it: each answered question carries its response, the turn is handed back (and a
 * stopped wait due to resume) as the save does, and answers or an approval end the conversation as the operator's
 * message, Waiting, until the record lists it under the same id.
 */
export function overlayTask(client: QueryClient, project: string, data: TaskView, all: PendingWrite[]): TaskView {
  const active = all.filter((entry) => entry.project === project && entry.slug === data.slug && shows(client, entry, "task", data));
  if (!active.length) return data;
  let view = data;
  for (const entry of active) {
    const answer = (question: Decision): Decision => {
      const given = !question.response && entry.answers.find((row) => row.question_id === question.id && row.revision === question.revision);
      return given ? { ...question, response: { text: given.text, at: entry.at, message_id: entry.id } } : question;
    };
    const handed = [String(view["handed_back"] ?? ""), entry.handedBack].sort().at(-1) || entry.at;
    view = {
      ...view,
      handed_back: handed,
      ...(view.state === "blocked" && !view["stop_id"] && !view.resume_after ? { resume_after: entry.at } : {}),
      ...(entry.answers.length ? {
        question: view.question && answer(view.question),
        questions: view.questions?.map(answer),
        question_group: view.question_group && { ...view.question_group, questions: view.question_group.questions.map(answer) },
      } : {}),
      ...(entry.kind !== "message" && !view.messages?.some((message) => message.id === entry.id) ? {
        messages: [...view.messages ?? [], { id: entry.id, at: entry.at, role: "operator", text: entry.text,
          delivery: { state: "queued" as const, at: null, removable: false } }],
      } : {}),
    };
  }
  return view;
}

/*
 * Where a refused send shows. A page that lists the task's decisions registers while mounted: the card returns
 * there with Not sent and brings it into view; with none mounted, a toast offers Show.
 */
const surfaces = new Map<string, number>();
export function useDecisionSurface(scope: string | null) {
  useEffect(() => {
    if (!scope) return;
    surfaces.set(scope, (surfaces.get(scope) ?? 0) + 1);
    return () => { surfaces.set(scope, (surfaces.get(scope) ?? 1) - 1); };
  }, [scope]);
}
/** The task's chat is on screen, so its pills already say what happened. */
export const chatOnScreen = (project: string, slug: string) => Boolean(surfaces.get(`${project}/${slug}`));
/** A page that shows the task's cards again after a refusal is on screen. */
export const shownOnScreen = (project: string, slug: string) => Boolean(surfaces.get("*")) || chatOnScreen(project, slug);
