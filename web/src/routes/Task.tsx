import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { Link, NavLink, useLocation, useMatch, useNavigate, useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError, imageSendRefused, removeL2Message, sendL2Message, taskAction, useOverview, useTask, useSendNow, writePending } from "../data/api";
import { handBack, startPending, useDecisionSurface } from "../data/pending";
import type { PendingWrite } from "../data/pending";
import { SendNow, SendNowError } from "../components/SendNow";
import { BusyLabel } from "../components/StatusMark";
import type { Decision, Overview, Step, TaskMessage, TaskView } from "../data/api";
import { InlineProse, ProseScope } from "../components/Prose";
import { ProseTerminal } from "../components/CodeBlock";
import { requestCommand } from "../data/terminalCommand";
import { agoText, dayText, localTimes, when } from "../data/observed";
import { isStep, questionPath, selections, turnLabel } from "../data/decisions";
import { stoppedByCoordinator, taskExplanation } from "../data/taskStatus";
import { holdText } from "../components/TaskCard";
import { Bubble, Coordination, DayDivider, MessageRow, RemoveMessage, Reply, notDone, useEntrance } from "../components/Bubbles";
import { Choice, deliveryChoice } from "../components/Choice";
import Composer, { markSubmitted, recoveryHoldsDraft, releaseSubmitted, returnToComposer } from "../components/Composer";
import { dropTaskOutgoing, startTaskOutgoing, updateTaskOutgoing, useTaskOutbox, type SendTarget } from "../data/outbox";
import type { TaskOutgoing } from "../data/outbox";
import { TaskActivity } from "../components/TaskActivity";
import { SteeringControls, SteeringNotice, useTaskSteering } from "../components/TaskSteering";
import type { Steering } from "../components/TaskSteering";
import { TransferCard, TransferDialog, useTaskTransfer } from "../components/TaskTransfer";
import { TaskModelButton } from "../components/ModelMenu";
import type { Transfer } from "../components/TaskTransfer";
import type { ImageSubmission } from "../components/ImageDraft";
import { MessageImages, PendingImages } from "../components/MessageImages";
import { ShownFiles } from "../components/Shown";
import { ShownReader } from "./ShownReader";
import { Question, QuestionSet, ReviewDecision, StepBody } from "../components/DecisionCard";
import type { QuestionOrigin } from "../components/DecisionCard";
import { TaskContext, TokenUsage } from "../components/TokenUsage";
import { latestReviews, ReviewBoxes, ReviewCard, ReviewFeedback, useTaskReview } from "../components/TaskReview";
import type { ReviewControls } from "../components/TaskReview";
import { useTaskBack } from "../components/useTaskBack";
import { useVisitMemory, useVisitReturn } from "../components/visitMemory";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import { PhoneHeader } from "../shell/PhoneHeader";
import LiveSession from "./LiveSession";
import Terminal, { TerminalPreview } from "../components/Terminal";
import { useTaskSwipe } from "../components/useTaskSwipe";
import "./task-details.css";

// TaskView is a passthrough schema: everything the server sends beyond the declared fields (attempt,
// session_id, l2_engine, prs, hold_merge, fault, ...) arrives typed `unknown`, so narrow it here.
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The idle phone view: laid out and retaining its state, invisible and untouchable (SPEC.md §3.10). */
const idle = { visibility: "hidden" } as const;
/** The rows that are not the L2's or the L3's are the operator's: right-aligned bubbles (SPEC.md §3.3). */
const REPLIERS = new Set(["l2", "l3"]);

// ---- what the page says about the task ---------------------------------------------------------

type Tone = "ok" | "held" | "danger" | undefined;

interface Chip {
  text: string;
  busy?: boolean;
  tone?: Tone;
  href?: string;
}

interface Facts {
  state: string;
  label: string;
  dot: "running" | "waiting" | "danger" | "idle";
  /** Compact state, engine/model and PR with its checks state; full reasons are disclosed. */
  chips: Chip[];
  /** What the task requested, launched with and what its engine reported (SPEC.md §3.10). */
  selection: ModelFacts;
  /** Task details: attempt and when it started or finished. */
  sub: string;
  /** What a queued task waits for; shown where the live panel would be. */
  waiting: string | null;
  /** The known wait, with raw technical evidence confined to details. */
  explanation: string | null;
  blockReason: string;
  queueReason: string;
  holdReason: string;
  engineLabel: string;
  finished: boolean;
  canMessage: boolean;
  canStop: boolean;
  canResume: boolean;
  resumeHeld: boolean;
  canReject: boolean;
  /** A held review-ready PR waiting for the operator (#419), and where the PR lives. */
  review: Decision | null;
  steps: Step[];
  repository?: string | null;
}

interface ModelFacts { chip: Chip | null; rows: [string, string][]; note: string }

/**
 * The chip names what the task runs with, in its model button's words: "6.1 Sol on Codex · High". Details keep the
 * chosen, launched and reported values and routing's recorded reason.
 */
function modelFacts(task: TaskView): ModelFacts {
  const running = task.running_model;
  if (!running) return { chip: null, rows: [], note: "" };
  const chip: Chip = { text: [[running.model, running.engine].filter(Boolean).join(" on "), running.effort].join(" · ") };
  const rows: [string, string][] = [
    ["Requested", running.chosen ? `${running.chosen} · set for this task` : "Project choice or defaults"],
    ["Launched", running.launched],
    ["Engine reports", running.reported ?? "Not reported yet"],
    ...(str(task["routing"]) ? [["Routing", sentence(str(task["routing"]))] as [string, string]] : []),
  ];
  return { chip, rows, note: `Messages and resumes ask for ${running.launched}.` };
}

