import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react";
import { Link, NavLink, useLocation, useMatch, useNavigate, useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError, imageSendRefused, removeL2Message, sendL2Message, taskAction, useOverview, useProject, useTask } from "../data/api";
import type { Decision, L2MessageInput, Overview, TaskMessage, TaskView } from "../data/api";
import { InlineProse, ProseScope } from "../components/Prose";
import { agoText, when } from "../data/observed";
import { questionPath, turnLabel } from "../data/decisions";
import { Bubble, DayDivider, Reply, dayLabel } from "../components/Bubbles";
import Composer from "../components/Composer";
import { TaskActivity } from "../components/TaskActivity";
import { Stamp } from "../components/Stamp";
import { SteeringControls, SteeringNotice, useTaskSteering } from "../components/TaskSteering";
import type { Steering } from "../components/TaskSteering";
import type { ImageSubmission } from "../components/ImageDraft";
import { MessageImages, PendingImages } from "../components/MessageImages";
import type { ImagePreview } from "../components/MessageImages";
import { Question, QuestionSet, ReviewDecision } from "../components/DecisionCard";
import { TokenUsage } from "../components/TokenUsage";
import { ReviewFeedback, ReviewMenu, ReviewRow, useTaskReview } from "../components/TaskReview";
import type { ReviewControls } from "../components/TaskReview";
import { useTaskBack } from "../components/useTaskBack";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import { PhoneHeader } from "../shell/PhoneHeader";
import LiveSession from "./LiveSession";
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
  tone?: Tone;
  href?: string;
}

interface Facts {
  state: string;
  label: string;
  dot: "running" | "waiting" | "danger" | "idle";
  /** Compact state, engine/model and PR with its checks state; full reasons are disclosed. */
  chips: Chip[];
  /** Task details: attempt, when it started or finished, context used. */
  sub: string;
  /** What a queued task waits for; shown where the live panel would be. */
  waiting: string | null;
  /** A block that is a fault: the one-sentence reason, and L3 has been told. */
  fault: string | null;
  blockReason: string;
  holdReason: string;
  engineLabel: string;
  finished: boolean;
  canMessage: boolean;
  canStop: boolean;
  canResume: boolean;
  canReject: boolean;
  /** The composer's hint line for this state. */
  hint: string;
  /** A held review-ready PR waiting for the operator (#419), and where the PR lives. */
  review: Decision | null;
  repository?: string | null;
}