export function taskFacts(task: TaskView, overview: Overview | undefined, project: string, repository?: string | null): Facts {
  const state = task.state ?? "";
  const held = state === "blocked" && Boolean(task.resume_after);
  const planned = state === "queued" ? task.planned_wait : null;
  const faultKind = str(task["fault"]);
  const reason = str(task["blocked_reason"]);
  const waitsOnL3 = state === "blocked" && !held && !faultKind && str(task["waiting_on"]) === "l3";
  const finished = state === "done" || state === "rejected";
  const turnRows = overview?.queue.filter((row): row is Decision => row.project === project && row.slug === task.slug) ?? [];
  // An operator step L3 named this task on is the operator's turn while the task waits (SPEC.md §3.8.4).
  const waitingHere = (state === "blocked" || state === "reported") && !held && !faultKind && !task["stop_id"];
  const steps = waitingHere ? overview?.queue.filter((row): row is Step => isStep(row) && row.project === project
    && row.tasks.some((waiting) => waiting.slug === task.slug)) ?? [] : [];
  // The header counts the task's own open questions, so it agrees with the chat before the overview refreshes.
  const handedBack = str(task["handed_back"]);
  const asked = state === "blocked" || state === "reported" ? (task.question_group?.questions ?? []).filter((question) =>
    question.status === "open" && !question.response && question.audience !== "l3" && (question.asked ?? "") > handedBack) : [];
  const turn = turnLabel([...asked, ...steps, ...turnRows.filter((row) => row.kind === "review")]);
  const replying = Boolean(task["handed_back"]) && (state === "running" || held);

  const engineId = str(task["l2_engine"]) || str(task["engine"]);
  const engineLabel = overview?.engines.find((e) => e.engine === engineId)?.label ?? engineId;
  const selection = modelFacts(task);

  const prs = arr(task["prs"]).map((n) => num(n)).filter((n): n is number => n != null);
  const number = prs[prs.length - 1];
  const landed = rec(rec(task.report_json)["landed"]);
  const merged = arr(landed["prs"]).some((p) => num(rec(p)["number"]) === number && rec(p)["merged"] === true);
  const runs = arr(landed["main_runs"]);
  const conclusion = str(rec(runs[runs.length - 1])["conclusion"]);
  const checks =
    conclusion === "success" ? "main checks passed" : conclusion === "failure" ? "main checks failed" : conclusion ? `main run ${conclusion}` : "";
  const prChip: Chip | null =
    number != null
      ? {
          text: [`PR #${number} ${merged ? "merged" : "open"}`, checks].filter(Boolean).join(" · "),
          tone: conclusion === "failure" ? "danger" : merged ? "ok" : undefined,
          href: repository ? `${repository}/pull/${number}` : undefined,
        }
      : null;
  const hold = str(task["hold_merge"]);

  const label = task.steering?.state === "stopped" ? stoppedByCoordinator(task) ? "Stopped by coordinator" : "Stopped by you" : task.steering?.state === "stopping" ? "Stopping…"
    : faultKind && state === "blocked" ? "Work interrupted" : turn ?? (replying ? "L2 replying to you"
    : task.steering?.state === "resuming" ? "Waiting to resume" : planned ? "Planned" : held ? "Queued" : state === "blocked"
    ? waitsOnL3 ? "Waiting for coordinator" : "Paused" : state === "running" ? "L2 working" : sentence(state || "unknown"));
  const dot: Facts["dot"] =
    faultKind || state === "rejected" ? "danger" : state === "running" ? "running" : state === "blocked" && !held ? "waiting" : "idle";

  const attempt = num(task["attempt"]) ?? 0;
  const sub = [
    attempt > 0 ? `attempt ${attempt}` : "",
    state === "running" && agoText(task["dispatched"]) ? `started ${agoText(task["dispatched"])}` : "",
    finished && agoText(task["updated"]) ? `${state} ${agoText(task["updated"])}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const wait = overview?.wip.waiting.find((w) => w.project === project && w.slug === task.slug);
  const explanation = taskExplanation(task, turnRows.find((row) => row.kind === "review") ?? steps[0]);
  const waiting = state === "queued" || held
    ? planned ? explanation : sentence(holdText(wait?.hold, wait?.why ?? (held ? "resume" : "dispatch"))) : null;

  return {
    state,
    label,
    dot,
    chips: [{ text: label, tone: dot === "danger" ? "danger" as const : undefined }, ...(selection.chip ? [selection.chip] : []), ...(prChip ? [prChip] : []), ...(hold ? [{ text: "Merge held", tone: "held" as const }] : [])],
    sub,
    waiting,
    explanation: !faultKind && (state === "queued" || held) ? planned || wait?.hold ? waiting : null : explanation,
    blockReason: state === "blocked" ? reason : "",
    queueReason: state === "queued" || held ? wait?.hold ?? "" : "",
    holdReason: hold,
    engineLabel,
    selection,
    finished,
    canMessage: state === "running" || state === "blocked" || task["can_continue"] === true || (state === "queued" && (!task["dispatched"] || Boolean(task.question) || task.transfer?.request?.status === "done")),
    canStop: state === "running",
    canResume: (state === "blocked" || task["can_continue"] === true) && (!task.steering || task.steering.state === "idle") && task.question?.status !== "open",
    resumeHeld: Boolean(task.transfer?.resume_hold),
    canReject: ["queued", "running", "blocked", "reported"].includes(state),
    review: turnRows.find((row) => row.kind === "review") ?? null,
    steps,
    repository,
  };
}

// ---- the conversation (SPEC.md §3.3 bubbles and prose, §3.6 composer, §3.10 states) ------------


/** An L2 reply's validation captures open in their own tab, leaving the conversation and draft in place. */
function CaptureLink({ project, slug, message }: { project: string; slug: string; message: TaskMessage }) {
  const captures = message.captures ?? [];
  if (!captures.length) return null;
  return <a className="text-meta prose-link capture-link" href={`/projects/${project}/tasks/${slug}/captures/${message.id}`} target="_blank" rel="noopener noreferrer">
    {captures.length > 1 ? `Watch ${captures.length} captures` : `Watch capture · ${captures[0]?.title}`}
  </a>;
}

function TaskConversation({ project, task, facts, readOnly, checking, refresh, draft, setDraft, steering, active, denied, setDenied, questionVisit, selection, onEscapeOwnership, reviewControls, transfer }: {
  project: string; task: TaskView; facts: Facts; readOnly: boolean; checking: boolean; refresh: () => void;
  draft: string; setDraft: (value: string) => void;
  steering: Steering; active: boolean; denied: boolean; setDenied: (denied: boolean) => void; questionVisit: number;
  selection: RefObject<{ start: number; end: number } | null>; onEscapeOwnership: (owned: boolean) => void;
  reviewControls: ReviewControls;
  transfer: Transfer;
}) {
  const queryClient = useQueryClient();
  useDecisionSurface(active ? `${project}/${task.slug}` : null);
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const questionId = params.get("question");
  const revision = params.get("revision");
  const reviewId = params.get("review");
  const questions = task.questions ?? (task.question ? [task.question] : []);
  const group = task.question_group;
  const inGroup = (question: Decision) => group?.questions.some((q) => q.id === question.id && q.revision === question.revision);
  const target = questionId ? [...questions].reverse().find((q) => q.id === questionId && (revision == null || String(q.revision) === revision)) : undefined;
  // Back from a preview or review opened on this entry returns to its question's latest revision or the merge review card, even when
  // the URL names an older revision; only the URL's question retargets replies.
  const origin = useVisitReturn<QuestionOrigin>("origin");
  const returned = origin?.project === project && origin.slug === task.slug
    ? origin.id ? [...questions].reverse().find((q) => q.id === origin.id)
      : facts.review ?? undefined : undefined;
  const anchor = returned ?? target;
  const open = (group?.questions ?? (task.question ? [task.question] : [])).filter((question) => question.status === "open" && !question.response);
  const current = open.find((question) => question.shown) ?? open[0];
  // The open group is the last thing in the chat; a reply hands the questions asked before it back to the L2.
  const live = Boolean(group?.questions.some((question) => question.status === "open"));
  const handedBack = str(task["handed_back"]);
  const turn = open.filter((question) => question.audience !== "l3" && (!handedBack || (question.asked ?? "") > handedBack));
  const count = `Your turn · ${turn.length} question${turn.length === 1 ? "" : "s"}`;
  const turnText = turn.some((question) => question.asked_again) ? `${count} · asked again` : count;
  // An operator step naming this task is the next move, so the owner's note to L3 waits out of sight (SPEC.md §3.8.4).
  const answering = live && !turn.length && !facts.steps.length && !(handedBack || group?.questions.some((q) => q.response));
  const scroller = useRef<HTMLDivElement>(null);
  const viewportHeight = useRef(0);
  const anchors = useRef(new Map<string, HTMLDivElement>());
  const followedAnchor = useRef("");
  const following = useRef(!questionId && !reviewId && !returned);
  const [latest, setLatest] = useState(false);
  const [questionOffscreen, setQuestionOffscreen] = useState(false);
  const [questionAtLatest, setQuestionAtLatest] = useState(false);
  const reading = useRef(0);
  const pinned = useRef<number | null>(null);
  const [accessRefresh, setAccessRefresh] = useState(0);
  const messages = task.messages ?? [];
  const conversation = `task/${project}/${task.slug}`;
  const enter = useEntrance(true);
  // Each message stays on screen from Send until the task's record lists it under its send id, wherever the
  // operator went meanwhile; a failed one stays with its reason (SPEC.md §3.10).
  const outbox = useTaskOutbox(project, task.slug);
  const listed = useMemo(() => new Set(messages.map((message) => message.id)), [messages]);
  const pending = outbox.filter((entry) => !listed.has(entry.id));
  useEffect(() => {
    const leaving = outbox.filter((entry) => listed.has(entry.id));
    if (!leaving.length) return;
    for (const entry of leaving) if (entry.copy) releaseSubmitted(conversation, entry.copy);
    dropTaskOutgoing(queryClient, project, task.slug, new Set(leaving.map((entry) => entry.id)));
  }, [outbox, listed, conversation, project, task.slug, queryClient]);
  // A failed message keeps its recovery copy while it shows; pairing clears both.
  const shownCopy = useCallback((copy: string) => outbox.some((entry) => entry.copy === copy), [outbox]);
  const removeFailedAt = useRef<number | null>(null);
  const removal = useMutation({
    mutationFn: (id: string) => removeL2Message(project, task.slug, id),
    onMutate: () => { removeFailedAt.current = null; },
    onError: (error) => {
      removeFailedAt.current = Date.now();
      if (error instanceof ApiError && [401, 403].includes(error.status)) setDenied(true);
    },
    onSuccess: async (_result, id) => {
      await queryClient.cancelQueries({ queryKey: ["task", project, task.slug] });
      queryClient.setQueryData<TaskView>(["task", project, task.slug], (cached) => cached && ({ ...cached,
        messages: cached.messages?.map((row) => row.id === id ? { ...row, delivery: { state: "removed", at: null, removable: false } } : row),
      }));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["task", project, task.slug] }),
  });
  const sendNow = useSendNow(project, task.slug);
  // Queued messages share one Send now, under the last of them (SPEC.md §3.10).
  const waiting = messages.filter((row) => row.delivery?.state === "queued" && (row.delivery.removable || row.delivery.send_now_pending));
  const lastWaiting = waiting.at(-1);
  // A failed removal says "Not removed" only once a read after it still lists the message; one the session
  // took meanwhile (409) needs no line, its own state says it (SPEC.md §3.10).
  const readAt = queryClient.getQueryState(["task", project, task.slug])?.dataUpdatedAt ?? 0;
  const notRemoved = removal.isError && !(removal.error instanceof ApiError && removal.error.status === 409)
    && removeFailedAt.current !== null && readAt > removeFailedAt.current ? removal.variables : undefined;
  const anchorKey = `${location.key}:${questionVisit}:${returned?.id ?? questionId ?? ""}:${returned?.revision ?? revision ?? ""}`;
  const updateQuestionVisibility = useCallback((node: HTMLDivElement) => {
    const anchor = current && anchors.current.get(`${current.id}:${current.revision}`);
    setQuestionAtLatest(Boolean(anchor && anchor.getBoundingClientRect().top - node.getBoundingClientRect().top + node.scrollTop >= node.scrollHeight - node.clientHeight));
    setQuestionOffscreen(Boolean(anchor && (anchor.getBoundingClientRect().bottom < node.getBoundingClientRect().top || anchor.getBoundingClientRect().top > node.getBoundingClientRect().bottom)));
  }, [current?.id, current?.revision]);
  const jumpTo = useCallback((question: Decision) => {
    const node = anchors.current.get(question.id ? `${question.id}:${question.revision}` : "review");
    const container = scroller.current;
    if (!node || !container) return false;
    const history = node.querySelector<HTMLDetailsElement>(":scope > details.question-history");
    if (history) history.open = true;
    following.current = false;
    // Leave a little of the preceding explanation visible; never jump to the latest tool event.
    container.scrollTop += node.getBoundingClientRect().top - container.getBoundingClientRect().top - 80;
    reading.current = container.scrollTop;
    node.focus({ preventScroll: true });
    setQuestionOffscreen(false);
    return true;
  }, []);
  useEffect(() => {
    if (!active || !anchor || followedAnchor.current === anchorKey) return;
    if (jumpTo(anchor)) followedAnchor.current = anchorKey;
  }, [active, anchorKey, anchor, messages, jumpTo]);
  useLayoutEffect(() => {
    const key = `${location.key}:review:${reviewId}`;
    if (!active || !reviewId || followedAnchor.current === key) return;
    const node = document.getElementById(`review-${reviewId}`);
    const container = scroller.current;
    if (!node || !container) return;
    following.current = false;
    container.scrollTop += node.getBoundingClientRect().top - container.getBoundingClientRect().top - 32;
    reading.current = container.scrollTop;
    node.focus({ preventScroll: true });
    followedAnchor.current = key;
  }, [active, location.key, reviewId, task.review]);
  const follow = useCallback((node: HTMLDivElement) => {
    // A scroll up whose event is still queued releases following before a commit or resize can undo it.
    if (following.current && pinned.current !== null && node.clientHeight === viewportHeight.current
      && node.scrollTop < Math.min(pinned.current, node.scrollHeight - node.clientHeight) - 1) {
      following.current = false;
      reading.current = node.scrollTop;
    }
    node.scrollTop = following.current ? node.scrollHeight : reading.current;
    pinned.current = following.current ? node.scrollTop : null;
  }, []);
  useEffect(() => {
    const node = scroller.current;
    const column = node?.firstElementChild;
    if (!active || !node || !column || typeof ResizeObserver === "undefined") return;
    follow(node);
    const observer = new ResizeObserver(() => {
      follow(node);
      viewportHeight.current = node.clientHeight;
      setLatest(!following.current && node.scrollHeight - node.scrollTop - node.clientHeight > 48);
      updateQuestionVisibility(node);
    });
    observer.observe(column);
    observer.observe(node);
    return () => observer.disconnect();
  }, [active, follow, updateQuestionVisibility]);
  useLayoutEffect(() => {
    // A removed preview can clamp scrollTop before a queued scroll event sees its
    // replacement. Restore following during the commit, before that event can pause it.
    const node = scroller.current;
    if (active && following.current && node) follow(node);
  });
  // What a message answers and which stopped session it continues. Retry sends to exactly what the message was
  // sent to, and only while a Send would send there too: a revised question or a new stop takes Edit.
  const targetOf = (context?: Decision | null): SendTarget => ({
    ...(steering.state === "stopped" && task.steering?.stop_id ? { stop_id: task.steering.stop_id } : {}),
    ...(group && group.questions.length > 1 && context && inGroup(context)
      ? { group_id: group.id, group_revision: group.revision }
      : context?.id && context.revision != null ? { question_id: context.id, revision: context.revision } : {}) });
  const send = async (text: string, to: SendTarget, onAccepted?: () => void, images?: ImageSubmission, copy?: string, replaces?: string) => {
    // A new message brings the operator to the bottom; Retry keeps the reader where they are.
    if (!replaces) { following.current = true; setLatest(false); }
    // The stored row keeps the send id as its own, so it settles this bubble in place (SPEC.md §3.6 Sending).
    const id = (images?.request_id ?? crypto.randomUUID()).replaceAll("-", "");
    if (copy) markSubmitted(conversation, copy, null);
    startTaskOutgoing(queryClient, project, task.slug, { id, text, images: images?.previews, replay: images, copy, target: to }, replaces);
    // A reply hands the task back at once: its questions and merge review leave Needs you and the badge (SPEC.md §1.1).
    const entry: PendingWrite = { id, project, slug: task.slug, kind: "message", state: "sending", ...handBack(queryClient, project, task.slug),
      at: new Date().toISOString(), text, answers: [] };
    void startPending(queryClient, entry);
    try {
      const row = await writePending(queryClient, entry, () => sendL2Message({ project, slug: task.slug, text, request_id: images?.request_id ?? id, ...to,
        ...(images ? { images: images.images } : {}) }));
      onAccepted?.();
      if (copy) releaseSubmitted(conversation, copy);
      await queryClient.cancelQueries({ queryKey: ["task", project, task.slug] });
      queryClient.setQueryData<TaskView>(["task", project, task.slug], (cached) =>
        cached && !cached.messages?.some((message) => message.id === row.id)
          ? { ...cached, messages: [...(cached.messages ?? []), row] } : cached,
      );
    } catch (error) {
      // The message keeps its bubble: a refusal says why with Retry and Edit; an unknown outcome says Unconfirmed.
      if (error instanceof ApiError && [401, 403].includes(error.status)) setDenied(true);
      const refused = images ? imageSendRefused(error) : error instanceof ApiError && error.status < 500;
      const failure = refused ? "refused" : "unconfirmed";
      updateTaskOutgoing(queryClient, project, task.slug, id, (current) => ({ ...current, failure,
        reason: refused && error instanceof Error ? error.message : undefined }));
      if (copy) markSubmitted(conversation, copy, failure);
      throw error;
    } finally {
      for (const queryKey of [["task", project, task.slug], ["overview"], ["project", project]]) void queryClient.invalidateQueries({ queryKey });
    }
  };
  const prepareSend = () => {
    const currentNode = current && anchors.current.get(`${current.id}:${current.revision}`);
    const bounds = scroller.current?.getBoundingClientRect();
    const currentBounds = currentNode?.getBoundingClientRect();
    const viewingCurrent = bounds && currentBounds && Math.min(bounds.bottom, currentBounds.bottom) - Math.max(bounds.top, currentBounds.top) >= 48;
    // A historical URL does not keep replies attached to an old revision after the reader scrolls
    // to the current question. Merely having a newer record offscreen does not retarget a reply.
    const to = targetOf(following.current || viewingCurrent ? current ?? target : target ?? current);
    return (text: string, onAccepted?: () => void, images?: ImageSubmission, copy?: string) => send(text, to, onAccepted, images, copy);
  };
  const retarget = targetOf(current);
  const sameTarget = (sent: SendTarget) => (["stop_id", "question_id", "revision", "group_id", "group_revision"] as const)
    .every((key) => sent[key] === retarget[key]);
  // Edit hands a refused message's words, and its images, back to the field and takes it off the conversation;
  // a message whose images do not fit beside the picker's stays.
  const edit = (entry: TaskOutgoing) => {
    if (!returnToComposer(conversation, entry.text, entry.images)) return;
    dropTaskOutgoing(queryClient, project, task.slug, new Set([entry.id]));
    if (entry.copy) releaseSubmitted(conversation, entry.copy);
  };
  // Retry sends the same words, and images, to the same question and session, under a new send id.
  const retry = (entry: TaskOutgoing) => void send(entry.text, entry.target, undefined, entry.replay && { ...entry.replay, request_id: crypto.randomUUID() }, entry.copy, entry.id)
    .catch(() => undefined);
  // A refused message offers Retry while it would go where it went, and Edit while its images can return to the
  // picker (images the server already stored cannot); an unknown outcome only copies its words (SPEC.md §3.10).
  const actionsFor = (entry: TaskOutgoing) => {
    if (entry.failure !== "refused") return ["Copy to draft"];
    const actions = [...sameTarget(entry.target) ? ["Retry"] : [], ...entry.replay?.image_ids ? [] : ["Edit"]];
    return actions.length ? actions : ["Copy to draft"];
  };
  const composing = facts.canMessage && !readOnly && !denied;
  const rows: ReactNode[] = [];
  const deliveries = Object.fromEntries(messages.map((message) => [message.id, message.delivery?.state]));
  const shownReviews = latestReviews(task);
  const restoreAccess = () => { setDenied(false); setAccessRefresh((value) => value + 1); refresh(); };
  let lastDay = "";
  messages.forEach((message, index) => {
    // A removed message leaves the conversation; its record keeps the original (SPEC.md §3.10).
    if (message.delivery?.state === "removed") return;
    const question = questions.find((q) => q.anchor_id === message.id);
    const atGroup = Boolean(group?.anchor_id && group.anchor_id === message.id);
    // The current group occupies one stable discussion anchor. Old revisions retain their receipts.
    if (!atGroup && question && inGroup(question)) return;
    const day = dayText(message.at);
    if (day && day !== lastDay) {
      rows.push(<DayDivider key={`day-${day}`} label={day} />);
      lastDay = day;
    }
    const key = message.id || `${message.at ?? "message"}-${index}`;
    if (question && (!atGroup || !inGroup(question))) {
      const historical = Boolean(group && !inGroup(question));
      const withdrawn = question.resolution?.disposition === "withdrawn";
      const content = <>
        {!withdrawn ? <p className="text-meta text-muted">{question.asked_by === "l3" ? "L3 brought this question to the L2" : "L2"}</p> : null}
        <Question key={`${question.id}:${question.revision}`} decision={question} refreshKey={accessRefresh} deliveries={deliveries} chat disabled={readOnly || checking || denied || facts.finished || question.audience === "l3"} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
      </>;
      rows.push(<div key={`${key}-question`} className="conversation-question" data-historical={historical || undefined} tabIndex={-1} ref={(node) => {
        const id = `${question.id}:${question.revision}`;
        if (node) anchors.current.set(id, node); else anchors.current.delete(id);
      }}>
        {historical && !withdrawn ? <details className="question-history" open={target?.id === question.id && target?.revision === question.revision}>
          <summary>Earlier question · {question.resolution?.disposition === "answered" ? "decision recorded" : "closed"}</summary>
          {content}
        </details> : content}
      </div>);
    }
    if (atGroup && group && !live) {
      const withdrawn = group.questions.every((q) => q.resolution?.disposition === "withdrawn");
      rows.push(<div key={`${key}-group`} className="conversation-question" data-historical={withdrawn || undefined} tabIndex={-1} ref={(node) => {
        group.questions.forEach((q) => {
          const id = `${q.id}:${q.revision}`;
          if (node) anchors.current.set(id, node); else anchors.current.delete(id);
        });
      }}>
        {!withdrawn ? <p className="text-meta text-muted">{group.questions.some((q) => q.asked_by === "l3") ? "L3 brought these questions to the L2" : "L2"}</p> : null}
        <QuestionSet key={group.id} decisions={group.questions} group={group} target={target} refreshKey={accessRefresh} deliveries={deliveries} chat disabled={readOnly || checking || denied || facts.finished} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
      </div>);
    } else if (!question && message.review_id) {
      const review = task.review?.history.find((entry) => entry.id === message.review_id);
      // Each kind shows one card at its latest request's anchor; earlier iterations fold inside it.
      if (!review) rows.push(<p key={key} className="text-meta text-muted">{message.text}</p>);
      else if (shownReviews.has(review.id) && !messages.slice(0, messages.indexOf(message)).some((entry) => entry.review_id === message.review_id)) {
        rows.push(<ReviewCard key={key} review={review} task={task} controls={reviewControls} target={reviewId === review.id} onRead={() => { following.current = false; }} />);
      }
    } else if (!question && message.role === "system") {
      rows.push(<p key={key} className="text-meta text-muted" data-enter={enter(`msg:${key}`) || undefined}><InlineProse text={message.text} /></p>);
    } else if (!question) {
      const removeBlocked = readOnly || checking || denied || removal.isPending || sendNow.isPending || Boolean(message.delivery?.send_now_pending);
      const queueActions = <>
        {message.id === lastWaiting?.id ? <div className="queued-actions">
          <SendNow task pending={sendNow.isPending}
            disabled={readOnly || checking || denied || removal.isPending || sendNow.isPending || !message.delivery?.send_now}
            reason={waiting.find((row) => row.delivery?.send_now_pending)?.delivery?.send_now_reason ?? message.delivery?.send_now_reason}
            onClick={() => sendNow.mutate(message.id)} />
        </div> : null}
        {notRemoved === message.id ? <>
          <span className="text-danger" role="alert">{notDone("Not removed", removal.error instanceof ApiError ? removal.error.message : undefined)}</span>
          {message.delivery?.removable ? <span className="message-actions"><button type="button" className="link" disabled={removeBlocked} onClick={() => removal.mutate(message.id)}>Retry</button></span> : null}
        </> : null}
      </>;
      const chosen = REPLIERS.has(message.role) || message.images?.length ? [] : selections(message, questions);
      if (chosen.length) {
        const state = message.delivery?.state;
        // A press shows as the choice it made, never as words the operator typed; written answers keep their
        // bubble. Each names the question it answers (SPEC.md §3.8.2 Decision selections).
        chosen.forEach((choice, index) => {
          const last = index === chosen.length - 1;
          rows.push(choice.label ? <MessageRow key={`${key}-${index}`} at={state === "queued" ? null : message.at} mine enter={enter(`choice:${key}:${index}`, index ? null : `msg:${key}`)}>
            <Choice mine label={choice.label} about={choice.about} aboutShown
              {...deliveryChoice(state, choice.question?.resolution?.disposition === "answered" && choice.question.resolution.message_id === message.id)}
              onRemove={message.delivery?.removable ? () => removal.mutate(message.id) : undefined} removing={removeBlocked} />
            {last ? queueActions : null}
          </MessageRow> : <Bubble key={`${key}-${index}`} text={choice.text} at={message.at} state={state} enter={enter(`choice:${key}:${index}`, index ? null : `msg:${key}`)}>
            <span className="choice-about">{choice.about}</span>{last ? queueActions : null}
          </Bubble>);
        });
        return;
      }
      rows.push(!REPLIERS.has(message.role) ? <Bubble key={key} text={message.text} at={message.at} enter={enter(`msg:${key}`)}
        images={<MessageImages project={project} images={message.images} />}
        state={message.delivery?.state}
        side={message.delivery?.removable ? <RemoveMessage message={message.text} removing={removal.isPending && removal.variables === message.id}
          disabled={removeBlocked} onClick={() => removal.mutate(message.id)} /> : undefined}>
        {queueActions}
      </Bubble> :
        message.role === "l3" ? <Coordination key={key} text={message.text} summary={message.summary} at={message.at} images={message.images?.length} enter={enter(`msg:${key}`)} onOpen={() => { following.current = false; }}><MessageImages project={project} images={message.images} /></Coordination>
        : <Reply key={key} text={message.text} at={message.at} role={message.role} enter={enter(`msg:${key}`)}><MessageImages project={project} images={message.images} />{message.shown ? <ShownFiles shown={message.shown} /> : null}<CaptureLink project={project} slug={task.slug} message={message} /></Reply>);
    }
  });
  for (const entry of pending) {
    const day = dayText(new Date().toISOString());
    if (day !== lastDay) { rows.push(<DayDivider key={`day-${day}`} label={day} />); lastDay = day; }
    const key = entry.row ?? entry.id;
    rows.push(<Bubble key={key} text={entry.text} at={new Date().toISOString()} images={<PendingImages images={entry.images} />}
      enter={enter(`local:${key}`, `msg:${entry.id}`)} state={entry.failure === "refused" ? "failed" : entry.failure ?? "pending"}>
      {entry.failure === "refused" ? <span className="text-danger" role="alert">{notDone("Not sent", entry.reason)}</span> : null}
      {composing && entry.failure ? <span className="message-actions">
        {actionsFor(entry).map((action, index) => <Fragment key={action}>
          {index ? <span aria-hidden="true">·</span> : null}
          <button type="button" className="link" disabled={action === "Retry" && (checking || ["stopping", "stop_unconfirmed"].includes(steering.state))}
            onClick={() => action === "Retry" ? retry(entry) : action === "Edit" ? edit(entry) : returnToComposer(conversation, entry.text)}>{action}</button>
        </Fragment>)}
      </span> : null}
    </Bubble>);
  }
  return (
    <section className="convo" aria-label="Task conversation">
      <p className="sr-only" role="status">{removal.isSuccess ? "Message removed" : ""}</p>
      {(readOnly || denied) ? <p className="conversation-notice" role="alert">
        {denied ? "This page lost access." : "Showing saved conversation. Refresh before replying or deciding."}{" "}
        <button className="link" onClick={restoreAccess}>Refresh</button>
      </p> : null}
      {questionId && !target ? <p className="conversation-notice" role="status">This question is unavailable. The task conversation is below.</p> : null}
      {target && current && !inGroup(target) && (target.id !== current.id || target.revision !== current.revision) ? <p className="conversation-notice">This question has been replaced. <Link to={questionPath(current)} state={location.state} replace>View current question</Link></p> : null}
      <div className="task-reading">
      <div className="convo-scroll" ref={scroller} onScroll={(event) => {
        const node = event.currentTarget;
        // Keyboard/composer resize can emit a scroll before ResizeObserver restores bottom-follow.
        if (!active || node.clientHeight !== viewportHeight.current) return;
        reading.current = node.scrollTop;
        following.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
        pinned.current = following.current ? node.scrollTop : null;
        setLatest(!following.current);
        updateQuestionVisibility(node);
      }}>
        <div className="convo-col">
          {messages.length === 0 && !pending.length ? <p className="convo-empty text-muted">{facts.finished ? "No messages on this task." : "No messages yet."}</p> : null}
          {rows}
          {sendNow.isError && waiting.some((row) => row.id === sendNow.variables) ? <SendNowError error={sendNow.error} /> : null}
          {task.review?.history.filter((review) => shownReviews.has(review.id) && !messages.some((message) => message.review_id === review.id)).map((review) => <ReviewCard key={review.id} review={review} task={task} controls={reviewControls} target={reviewId === review.id} onRead={() => { following.current = false; }} />)}
          <ReviewFeedback controls={reviewControls} />
          {live && group ? <div className={turn.length || answering ? "conversation-question" : "sr-only"} data-turn={turn.length ? "operator" : "l2"} tabIndex={-1} ref={(node) => {
            group.questions.forEach((q) => {
              const id = `${q.id}:${q.revision}`;
              if (node) anchors.current.set(id, node); else anchors.current.delete(id);
            });
          }}>
            {turn.length || answering ? <>
              <p className="conversation-turn">{turn.length ? turnText : "L3 is answering"}</p>
              <QuestionSet key={group.id} decisions={group.questions} group={group} target={target} refreshKey={accessRefresh} deliveries={deliveries} chat disabled={readOnly || checking || denied || facts.finished} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
            </> : facts.steps.length ? null : <p className="sr-only" role="status">{task.state === "queued" ? "Sent · waiting for the L2 to start." : "Sent · the L2 has your reply."}</p>}
          </div> : (task.question?.status === "resolved" || task.question?.response) && !facts.finished ? <p className="sr-only" role="status">{task.state === "running" ? "Work resumed" : task.state === "queued" ? "Waiting for the L2 to start" : "Waiting to resume"}</p>
          : handedBack && !facts.finished && !facts.review ? <p className="sr-only" role="status">{task.state === "queued" ? "Sent · waiting for the L2 to start." : "Sent · the L2 has your reply."}</p> : null}
          {facts.review ? <div className="conversation-question" data-turn="operator" tabIndex={-1} ref={(node) => {
            if (node) anchors.current.set("review", node); else anchors.current.delete("review");
          }}>
            <p className="conversation-turn">Your turn · review before merge</p>
            <ReviewDecision key={`${facts.review.pr}:${facts.review.review_hold_id ?? ""}`} decision={facts.review} repository={facts.repository} chat disabled={readOnly || checking || denied} />
          </div> : null}
          {facts.steps.map((step) => <div key={step.id} className="conversation-question" data-turn="operator">
            <p className="conversation-turn">Your turn · a command from L3</p>
            <StepBody step={step} tasks={false} />
          </div>)}
          {/* What the task waits on ends the chat, after any card, under the header's state; L3's open question already says it (SPEC.md §3.10). */}
          {facts.explanation && !(transfer.engineOffer && !readOnly && !denied) && !(answering && !task["fault"]) ? <p className="conversation-status" data-tone={task["fault"] ? "danger" : undefined} role="status">
            <span className="dot" data-state={facts.dot} aria-hidden />{facts.explanation}
          </p> : null}
          <TransferCard transfer={transfer} />
          {facts.canMessage && steering.state === "running" && !(task.state === "blocked" && current) ? <TaskActivity activity={task.activity} /> : null}
        </div>
      </div>
      {(latest || (current && questionOffscreen)) ? <div className="conversation-jumps">
        {current && questionOffscreen && turn.length ? <button type="button" className="btn conversation-pill" aria-label={count} onClick={() => jumpTo(current)}>{turn.length} question{turn.length === 1 ? "" : "s"} ↓</button> : null}
        {latest && (transfer.controls || !(current && questionOffscreen && turn.length && questionAtLatest)) ? <button type="button" className="btn" aria-label={transfer.controls ? "Switch controls" : "Latest messages"} onClick={() => { following.current = true; if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; setLatest(false); }}>{transfer.controls ? "Switch controls ↓" : "Latest ↓"}</button> : null}
      </div> : null}
      </div>
      {facts.canMessage ? <div className="convo-dock">
        <Composer active={active} conversation={conversation} value={draft} onChange={setDraft} onSubmit={(text, accepted, images, copy) => prepareSend()(text, accepted, images, copy)} prepareSubmit={prepareSend} selection={selection} onEscapeOwnership={onEscapeOwnership}
        pill={<TaskModelButton transfer={transfer} />}
        shows={shownCopy}
        imageScope={{ project, task: task.slug, engine: str(task["l2_engine"]) || str(task["engine"]) }}
        ariaLabel="Message the L2" placeholder="Message the L2" disabled={readOnly || denied}
        sendDisabled={transfer.stopping || (task.state === "queued" && task.transfer?.request?.status === "done") || ["stopping", "stop_unconfirmed"].includes(steering.state)}
        hint={task.state === "blocked" && steering.state === "idle" && !task.transfer?.resume_hold ? "Sending resumes the L2 with your message." : ""} />
        {steering.state === "stopped" ? <p className="text-meta text-muted">Send a correction to continue this session.</p>
          : null}
      </div> : null}
    </section>
  );
}

// ---- Reject and operational Resume (SPEC.md §3.10) ---------------------------------------------

type Confirm = "" | "reject";

function useTaskActions(project: string, slug: string) {
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<Confirm>("");
  const [reason, setReason] = useState("");
  const act = useMutation({
    mutationFn: taskAction,
    onSuccess: () => {
      setConfirm("");
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["task", project, slug] });
      void queryClient.invalidateQueries({ queryKey: ["project", project] });
    },
  });
  const open = useCallback(
    (which: Confirm) => {
      act.reset();
      setConfirm(which);
    },
    [act],
  );
  const run = () => {
    const text = reason.trim();
    act.mutate({ project, slug, action: confirm, ...(confirm === "reject" && text ? { reason: text } : {}) });
  };
  const resume = () => act.mutate({ project, slug, action: "resume", reason: "Resume requested from the task conversation" });
  return { confirm, open, reason, setReason, run, resume, pending: act.isPending, error: act.isError };
}

function ActionButtons({ facts, actions }: { facts: Facts; actions: ReturnType<typeof useTaskActions> }) {
  if (!facts.canStop && !facts.canReject) return null;
  return (
    <>
      {facts.canResume ? <button type="button" className="btn btn-ghost task-action" disabled={actions.pending || facts.resumeHeld} title={facts.resumeHeld ? "Waiting for engine availability" : undefined} onClick={actions.resume}><BusyLabel busy={actions.pending} label="Resume" working="Resuming…" /></button> : null}
      {facts.canReject ? (
        <button type="button" className="btn btn-ghost task-action" onClick={() => actions.open("reject")}>
          Reject
        </button>
      ) : null}
    </>
  );
}

function ConfirmRow({ actions }: { actions: ReturnType<typeof useTaskActions> }) {
  if (!actions.confirm) return null;
  const question = "Reject this task?";
  return (
    <div className="task-confirm" role="group" aria-label={question}
      onKeyDown={(event) => { if (event.key === "Escape" && !actions.pending) { event.stopPropagation(); actions.open(""); } }}>
      <p className="task-confirm-text">
        Reject this task? Its worker ends and the task is archived.
      </p>
      {(
        <input
          className="field"
          aria-label="Reason (optional)"
          placeholder="Reason (optional)"
          value={actions.reason}
          onChange={(event) => actions.setReason(event.target.value)}
        />
      )}
      <div className="task-confirm-actions">
        <button type="button" className="btn" autoFocus disabled={actions.pending} onClick={() => actions.open("")}>
          Cancel
        </button>
        <button type="button" className="btn btn-danger" disabled={actions.pending} onClick={actions.run}>
          {actions.pending ? <span className="spinner" aria-hidden /> : null}
          Reject task
        </button>
      </div>
      {actions.error ? (
        <p className="text-meta text-danger" role="alert">
          Could not reject the task.{" "}
          <button type="button" className="link" onClick={actions.run}>
            Retry
          </button>
        </p>
      ) : null}
    </div>
  );
}

// ---- the page ----------------------------------------------------------------------------------

function Chips({ chips }: { chips: Chip[] }) {
  return (
    <div className="task-chips">
      {chips.map((chip) => (
        <span key={chip.text} className="chip" data-tone={chip.tone}>
          {chip.busy ? <span className="spinner" aria-hidden /> : null}
          <ChipText chip={chip} />
        </span>
      ))}
    </div>
  );
}

function ChipText({ chip }: { chip: Chip }) {
  return chip.href ? <a href={chip.href} target="_blank" rel="noopener noreferrer">{chip.text}</a> : chip.text;
}

export function PanelIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" width="20" height="20">
      <rect x="3" y="4" width="14" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 4v12" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function TaskPage({
  project,
  task,
  overview,
  liveRoute,
  terminalRoute,
  readOnly,
  checking,
  refresh,
  detailsOpen,
  setDetailsOpen,
  shown,
}: {
  project: string;
  task: TaskView;
  overview: UseQueryResult<Overview>;
  liveRoute: boolean;
  /** `/view/:shownId[/:revision]`: the reader of files the L2 showed, over or beside the conversation. */
  shown: { id: string; revision?: string } | null;
  /** `/terminal`: the phone's Terminal tab, the desktop panel's Terminal view. */
  terminalRoute: boolean;
  readOnly: boolean;
  checking: boolean;
  refresh: () => Promise<unknown>;
  detailsOpen: boolean;
  setDetailsOpen: (open: boolean) => void;
}) {
  const { phone, panelInline } = useViewport();
  const location = useLocation();
  const navigate = useNavigate();
  const back = useTaskBack(project);
  const facts = taskFacts(task, overview.data, project, task.repository);
  const decision = (task.question_group?.questions ?? (task.question ? [task.question] : []))
    .find((question) => question.status === "open" && !question.response);
  // The unsent message returns with Back/Forward to this history entry, like unsent answers. Recovered words have one
  // owner, the recovery copy, which every visit reads; the entry keeps none of them.
  const message = `message:${project}/${task.slug}`;
  const kept: Record<string, string> = useVisitMemory<string>([message], (): string | undefined =>
    draft && !recoveryHoldsDraft(`task/${project}/${task.slug}`, draft) ? draft : undefined);
  const [draft, setDraft] = useState<string>(kept[message] ?? "");
  const [denied, setDenied] = useState(false);
  const [questionVisit, setQuestionVisit] = useState(0);
  const closeDetails = useCallback(() => setDetailsOpen(false), [setDetailsOpen]);
  const selection = useRef<{ start: number; end: number } | null>(null);
  const [voiceOwnsEscape, setVoiceOwnsEscape] = useState(false);
  const steering = useTaskSteering(project, task, refresh);
  const transfer = useTaskTransfer(project, task, refresh, readOnly || checking || denied, () => setDenied(true), closeDetails);
  if (transfer.label) {
    facts.label = transfer.label;
    facts.chips[0] = { text: transfer.label, busy: transfer.active };
  }
  const actions = useTaskActions(project, task.slug);
  const reviewControls = useTaskReview(project, task, refresh, readOnly || checking, closeDetails);
  const resumeError = facts.canResume && actions.error && !actions.confirm
    ? <p className="task-line text-danger" role="alert">Could not resume. Try again.</p> : null;
  // Inline at the panel width, open by default; below it an overlay the operator opens (SPEC.md §2.2 rule).
  const [panelOpen, setPanelOpen] = useState(liveRoute || terminalRoute || (panelInline && !new URLSearchParams(location.search).has("question")));
  useEffect(() => {
    if (liveRoute || terminalRoute) setPanelOpen(true);
  }, [liveRoute, terminalRoute]);
  // A terminal opens in the task's worktree, so only a task that has one offers it; an open view stays.
  const terminalOffered = terminalRoute || (Boolean(task.worktree) && task.state !== "done" && task.state !== "rejected");
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const base = `/projects/${project}/tasks/${task.slug}`;
  const title = task.title || task.slug;
  const detailsButton = <button type="button" className="icon-btn" aria-label="Task details" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>⋯</button>;
  const details = detailsOpen ? <Overlay label="Task details" side={phone ? "bottom" : "right"} onClose={closeDetails}>
    <div className="task-details">
      <div className="task-details-heading"><h2>Task details</h2><button type="button" className="icon-btn" aria-label="Close task details" onClick={closeDetails}>×</button></div>
      <p className="task-details-title">{title}</p>
      {facts.sub ? <p className="task-sub">{facts.sub}</p> : null}
      <Chips chips={facts.chips} />
      {/* The complete reasons come first: the page shows only the state and a short excerpt (SPEC.md §3.10). */}
      {facts.blockReason ? <section><h3>{facts.label}</h3><p>{localTimes(facts.blockReason)}</p></section> : null}
      {facts.queueReason ? <section><h3>Start condition</h3><p>{localTimes(facts.queueReason)}</p></section> : null}
      {facts.holdReason ? <section><h3>Merge held</h3><p>{localTimes(facts.holdReason)}</p></section> : null}
      <ReviewBoxes task={task} controls={reviewControls} view={(id) => {
        closeDetails();
        const search = new URLSearchParams(location.search);
        search.delete("question"); search.delete("revision"); search.set("review", id);
        void navigate(`${base}?${search}`, { replace: true, state: location.state });
      }} />
      <TaskContext context={task.token_usage?.context} running={task.state === "running"} />
      <TokenUsage usage={task.token_usage} running={task.state === "running"} engines={overview.data?.engines} />
      {facts.selection.rows.length ? <section aria-label="Model and effort"><h3>Model and effort</h3>
        <dl className="task-model">{facts.selection.rows.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl>
        <p className="text-meta text-muted">{facts.selection.note}</p>
      </section> : null}
      {decision ? <Link className="btn" to={questionPath(decision)} state={location.state} replace onClick={() => { setQuestionVisit((visit) => visit + 1); closeDetails(); }}>View question</Link> : null}
      {phone ? <>
        <div className="task-actions"><ActionButtons facts={facts} actions={actions} /></div>
        <ConfirmRow actions={actions} />
        {resumeError}
      </> : null}
    </div>
  </Overlay> : null;
  useEffect(() => {
    if (phone || readOnly || denied || voiceOwnsEscape || actions.confirm || steering.state !== "running") return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.key !== "Escape" || event.repeat || event.isComposing || event.defaultPrevented ||
          target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false]), .transfer-card") ||
          document.querySelector('[role="dialog"], [role="menu"], [aria-haspopup][aria-expanded="true"], .overlay')) return;
      event.preventDefault();
      steering.stop();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [phone, readOnly, denied, voiceOwnsEscape, actions.confirm, steering]);
  // The phone's views in their tab order: a swipe moves one step and never wraps (SPEC.md §3.10).
  const paths = terminalOffered ? ["", "/live", "/terminal"] : ["", "/live"];
  const view = terminalRoute ? 2 : liveRoute ? 1 : 0;
  // A completed swipe already shows its view, so the switch commits with its URL rather than in a transition:
  // a touch on that view never reaches the previous view's handlers, which would drop it.
  const swipe = useTaskSwipe(phone && !detailsOpen && !voiceOwnsEscape && !shown, view, paths.length, (next) => {
    void navigate(`${base}${paths[next]}${location.search}`, { replace: true, state: location.state, flushSync: true });
  });
  // The phone tab row stays above the sliding views; the terminal on screen places its × there.
  const [tabSlot, setTabSlot] = useState<HTMLDivElement | null>(null);
  const control = <SteeringControls steering={steering} disabled={readOnly || denied || transfer.stopping || Boolean(steering.state === "stopped" && (task.transfer?.resume_hold || (transfer.request?.phase === "stopping" && !task.steering?.stop_id)))} escape={!phone} />;

  // A drag that reveals Live session starts its transcript, so the incoming view loads while it slides in.
  const panelSwitch = terminalOffered ? <nav className="panel-switch" aria-label="Panel view">
    <Link to={`${base}/live${location.search}`} replace state={location.state} aria-current={terminalRoute ? undefined : "page"}>Live session</Link>
    <Link to={`${base}/terminal${location.search}`} replace state={location.state} aria-current={terminalRoute ? "page" : undefined}>Terminal</Link>
  </nav> : undefined;
  const tabs = (close: ReactNode) => <nav className="task-tabs" aria-label="Task views">
    <NavLink className="task-tab" to={`${base}${location.search}`} replace state={location.state} end>
      Conversation
    </NavLink>
    <NavLink className="task-tab" to={`${base}/live${location.search}`} replace state={location.state}>
      Live session
    </NavLink>
    {terminalOffered ? close ? <div className="task-tab-close">
      <NavLink className="task-tab" to={`${base}/terminal${location.search}`} replace state={location.state}>Terminal</NavLink>
      {close}
    </div> : <NavLink className="task-tab" to={`${base}/terminal${location.search}`} replace state={location.state}>
      Terminal
    </NavLink> : null}
  </nav>;
  // The shell's end, however it ends, returns to Live session.
  const terminal = <Terminal project={project} task={task.slug} keys={phone} closeIcon={phone}
    onLeave={() => void navigate(`${base}/live${location.search}`, { replace: true, state: location.state })}
    head={phone ? (close) => tabSlot ? createPortal(tabs(close), tabSlot) : null : (close) => <header className="live-head">{panelSwitch}{close}</header>} />;
  const panel = !phone && terminalRoute ? terminal : <ProseScope project={project} repository={task.repository}><LiveSession project={project} task={task} engineLabel={facts.engineLabel} waiting={facts.waiting} steering={!phone && !panelInline ? steering : undefined} readOnly={readOnly || denied} active={!phone || liveRoute || swipe.dragging} heading={phone ? undefined : panelSwitch} /></ProseScope>;
  // A `run` block in this conversation opens the task's terminal with its command typed (SPEC.md §3.3).
  const runTarget = useMemo(() => Boolean(task.worktree) && task.state !== "done" && task.state !== "rejected"
    ? { open: (command: string) => {
      requestCommand(project, task.slug, command);
      void navigate(`${base}/terminal${location.search}`, { replace: true, state: location.state });
    } }
    : { unavailable: "This task has no terminal now." }, [task.worktree, task.state, task.slug, project, base, location.search, location.state, navigate]);
  // The reader is a panel where the live session panel sits inline, and covers the screen elsewhere (SPEC.md §3.11).
  const known = shown ? shown.revision == null ? task.messages?.find((row) => row.id === shown.id)?.shown
    : (task.questions ?? (task.question ? [task.question] : [])).find((question) => question.id === shown.id && String(question.revision) === shown.revision)?.shown : undefined;
  const reader = shown ? <ShownReader key={`${shown.id}/${shown.revision ?? ""}`} project={project} slug={task.slug} shownId={shown.id} revision={shown.revision}
    known={known ?? undefined} panel={!phone && panelInline} /> : null;
  const screenReader = reader && (phone || !panelInline);
  const conversation = <ProseScope project={project} repository={task.repository}><ProseTerminal value={runTarget}><TaskConversation project={project} task={task} facts={facts} readOnly={readOnly} checking={checking} refresh={refresh} draft={draft} setDraft={setDraft} steering={steering} active={!phone || view === 0} denied={denied} setDenied={setDenied} questionVisit={questionVisit} selection={selection} onEscapeOwnership={setVoiceOwnsEscape} reviewControls={reviewControls} transfer={transfer} /></ProseTerminal></ProseScope>;

  if (phone) {
    const idleUnless = (index: number) => view !== index && !swipe.dragging ? idle : undefined;
    return (
      <>
      <PhoneHeader overview={overview} onTitleClick={() => setDetailsOpen(true)} titleExpanded={detailsOpen} status={
        <span className="task-state-line" role="status"><span className={transfer.active ? "spinner" : "dot"} data-state={facts.dot} aria-hidden /><span data-tone={facts.dot === "danger" ? "danger" : undefined}>{facts.label}</span>{facts.holdReason ? <span data-tone="held"> · Merge held</span> : null}</span>
      }>{control}</PhoneHeader>
      <div className="task-page" data-phone inert={Boolean(screenReader)}>
        <SteeringNotice steering={steering} />
        {!detailsOpen ? resumeError : null}
        {!detailsOpen && actions.error && actions.confirm ? <p className="task-line text-danger" role="alert">Could not {actions.confirm} the task. <button type="button" className="link" onClick={() => setDetailsOpen(true)}>Retry</button></p> : null}
        {terminalRoute ? null : tabs(null)}
        <div className="task-tab-slot" ref={setTabSlot} />
        <div className="task-views">
          {/* Every view stays laid out; the idle ones are invisible until a drag reveals one (SPEC.md §3.10). */}
          <div className="task-track" ref={swipe.track} style={{ marginLeft: `${-100 * view}%` }}>
            <div className="task-view" style={idleUnless(0)}>{conversation}</div>
            <div className="task-view" style={idleUnless(1)}>{panel}</div>
            {terminalOffered ? <div className="task-view" style={idleUnless(2)}>{terminalRoute ? terminal : <TerminalPreview />}</div> : null}
          </div>
        </div>
        {details}
      </div>
      {reader}
      <TransferDialog transfer={transfer} />
      </>
    );
  }

  return (
    <>
    <div className="task-page" inert={Boolean(screenReader)}>
      <header className="task-header">
        <div className="task-crumb-row">
          <button type="button" className="task-crumb" aria-label="Back" onClick={back}>
            ‹ {project}
          </button>
          <h1 className="task-title">
            <span className="dot" data-state={facts.dot} aria-hidden />
            <span>{title}</span>
          </h1>
          <div className="task-actions">
            {control}
            <ActionButtons facts={facts} actions={actions} />
            {detailsButton}
            <button
              type="button"
              className="icon-btn"
              aria-label={terminalRoute ? "Terminal" : "Live session"}
              aria-pressed={panelOpen && !reader}
              onClick={() => {
                // The reader holds the panel's place; the panel button puts the live session back.
                if (reader) {
                  void navigate(`${base}${location.search}`, { replace: true, state: location.state });
                  setPanelOpen(true);
                } else setPanelOpen((open) => !open);
              }}
            >
              <PanelIcon />
            </button>
          </div>
        </div>
        <Chips chips={facts.chips} />
        <SteeringNotice steering={steering} />
        <ConfirmRow actions={actions} />
        {resumeError}
      </header>
      <div className="task-body">
        <div className="task-main">{conversation}</div>
        {reader && !screenReader ? reader : panelOpen ? (
          panelInline ? (
            panel
          ) : (
            <Overlay label={terminalRoute ? "Terminal" : "Live session"} side="right" onClose={closePanel}>
              {panel}
            </Overlay>
          )
        ) : null}
      </div>
      {details}
    </div>
    {screenReader ? reader : null}
    <TransferDialog transfer={transfer} />
    </>
  );
}

function TaskSkeleton({ phone }: { phone: boolean }) {
  return (
    <div className="task-page" data-phone={phone || undefined} aria-label="Loading">
      <header className="task-header">
        {phone ? null : <div className="skeleton h-4 w-24" />}
        {phone ? null : <div className="skeleton h-6 w-80 max-w-full" />}
        <div className="flex gap-2">
          <div className="skeleton h-5 w-20" />
          <div className="skeleton h-5 w-28" />
        </div>
      </header>
      <div className="task-body">
        <div className="task-main">
          <div className="convo-col gap-4 p-4">
            <div className="skeleton h-12 w-2/3 self-end" />
            <div className="skeleton h-16 w-4/5" />
            <div className="skeleton h-10 w-1/2 self-end" />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The task page (SPEC.md §3.10): the operator's conversation with the L2 beside the worker's live
 * session, direct Stop and confirmed Reject; on the phone a compact header and two tabs, the
 * composer pinned above the tab bar on the Conversation tab, and a Terminal tab while the task has a
 * worktree. `/live` and `/terminal` select their tab on phone and their view of the desktop panel.
 */
export default function Task() {
  const params = useParams();
  const project = params["name"] ?? "";
  const slug = params["slug"] ?? "";
  const liveRoute = Boolean(useMatch("/projects/:name/tasks/:slug/live"));
  const terminalRoute = Boolean(useMatch("/projects/:name/tasks/:slug/terminal"));
  const replyView = useMatch("/projects/:name/tasks/:slug/view/:messageId")?.params;
  const questionView = useMatch("/projects/:name/tasks/:slug/view/:questionId/:revision")?.params;
  const shownId = replyView?.messageId ?? questionView?.questionId;
  const shownRevision = questionView?.revision;
  const shown = useMemo(() => shownId ? { id: shownId, revision: shownRevision } : null, [shownId, shownRevision]);
  const task = useTask(project, slug);
  const overview = useOverview();
  const { phone } = useViewport();
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => setDetailsOpen(false), [project, slug]);
  const header = phone && !task.data ? <PhoneHeader overview={overview} /> : null;
  let content: ReactNode;
  if (task.isPending) content = <TaskSkeleton phone={phone} />;
  else if (task.isError && !task.data) {
    content = (
      <div className="page">
        <p className="text-danger">
          Could not load the task.{" "}
          <button type="button" className="link" onClick={() => task.refetch()}>
            Retry
          </button>
        </p>
      </div>
    );
  } else content = <TaskPage key={`${project}:${slug}`} project={project} task={task.data!} overview={overview} liveRoute={liveRoute} terminalRoute={terminalRoute} readOnly={task.isError} checking={task.isFetching && !task.isFetchedAfterMount} refresh={() => task.refetch({ throwOnError: true })} detailsOpen={detailsOpen} setDetailsOpen={setDetailsOpen} shown={shown} />;
  return <>{header}{content}</>;
}