function faultSummary(text: string): string {
  const first = text.trim().split(/(?<=[.!?])\s+/)[0] ?? "";
  // The complete fault remains in Task details; its permanent notice leaves room for messages.
  if (first.length > 100) return `${first.slice(0, 100).replace(/\s+\S*$/, "")}…`;
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

export function taskFacts(task: TaskView, overview: Overview | undefined, project: string, repository?: string | null): Facts {
  const state = task.state ?? "";
  const held = state === "blocked" && Boolean(task.resume_after);
  const planned = state === "queued" ? task.planned_wait : null;
  const faultKind = str(task["fault"]);
  const reason = str(task["blocked_reason"]);
  const waitsOnL3 = state === "blocked" && !held && !faultKind && str(task["waiting_on"]) === "l3";
  const finished = state === "done" || state === "rejected";
  const turnRows = overview?.queue.filter((row) => row.project === project && row.slug === task.slug) ?? [];
  // The header counts the task's own open questions, so it agrees with the chat before the overview refreshes.
  const handedBack = str(task["handed_back"]);
  const asked = state === "blocked" || state === "reported" ? (task.question_group?.questions ?? []).filter((question) =>
    question.status === "open" && !question.response && question.audience !== "l3" && (question.asked ?? "") > handedBack) : [];
  const turn = turnLabel([...asked, ...turnRows.filter((row) => row.kind === "review")]);
  const replying = Boolean(task["handed_back"]) && (state === "running" || held);

  const engineId = str(task["l2_engine"]) || str(task["engine"]);
  const engineLabel = overview?.engines.find((e) => e.engine === engineId)?.label ?? engineId;
  const model = str(task["engine_model"]) || str(task["model"]);
  const engineChip = [model ? sentence(model) : "", engineLabel].filter(Boolean).join(" on ");

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

  const label = task.steering?.state === "stopped" ? "Stopped by you" : task.steering?.state === "stopping" ? "Stopping…"
    : faultKind && state === "blocked" ? "Paused · fault" : turn ?? (replying ? "L2 replying to you"
    : task.steering?.state === "resuming" ? "Waiting to resume" : planned ? "Planned" : held ? "Queued" : state === "blocked"
    ? waitsOnL3 ? "Waits for L3" : "Paused" : state === "running" ? "L2 working" : sentence(state || "unknown"));
  const dot: Facts["dot"] =
    faultKind || state === "rejected" ? "danger" : state === "running" ? "running" : state === "blocked" && !held ? "waiting" : "idle";

  const attempt = num(task["attempt"]) ?? 0;
  const context = num(rec(task.live)["context_percent"]);
  const sub = [
    attempt > 0 ? `attempt ${attempt}` : "",
    state === "running" && agoText(task["dispatched"]) ? `started ${agoText(task["dispatched"])}` : "",
    finished && agoText(task["updated"]) ? `${state} ${agoText(task["updated"])}` : "",
    state === "running" && context != null ? `${Math.round(context)}% of its context used` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const why = overview?.wip.waiting.find((w) => w.project === project && w.slug === task.slug)?.why;
  const waiting =
    state === "queued"
      ? `Waits for ${planned?.reason ?? (why === "resume" ? "resume" : "dispatch")}`
      : held
        ? `Waits for resume${reason ? ` · ${reason}` : ""}`
        : null;

  return {
    state,
    label,
    dot,
    chips: [{ text: label }, ...(engineChip ? [{ text: engineChip }] : []), ...(prChip ? [prChip] : []), ...(hold ? [{ text: "Merge held", tone: "held" as const }] : [])],
    sub,
    waiting,
    fault: faultKind ? `${faultSummary(reason || `A ${faultKind} fault blocked the task`)} L3 has been told.` : null,
    blockReason: state === "blocked" ? reason : "",
    holdReason: hold,
    engineLabel,
    finished,
    canMessage: state === "running" || state === "blocked" || task["can_continue"] === true || (state === "queued" && (!task["dispatched"] || Boolean(task.question))),
    canStop: state === "running",
    canResume: (state === "blocked" || task["can_continue"] === true) && (!task.steering || task.steering.state === "idle") && task.question?.status !== "open",
    canReject: ["queued", "running", "blocked", "reported"].includes(state),
    hint:
      state === "queued"
        ? "Delivered when Altitude starts the L2."
        : state === "running"
        ? "Reaches the L2 at its next checkpoint."
        : held
          ? "Delivered when Altitude resumes the L2."
          : "Sending resumes the L2 with your message.",
    review: turnRows.find((row) => row.kind === "review") ?? null,
    repository,
  };
}

// ---- the conversation (SPEC.md §3.3 bubbles and prose, §3.6 composer, §3.10 states) ------------

interface PendingMessage { id: string; text: string; images?: ImagePreview[] }

function TaskConversation({ project, task, facts, readOnly, checking, refresh, draft, setDraft, pending, setPending, steering, active, denied, setDenied, questionVisit, selection, onEscapeOwnership, reviewControls }: {
  project: string; task: TaskView; facts: Facts; readOnly: boolean; checking: boolean; refresh: () => void;
  draft: string; setDraft: (value: string) => void; pending: PendingMessage | null; setPending: Dispatch<SetStateAction<PendingMessage | null>>;
  steering: Steering; active: boolean; denied: boolean; setDenied: (denied: boolean) => void; questionVisit: number;
  selection: RefObject<{ start: number; end: number } | null>; onEscapeOwnership: (owned: boolean) => void;
  reviewControls: ReviewControls;
}) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const questionId = params.get("question");
  const revision = params.get("revision");
  const reviewId = params.get("review");
  const questions = task.questions ?? (task.question ? [task.question] : []);
  const group = task.question_group;
  const inGroup = (question: Decision) => group?.questions.some((q) => q.id === question.id && q.revision === question.revision);
  const target = questionId ? [...questions].reverse().find((q) => q.id === questionId && (revision == null || String(q.revision) === revision)) : undefined;
  const open = (group?.questions ?? (task.question ? [task.question] : [])).filter((question) => question.status === "open" && !question.response);
  const current = open.find((question) => question.design_url) ?? open[0];
  // The open group is the last thing in the chat; a reply hands the questions asked before it back to the L2.
  const live = Boolean(group?.questions.some((question) => question.status === "open"));
  const handedBack = str(task["handed_back"]);
  const turn = open.filter((question) => question.audience !== "l3" && (!handedBack || (question.asked ?? "") > handedBack));
  const count = `Your turn · ${turn.length} question${turn.length === 1 ? "" : "s"}`;
  const turnText = turn.some((question) => question.asked_again) ? `${count} · asked again` : count;
  const scroller = useRef<HTMLDivElement>(null);
  const viewportHeight = useRef(0);
  const anchors = useRef(new Map<string, HTMLDivElement>());
  const followedAnchor = useRef("");
  const following = useRef(!questionId && !reviewId);
  const [latest, setLatest] = useState(false);
  const [questionOffscreen, setQuestionOffscreen] = useState(false);
  const [questionAtLatest, setQuestionAtLatest] = useState(false);
  const reading = useRef(0);
  const [accessRefresh, setAccessRefresh] = useState(0);
  const submission = useRef<L2MessageInput | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; setPending(null); };
  }, [setPending]);
  const messages = task.messages ?? [];
  // The stored row carries the submission id: once it renders, it has settled the pending bubble in place.
  useEffect(() => {
    if (pending && messages.some((message) => message.id === pending.id)) setPending(null);
  }, [messages, pending, setPending]);
  const removal = useMutation({
    mutationFn: (id: string) => removeL2Message(project, task.slug, id),
    onSuccess: async (_result, id) => {
      await queryClient.cancelQueries({ queryKey: ["task", project, task.slug] });
      queryClient.setQueryData<TaskView>(["task", project, task.slug], (cached) => cached && ({ ...cached,
        messages: cached.messages?.map((row) => row.id === id ? { ...row, delivery: { state: "removed", at: null, removable: false } } : row),
      }));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["task", project, task.slug] }),
  });
  const anchorKey = `${questionVisit}:${questionId ?? ""}:${revision ?? ""}`;
  const updateQuestionVisibility = useCallback((node: HTMLDivElement) => {
    const anchor = current && anchors.current.get(`${current.id}:${current.revision}`);
    setQuestionAtLatest(Boolean(anchor && anchor.getBoundingClientRect().top - node.getBoundingClientRect().top + node.scrollTop >= node.scrollHeight - node.clientHeight));
    setQuestionOffscreen(Boolean(anchor && (anchor.getBoundingClientRect().bottom < node.getBoundingClientRect().top || anchor.getBoundingClientRect().top > node.getBoundingClientRect().bottom)));
  }, [current?.id, current?.revision]);
  const jumpTo = useCallback((question: Decision) => {
    const node = anchors.current.get(`${question.id}:${question.revision}`);
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
    if (!active || !target || followedAnchor.current === anchorKey) return;
    if (jumpTo(target)) followedAnchor.current = anchorKey;
  }, [active, anchorKey, target, messages, jumpTo]);
  useLayoutEffect(() => {
    const key = `${location.key}:review:${reviewId}`;
    if (!active || !reviewId || followedAnchor.current === key) return;
    const node = document.getElementById(`review-${reviewId}`);
    const container = scroller.current;
    if (!node || !container) return;
    following.current = false;
    const details = node.querySelector("details");
    if (details) details.open = true;
    container.scrollTop += node.getBoundingClientRect().top - container.getBoundingClientRect().top - 32;
    reading.current = container.scrollTop;
    node.focus({ preventScroll: true });
    followedAnchor.current = key;
  }, [active, location.key, reviewId, task.review]);
  useEffect(() => {
    const node = scroller.current;
    const column = node?.firstElementChild;
    if (!active || !node || !column || typeof ResizeObserver === "undefined") return;
    node.scrollTop = following.current ? node.scrollHeight : reading.current;
    const observer = new ResizeObserver(() => {
      node.scrollTop = following.current ? node.scrollHeight : reading.current;
      viewportHeight.current = node.clientHeight;
      setLatest(!following.current && node.scrollHeight - node.scrollTop - node.clientHeight > 48);
      updateQuestionVisibility(node);
    });
    observer.observe(column);
    observer.observe(node);
    return () => observer.disconnect();
  }, [active, updateQuestionVisibility]);
  const prepareSend = () => {
    const currentNode = current && anchors.current.get(`${current.id}:${current.revision}`);
    const bounds = scroller.current?.getBoundingClientRect();
    const currentBounds = currentNode?.getBoundingClientRect();
    const viewingCurrent = bounds && currentBounds && Math.min(bounds.bottom, currentBounds.bottom) - Math.max(bounds.top, currentBounds.top) >= 48;
    // A historical URL does not keep replies attached to an old revision after the reader scrolls
    // to the current question. Merely having a newer record offscreen does not retarget a reply.
    const context = following.current || viewingCurrent ? current ?? target : target ?? current;
    return async (text: string, onAccepted: () => void, images?: ImageSubmission) => {
      following.current = true;
      setLatest(false);
      const preview = { id: (images?.request_id ?? crypto.randomUUID()).replaceAll("-", ""), text, images: images?.previews };
      setPending(preview);
      try {
        const input = images && submission.current?.request_id === images.request_id ? submission.current : { project, slug: task.slug, text, request_id: preview.id,
          ...(steering.state === "stopped" && task.steering?.stop_id ? { stop_id: task.steering.stop_id } : {}),
          ...(images ? { request_id: images.request_id, images: images.images } : {}),
          ...(group && group.questions.length > 1 && context && inGroup(context)
            ? { group_id: group.id, group_revision: group.revision }
            : context?.id && context.revision != null ? { question_id: context.id, revision: context.revision } : {}) };
        if (images) submission.current = input;
        const row = await sendL2Message(input);
        onAccepted();
        await queryClient.cancelQueries({ queryKey: ["task", project, task.slug] });
        queryClient.setQueryData<TaskView>(["task", project, task.slug], (cached) =>
          cached ? { ...cached, messages: [...(cached.messages ?? []).filter((m) => m.id !== row.id), row] } : cached,
        );
        for (const queryKey of [["task", project, task.slug], ["overview"], ["project", project]]) void queryClient.invalidateQueries({ queryKey });
        if (images && submission.current?.request_id === images.request_id) submission.current = null;
      } catch (error) {
        if (error instanceof ApiError && [401, 403].includes(error.status)) setDenied(true);
        if (mounted.current && (!images || imageSendRefused(error))) {
          if (images && submission.current?.request_id === images.request_id) submission.current = null;
          setPending((current) => current === preview ? null : current);
        }
        throw error;
      }
    };
  };
  const rows: ReactNode[] = [];
  const restoreAccess = () => { setDenied(false); setAccessRefresh((value) => value + 1); refresh(); };
  let lastDay = "";
  messages.forEach((message, index) => {
    const question = questions.find((q) => q.anchor_id === message.id);
    const atGroup = Boolean(group?.anchor_id && group.anchor_id === message.id);
    // The current group occupies one stable discussion anchor. Old revisions retain their receipts.
    if (!atGroup && question && inGroup(question)) return;
    const at = when(message.at);
    const day = at != null ? dayLabel(at) : "";
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
        <Question key={`${question.id}:${question.revision}`} decision={question} refreshKey={accessRefresh} chat disabled={readOnly || checking || denied || facts.finished || question.audience === "l3"} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
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
        <QuestionSet key={group.id} decisions={group.questions} group={group} refreshKey={accessRefresh} chat disabled={readOnly || checking || denied || facts.finished} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
      </div>);
    } else if (!question && message.review_id) {
      const review = task.review?.history.find((entry) => entry.id === message.review_id);
      // A review owns one durable conversation anchor; status updates reuse that row.
      if (!messages.slice(0, messages.indexOf(message)).some((entry) => entry.review_id === message.review_id)) {
        rows.push(review ? <ReviewRow key={key} review={review} controls={reviewControls} availability={task.review} onRead={() => { following.current = false; }} /> : <p key={key} className="text-meta text-muted">{message.text}</p>);
      }
    } else if (!question && message.role === "system") {
      rows.push(<p key={key} className="text-meta text-muted"><InlineProse text={message.text} /></p>);
    } else if (!question) {
      rows.push(!REPLIERS.has(message.role) ? <Bubble key={key} text={message.delivery?.state === "removed" ? "Message removed" : message.text} at={message.at}
        images={message.delivery?.state !== "removed" ? <MessageImages project={project} images={message.images} /> : undefined}
        receipt={message.delivery ? message.delivery.state === "removed" ? "Removed · not sent to the session" : message.delivery.state === "sending" ? "Sending to session · cannot remove" : message.delivery.state === "delivered" ? "Delivered to session" : message.delivery.state === "queued" ?
          ["stopping", "stopped", "stop_unconfirmed"].includes(steering.state) ? "Queued · held until you continue" : task.state === "queued" && !task["dispatched"] ? "Queued · waiting for the L2 to start" : "Queued · waiting for a checkpoint" : "Delivery unconfirmed · cannot remove" : undefined}>
        {message.delivery?.removable ? <button type="button" className="link" disabled={readOnly || checking || denied || removal.isPending}
          onClick={() => removal.mutate(message.id)}>{removal.isPending && removal.variables === message.id ? "Removing…" : "Remove"}</button> : null}
        {removal.isError && removal.variables === message.id ? <span role="alert">{removal.error instanceof ApiError && [401, 403].includes(removal.error.status) ? "You do not have permission to remove this message." : removal.error instanceof ApiError && removal.error.status === 409 ? removal.error.message : "Removal unconfirmed. Check this message’s status before trying again."}</span> : null}
      </Bubble> :
        <Reply key={key} text={message.text} at={message.at} role={message.role} from={message.role === "l3" ? "L3" : undefined}><MessageImages project={project} images={message.images} /></Reply>);
    }
  });
  if (pending && !messages.some((message) => message.id === pending.id)) {
    // The stored row keeps this key, so acceptance settles the same bubble in place (SPEC.md §3.6 Sending).
    const day = dayLabel(Date.now());
    if (day !== lastDay) rows.push(<DayDivider key={`day-${day}`} label={day} />);
    rows.push(<Bubble key={pending.id} text={pending.text} at={new Date().toISOString()} pending receipt="Sending…" images={<PendingImages images={pending.images} />} />);
  }
  return (
    <section className="convo" aria-label="Task conversation">
      {task.state === "queued" && task.planned_wait ? <p className="conversation-notice" role="status">{facts.waiting}</p> : null}
      {(readOnly || denied) ? <p className="conversation-notice" role="alert">
        {denied ? "You cannot send messages or answers here." : "Showing saved conversation. Refresh before replying or deciding."}{" "}
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
        setLatest(!following.current);
        updateQuestionVisibility(node);
      }}>
        <div className="convo-col">
          {messages.length === 0 && !pending ? <p className="convo-empty text-muted">{facts.finished ? "No messages on this task." : "No messages yet."}</p> : null}
          {rows}
          {task.review?.history.filter((review) => !messages.some((message) => message.review_id === review.id)).map((review) => <ReviewRow key={review.id} review={review} controls={reviewControls} availability={task.review} onRead={() => { following.current = false; }} />)}
          <ReviewFeedback controls={reviewControls} />
          {live && group ? <div className="conversation-question" data-turn={turn.length ? "operator" : "l2"} tabIndex={-1} ref={(node) => {
            group.questions.forEach((q) => {
              const id = `${q.id}:${q.revision}`;
              if (node) anchors.current.set(id, node); else anchors.current.delete(id);
            });
          }}>
            {turn.length || !(handedBack || group.questions.some((q) => q.response)) ? <>
              <p className="conversation-turn">{turn.length ? turnText : "L3 is answering"}</p>
              <QuestionSet key={group.id} decisions={group.questions} group={group} refreshKey={accessRefresh} chat disabled={readOnly || checking || denied || facts.finished} onDenied={() => setDenied(true)} onRefresh={restoreAccess} />
            </> : <p className="text-meta text-muted" role="status">{task.state === "queued" ? "Sent · waiting for the L2 to start." : "Sent · the L2 has your reply."}</p>}
          </div> : (task.question?.status === "resolved" || task.question?.response) && !facts.finished ? <p className="text-meta text-muted" role="status">{task.state === "running" ? "Work resumed" : task.state === "queued" ? "Waiting for the L2 to start" : "Waiting to resume"}</p>
          : handedBack && !facts.finished && !facts.review ? <p className="text-meta text-muted" role="status">{task.state === "queued" ? "Sent · waiting for the L2 to start." : "Sent · the L2 has your reply."}</p> : null}
          {facts.review ? <div className="conversation-question" data-turn="operator">
            <p className="conversation-turn">Your turn · review before merge</p>
            <ReviewDecision decision={facts.review} repository={facts.repository} chat disabled={readOnly || checking || denied} />
          </div> : null}
          {(task.events?.length ?? 0) > 0 ? <details className="conversation-activity"><summary>Activity &amp; evidence</summary>
            <Link to={`/projects/${project}/tasks/${task.slug}/live${location.search}`} state={location.state} replace>Open live session</Link>
            {task.events?.slice(-20).map((event, i) => <p key={i} className="text-meta text-muted"><Stamp at={event["at"]} className="event-time" /> · <InlineProse text={str(event["reason"]) || str(event["text"]) || str(event["kind"])} /></p>)}
          </details> : null}
          {facts.canMessage && steering.state === "running" && !(task.state === "blocked" && current) ? <TaskActivity activity={task.activity} /> : null}
        </div>
      </div>
      {(latest || (current && questionOffscreen)) ? <div className="conversation-jumps">
        {current && questionOffscreen && turn.length ? <button type="button" className="btn conversation-pill" aria-label={count} onClick={() => jumpTo(current)}>{turn.length} question{turn.length === 1 ? "" : "s"} ↓</button> : null}
        {latest && !(current && questionOffscreen && turn.length && questionAtLatest) ? <button type="button" className="btn" aria-label="Latest messages" onClick={() => { following.current = true; if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; setLatest(false); }}>Latest ↓</button> : null}
      </div> : null}
      </div>
      {facts.canMessage ? <div className="convo-dock">
        <Composer active={active} conversation={`task/${project}/${task.slug}`} value={draft} onChange={setDraft} onSubmit={(...args) => prepareSend()(...args)} prepareSubmit={prepareSend} selection={selection} onEscapeOwnership={onEscapeOwnership}
        imageScope={{ project, task: task.slug, engine: str(task["l2_engine"]) || str(task["engine"]) }}
        ariaLabel="Message the L2" placeholder="Message the L2" disabled={readOnly || denied}
        sendDisabled={["stopping", "stop_unconfirmed"].includes(steering.state)}
        hint={["stopped", "stopping", "stop_unconfirmed"].includes(steering.state) ? "" : task.state !== "queued" && turn.length ? "Replying hands the turn back to the L2." : facts.hint} />
        {steering.state === "stopped" ? <p className="text-meta text-muted">Send a correction to continue this session.</p>
          : ["stopping", "stop_unconfirmed"].includes(steering.state) ? <p className="text-meta text-muted">Keep editing while Stop is confirmed.</p> : null}
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
      {facts.canResume ? <button type="button" className="btn btn-ghost task-action" disabled={actions.pending} onClick={actions.resume}>{actions.pending ? "Resuming…" : "Resume"}</button> : null}
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
    <div className="task-confirm" role="group" aria-label={question}>
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
        <button type="button" className="btn btn-primary" disabled={actions.pending} onClick={actions.run}>
          {actions.pending ? <span className="spinner" aria-hidden /> : null}
          Reject
        </button>
        <button type="button" className="btn btn-ghost" disabled={actions.pending} onClick={() => actions.open("")}>
          Cancel
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
  readOnly,
  checking,
  refresh,
  detailsOpen,
  setDetailsOpen,
}: {
  project: string;
  task: TaskView;
  overview: UseQueryResult<Overview>;
  liveRoute: boolean;
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
  const projectQuery = useProject(project);
  const facts = taskFacts(task, overview.data, project, projectQuery.data?.repository);
  const decision = (task.question_group?.questions ?? (task.question ? [task.question] : []))
    .find((question) => question.status === "open" && !question.response);
  const [draft, setDraft] = useState("");
  const [denied, setDenied] = useState(false);
  const [questionVisit, setQuestionVisit] = useState(0);
  const [pending, setPending] = useState<PendingMessage | null>(null);
  const closeDetails = useCallback(() => setDetailsOpen(false), [setDetailsOpen]);
  const selection = useRef<{ start: number; end: number } | null>(null);
  const [voiceOwnsEscape, setVoiceOwnsEscape] = useState(false);
  const steering = useTaskSteering(project, task, refresh);
  const actions = useTaskActions(project, task.slug);
  const reviewControls = useTaskReview(project, task, refresh, readOnly || checking, closeDetails);
  const resumeError = facts.canResume && actions.error && !actions.confirm
    ? <p className="task-line text-danger" role="alert">Could not resume. Try again.</p> : null;
  // Inline at the panel width, open by default; below it an overlay the operator opens (SPEC.md §2.2 rule).
  const [panelOpen, setPanelOpen] = useState(liveRoute || (panelInline && !new URLSearchParams(location.search).has("question")));
  useEffect(() => {
    if (liveRoute) setPanelOpen(true);
  }, [liveRoute]);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const base = `/projects/${project}/tasks/${task.slug}`;
  const title = task.title || task.slug;
  const detailsButton = <button type="button" className="icon-btn" aria-label="Task details" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>⋯</button>;
  const faultNotice = facts.fault ? <p className="task-line task-fault text-danger" role="status">{facts.fault}</p> : null;
  const details = detailsOpen ? <Overlay label="Task details" side={phone ? "bottom" : "right"} onClose={closeDetails}>
    <div className="task-details">
      <div className="task-details-heading"><h2>Task details</h2><button type="button" className="icon-btn" aria-label="Close task details" onClick={closeDetails}>×</button></div>
      <p className="task-details-title">{title}</p>
      {facts.sub ? <p className="task-sub">{facts.sub}</p> : null}
      <Chips chips={facts.chips} />
      <ReviewMenu task={task} controls={reviewControls} view={(id) => {
        closeDetails();
        const search = new URLSearchParams(location.search);
        search.delete("question"); search.delete("revision"); search.set("review", id);
        void navigate(`${base}?${search}`, { replace: true, state: location.state });
      }} />
      <TokenUsage usage={task.token_usage} running={task.state === "running"} engines={overview.data?.engines} />
      {facts.blockReason ? <section><h3>{facts.label}</h3><p>{facts.blockReason}</p></section> : null}
      {facts.holdReason ? <section><h3>Merge held</h3><p>{facts.holdReason}</p></section> : null}
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
          target?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])") ||
          document.querySelector('[role="dialog"], [role="menu"], [aria-haspopup][aria-expanded="true"], .overlay')) return;
      event.preventDefault();
      steering.stop();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [phone, readOnly, denied, voiceOwnsEscape, actions.confirm, steering]);
  const swipe = useTaskSwipe(phone && !detailsOpen && !voiceOwnsEscape, liveRoute, (live) => {
    void navigate(`${base}${live ? "/live" : ""}${location.search}`, { replace: true, state: location.state });
  });
  const control = <SteeringControls steering={steering} disabled={readOnly || denied} escape={!phone} />;

  // A drag that reveals Live session starts its transcript, so the incoming view loads while it slides in.
  const panel = <ProseScope project={project} repository={projectQuery.data?.repository}><LiveSession project={project} task={task} engineLabel={facts.engineLabel} waiting={facts.waiting} steering={!phone && !panelInline ? steering : undefined} readOnly={readOnly || denied} active={!phone || liveRoute || swipe.dragging} /></ProseScope>;
  const conversation = <ProseScope project={project} repository={projectQuery.data?.repository}><TaskConversation project={project} task={task} facts={facts} readOnly={readOnly} checking={checking} refresh={refresh} draft={draft} setDraft={setDraft} pending={pending} setPending={setPending} steering={steering} active={!phone || !liveRoute} denied={denied} setDenied={setDenied} questionVisit={questionVisit} selection={selection} onEscapeOwnership={setVoiceOwnsEscape} reviewControls={reviewControls} /></ProseScope>;

  if (phone) {
    return (
      <>
      <PhoneHeader overview={overview} onTitleClick={() => setDetailsOpen(true)} titleExpanded={detailsOpen} status={
        <span className="task-state-line" role="status"><span className="dot" data-state={facts.dot} aria-hidden /><span>{facts.label}</span>{facts.holdReason ? <span data-tone="held"> · Merge held</span> : null}</span>
      }>{control}</PhoneHeader>
      <div className="task-page" data-phone>
        {faultNotice}
        <SteeringNotice steering={steering} />
        {!detailsOpen ? resumeError : null}
        {!detailsOpen && actions.error && actions.confirm ? <p className="task-line text-danger" role="alert">Could not {actions.confirm} the task. <button type="button" className="link" onClick={() => setDetailsOpen(true)}>Retry</button></p> : null}
        <nav className="task-tabs" aria-label="Task views">
          <NavLink className="task-tab" to={`${base}${location.search}`} replace state={location.state} end>
            Conversation
          </NavLink>
          <NavLink className="task-tab" to={`${base}/live${location.search}`} replace state={location.state}>
            Live session
          </NavLink>
        </nav>
        <div className="task-views">
          {/* Both views stay laid out; the idle one is invisible until a drag reveals it (SPEC.md §3.10). */}
          <div className="task-track" ref={swipe.track} data-live={liveRoute || undefined}>
            <div className="task-view" style={liveRoute && !swipe.dragging ? idle : undefined}>{conversation}</div>
            <div className="task-view" style={!liveRoute && !swipe.dragging ? idle : undefined}>{panel}</div>
          </div>
        </div>
        {details}
      </div>
      </>
    );
  }

  return (
    <div className="task-page">
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
              aria-label="Live session"
              aria-pressed={panelOpen}
              onClick={() => setPanelOpen((open) => !open)}
            >
              <PanelIcon />
            </button>
          </div>
        </div>
        <Chips chips={facts.chips} />
        <SteeringNotice steering={steering} />
        <ConfirmRow actions={actions} />
        {resumeError}
        {faultNotice}
      </header>
      <div className="task-body">
        <div className="task-main">{conversation}</div>
        {panelOpen ? (
          panelInline ? (
            panel
          ) : (
            <Overlay label="Live session" side="right" onClose={closePanel}>
              {panel}
            </Overlay>
          )
        ) : null}
      </div>
      {details}
    </div>
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
 * composer pinned above the tab bar on the Conversation tab. `/live` selects the Live session tab and
 * opens the desktop panel.
 */
export default function Task() {
  const params = useParams();
  const project = params["name"] ?? "";
  const slug = params["slug"] ?? "";
  const liveRoute = Boolean(useMatch("/projects/:name/tasks/:slug/live"));
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
  } else content = <TaskPage key={`${project}:${slug}`} project={project} task={task.data!} overview={overview} liveRoute={liveRoute} readOnly={task.isError} checking={task.isFetching && !task.isFetchedAfterMount} refresh={() => task.refetch({ throwOnError: true })} detailsOpen={detailsOpen} setDetailsOpen={setDetailsOpen} />;
  return <>{header}{content}</>;
}
