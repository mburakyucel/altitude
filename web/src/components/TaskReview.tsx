import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, taskReview } from "../data/api";
import type { Review, ReviewAction, ReviewSubject, TaskView } from "../data/api";
import { InlineProse } from "./Prose";
import "./task-review.css";

const NAME: Record<ReviewSubject, string> = { proposal: "Proposal review", changes: "Implementation review" };
const SUBJECTS = ["proposal", "changes"] as const;
type Tone = "none" | "wait" | "run" | "ok" | "warn" | "bad";
const ICON: Record<Tone, React.ReactNode> = {
  none: <circle cx="12" cy="12" r="8.5" strokeDasharray="2.5 3" />,
  wait: <><circle cx="12" cy="12" r="8.5" /><path d="M12 8v4l2.5 2" /></>,
  run: <path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5" />,
  ok: <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12.2l2.4 2.4 4.6-5" /></>,
  warn: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5v5.5M12 16.3v.2" /></>,
  bad: <><circle cx="12" cy="12" r="8.5" /><path d="M9 9l6 6M15 9l-6 6" /></>,
};
const who = (role: string | null | undefined) => role === "l2" ? "L2" : "you";

function tone(review: Review): Tone {
  switch (review.state) {
    case "requested": case "withdrawn": return "wait";
    case "running": return "run";
    case "failed": case "cancelled": return "bad";
    case "completed": return !review.reconciled ? (review.result?.findings.length ? "warn" : "ok") : review.unresolved.length ? "warn" : "ok";
  }
}

function stateLabel(review: Review): string {
  switch (review.state) {
    case "requested": return `requested by ${who(review.requested_by)}`;
    case "running": return review.cancel_requested ? "stopping" : "in progress";
    case "completed": return "done";
    case "failed": case "cancelled": return "didn’t finish";
    case "withdrawn": return "skipped";
  }
}

/** The one sentence under the name: the reviewer's verdict, or what the review is waiting for. */
function sentence(review: Review): string {
  switch (review.state) {
    case "requested":
      if (review.waiting === "reviewer") return "Waiting for the reviewer: another review is running on this machine.";
      if (review.waiting === "resume") return "Queued. L2 starts it when it resumes.";
      return review.requested_by === "l2" ? `L2 asked for a review of its ${review.subject === "proposal" ? "proposal" : "implementation"} and starts it shortly.` : "Queued. L2 starts it after its current step.";
    case "running": return review.cancel_requested ? "Stopping the reviewer…" : `Reviewing the ${review.subject === "proposal" ? "proposal" : "implementation"}…`;
    case "completed": return review.result?.text ?? "";
    case "failed": return review.error || "The reviewer stopped without a result.";
    case "cancelled": return "Stopped before it finished.";
    case "withdrawn": return "";
  }
}

function counts(review: Review, iteration: number): React.ReactNode[] {
  const parts: React.ReactNode[] = iteration > 1 ? [`Review ${iteration}`] : [];
  if (review.state === "running") parts.push(`Requested by ${who(review.requested_by)}`);
  if (review.state === "withdrawn") parts.push(`Skipped by ${who(review.withdrawn_by)}`);
  if (review.state === "completed") {
    const total = review.result?.findings.length ?? 0;
    const open = review.unresolved.length;
    parts.push(total ? `${total} ${total === 1 ? "finding" : "findings"}` : "No findings");
    if (!review.reconciled && total) parts.push("L2 is responding");
    else if (open) parts.push(<span key="open" className="review-open">{open} open, blocks merge</span>);
    else if (total) parts[parts.length - 1] += total === 1 ? ", resolved" : total === 2 ? ", both resolved" : ", all resolved";
    if (review.earlier) parts.push("earlier version");
  }
  return parts;
}

function Dots({ parts }: { parts: React.ReactNode[] }) {
  return <>{parts.map((part, index) => <span key={index}>{index ? " · " : ""}{part}</span>)}</>;
}

function Summary({ review, iteration, name, action, extra, empty, footer, held }: { review: Review | null; iteration: number; name: string; action?: React.ReactNode; extra?: React.ReactNode; empty?: string; footer?: React.ReactNode; held?: boolean }) {
  const kind = !review ? "none" : held && tone(review) === "ok" ? "warn" : tone(review);
  const meta = review ? counts(review, iteration) : [];
  return <>
    <span className={`review-icon review-icon-${kind}`} aria-hidden><svg viewBox="0 0 24 24">{ICON[kind]}</svg></span>
    <div className="review-main">
      <div className="review-top">
        <p className="review-heading"><span className="review-name">{name}</span>{review ? <span className="review-state"> · {stateLabel(review)}</span> : null}</p>
        {action}
      </div>
      {review && sentence(review) ? <p className="review-sentence"><InlineProse text={sentence(review)} /></p> : null}
      {empty ? <p className="review-meta">{empty}</p> : null}
      {meta.length || extra ? <p className="review-meta"><Dots parts={meta} />{meta.length && extra ? " · " : ""}{extra}</p> : null}
      {footer}
    </div>
  </>;
}

export function useTaskReview(project: string, task: TaskView, refresh: () => Promise<unknown>, disabled: boolean, onRequested: () => void) {
  const client = useQueryClient();
  const [uncertain, setUncertain] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const check = async () => {
    setChecking(true);
    try { await refresh(); setUncertain(false); setError(""); }
    catch { setError("Review request unconfirmed. Refresh its saved status before trying again."); }
    finally { setChecking(false); }
  };
  const mutation = useMutation({
    mutationFn: (input: { action: ReviewAction; review?: Review; subject?: ReviewSubject }) => taskReview({ project, slug: task.slug, action: input.action,
      ...(input.subject ? { subject: input.subject } : {}),
      ...(input.action === "request" ? { request_id: crypto.randomUUID().replaceAll("-", "") } : {}),
      ...(input.review ? { review_id: input.review.id } : {}),
    }),
    onSuccess: async (review, input) => {
      await client.cancelQueries({ queryKey: ["task", project, task.slug] });
      client.setQueryData<TaskView>(["task", project, task.slug], (cached) => cached?.review && !cached.review.history.some((entry) => entry.id === review.id) ? { ...cached, review: { ...cached.review,
        history: [...cached.review.history, review],
        subjects: { ...cached.review.subjects, [review.subject]: { open: 0, ...cached.review.subjects[review.subject], available: false, why: "", latest: review } },
      } } : cached);
      setError("");
      if (input.action === "request" && mounted.current) onRequested();
      await client.invalidateQueries({ queryKey: ["task", project, task.slug] });
    },
    onError: async (failure) => {
      if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500) {
        setError(failure.status === 403 || failure.status === 401 ? "You do not have permission to request or change this review." : failure.message);
        await client.invalidateQueries({ queryKey: ["task", project, task.slug] });
      } else {
        setUncertain(true);
        setError("Review request unconfirmed. Checking its saved status before trying again.");
        await check();
      }
    },
  });
  return { disabled: disabled || mutation.isPending || checking || uncertain, pending: mutation.isPending,
    error, uncertain, checking, check,
    run: (action: ReviewAction, review?: Review, subject?: ReviewSubject) => { setError(""); mutation.mutate({ action, review, subject }); },
  };
}
export type ReviewControls = ReturnType<typeof useTaskReview>;

export function ReviewFeedback({ controls }: { controls: ReviewControls }) {
  if (!controls.error && !controls.pending) return null;
  return <p className="text-meta task-review-feedback" role={controls.error ? "alert" : "status"}>
    {controls.error || "Saving review request…"}{" "}
    {controls.uncertain ? <button type="button" className="link" disabled={controls.checking} onClick={() => void controls.check()}>{controls.checking ? "Checking…" : "Refresh review status"}</button> : null}
  </p>;
}

const iterations = (task: TaskView, subject: ReviewSubject) => task.review?.history.filter((entry) => entry.subject === subject) ?? [];

/** Open findings an earlier review of the kind still holds in the merge gate, e.g. under an additional review. */
const earlierOpen = (task: TaskView, review: Review) =>
  (task.review?.subjects[review.subject]?.open ?? 0) - (review.state === "withdrawn" ? 0 : review.unresolved.length);
const earlierNote = (open: number) => open > 0 ? <span className="review-open">{open} open in an earlier review, blocks merge</span> : null;
const join = (...parts: React.ReactNode[]) => parts.filter(Boolean).map((part, index) => <span key={index}>{index ? " · " : ""}{part}</span>);

/** Task details: one box per kind with its latest review and the one button that can start the next. */
export function ReviewBoxes({ task, controls, view }: { task: TaskView; controls: ReviewControls; view: (id: string) => void }) {
  const subjects = task.review?.subjects;
  if (!subjects) return null;
  return <>{SUBJECTS.map((subject) => {
    const entry = subjects[subject];
    if (!entry) return null;
    const latest = entry.latest;
    const label = !latest || latest.state === "withdrawn" ? "Request" : latest.state === "completed" ? "Review again" : "Try again";
    return <section key={subject} className="review-box" aria-label={NAME[subject]}>
      <Summary review={latest} iteration={iterations(task, subject).length} name={NAME[subject]}
        empty={latest ? undefined : entry.available ? "Not reviewed yet" : entry.why}
        held={latest ? earlierOpen(task, latest) > 0 : false}
        extra={latest ? join(earlierNote(earlierOpen(task, latest)), <button type="button" className="link" onClick={() => view(latest.id)}>View</button>) : null}
        footer={entry.available ? <button type="button" className="btn review-box-action" disabled={controls.disabled}
          onClick={() => controls.run("request", latest ?? undefined, subject)}>{label}</button> : null} />
    </section>;
  })}<ReviewFeedback controls={controls} /></>;
}

/** Findings, L2's answers and the reviewer footer with Technical details and Skip review. */
function ReviewBody({ review, controls, children }: { review: Review; controls: ReviewControls; children?: React.ReactNode }) {
  const [technical, setTechnical] = useState(false);
  const [skip, setSkip] = useState(false);
  return <>
    {review.result?.findings.map((finding) => {
      const disposition = review.dispositions.find((entry) => entry.finding_id === finding.id)?.disposition;
      const reason = review.dispositions.find((entry) => entry.finding_id === finding.id)?.reason;
      return <div className="review-finding" key={finding.id}>
        <p className="review-finding-title"><span className={`review-status review-status-${disposition ?? "new"}`}>{disposition === "open" ? "Open" : disposition === "fixed" ? "Fixed" : disposition === "dismissed" ? "Dismissed" : "New"}</span>
          <span><strong>{finding.title}</strong> <span className="text-muted">· {finding.severity}</span></span></p>
        <div className="review-finding-body">
          <p><InlineProse text={finding.body} /></p>
          {finding.path ? <p className="text-meta"><InlineProse text={`${finding.path}${finding.line ? `:${finding.line}` : ""}`} /></p> : null}
          {reason ? <p className="review-answer"><strong>L2:</strong> <InlineProse text={reason} /></p> : null}
        </div>
      </div>;
    })}
    {review.result?.limitations?.length ? <p className="review-note"><strong>Not covered:</strong> <InlineProse text={review.result.limitations.join("\n")} /></p> : null}
    {review.state === "withdrawn" && review.withdrawal_reason ? <p className="review-note"><InlineProse text={review.withdrawal_reason} /></p> : null}
    {children}
    <p className="review-foot">
      {review.engine_label ? <span>{review.engine_label} · {review.same_engine ? "same engine as the task" : "alternate engine"}</span> : null}
      <button type="button" className="link" aria-expanded={technical} onClick={() => setTechnical(!technical)}>Technical details</button>
      {review.can_withdraw && !skip ? <button type="button" className="link" disabled={controls.disabled} onClick={() => setSkip(true)}>Skip review</button> : null}
    </p>
    {skip && review.can_withdraw ? <div className="review-skip" role="group" aria-label="Skip review">
      <p>Skip this review? It no longer blocks merging and its findings stay visible. Checks and merge holds still apply.</p>
      <div className="review-foot"><button type="button" className="btn" disabled={controls.disabled} onClick={() => controls.run("withdraw", review)}>Skip review</button>
        <button type="button" className="link" disabled={controls.pending} onClick={() => setSkip(false)}>Keep review</button></div>
    </div> : null}
    {technical ? <div className="review-technical text-meta">
      <p>{[review.engine_label, review.model].filter(Boolean).join(" · ")}{review.fallback_reason ? ` · ${review.fallback_reason}` : ""}</p>
      <p>Requested by {who(review.requested_by)}{review.focus ? <>. Focus: <InlineProse text={review.focus} /></> : "."}</p>
      {review.snapshot?.proposal ? <div><p><strong>Reviewed proposal</strong> · {review.snapshot.proposal.at}</p><p><InlineProse text={review.snapshot.proposal.text} /></p></div> : null}
      {review.snapshot ? <p className="review-evidence">Reviewed head {review.snapshot.head} · base {review.snapshot.base} · tree {review.snapshot.tree} · context {review.snapshot.context_hash}{review.snapshot.input_hash ? ` · inputs ${review.snapshot.input_hash}` : ""}</p> : null}
      {review.snapshot?.selected_owner_evidence ? <p>Selected L2 evidence: {review.snapshot.context_ids?.join(", ") || "none"}; every operator and coordinator message included.</p> : null}
      {review.snapshot?.limitations?.length ? <p>Capture limits: <InlineProse text={review.snapshot.limitations.join("\n")} /></p> : null}
      {review.reconciled ? <p className="review-evidence">L2 assessed head {review.reconciled.head} · base {review.reconciled.base} · context {review.reconciled.context_hash}. <InlineProse text={review.reconciled.reason} /></p> : null}
    </div> : null}
  </>;
}

/** An earlier iteration: one line that opens to its own findings and actions, since it can still block merge. */
function EarlierReview({ review, iteration, controls }: { review: Review; iteration: number; controls: ReviewControls }) {
  const [open, setOpen] = useState(false);
  const body = useId();
  return <div className="review-earlier-entry">
    <button type="button" className="review-earlier-row" aria-expanded={open} aria-controls={body} onClick={() => setOpen(!open)}>
      <svg aria-hidden viewBox="0 0 24 24"><path d={open ? "M6 9l6 6 6-6" : "M9 6l6 6-6 6"} /></svg><b>Review {iteration}</b><span>{sentence(review) ? <InlineProse text={sentence(review)} /> : null}{sentence(review) ? " · " : ""}<Dots parts={counts(review, 1)} /></span>
    </button>
    {open ? <div className="review-earlier-body" id={body}><ReviewBody review={review} controls={controls} /></div> : null}
  </div>;
}

/** The conversation card for a kind's latest review; earlier iterations fold inside it. */
export function ReviewCard({ review, task, controls, target, onRead }: { review: Review; task: TaskView; controls: ReviewControls; target: boolean; onRead: () => void }) {
  const [open, setOpen] = useState(target);
  const body = useId();
  useEffect(() => { if (target) setOpen(true); }, [target]);
  const all = iterations(task, review.subject);
  const earlier = all.slice(0, all.findIndex((entry) => entry.id === review.id));
  const action = review.can_cancel ? <button type="button" className="link" disabled={controls.disabled} onClick={() => controls.run("cancel", review)}>Stop</button>
    : review.can_again && (review.state === "failed" || review.state === "cancelled") ? <button type="button" className="link" disabled={controls.disabled} onClick={() => controls.run("request", review, review.subject)}>Try again</button> : null;
  const toggle = <button type="button" className="review-toggle" aria-expanded={open} aria-controls={body} aria-label={`${open ? "Hide" : "Show"} ${NAME[review.subject].toLowerCase()} details`}
    onClick={() => { onRead(); setOpen(!open); }}>
    <svg aria-hidden viewBox="0 0 24 24"><path d={open ? "M6 9l6 6 6-6" : "M9 6l6 6-6 6"} /></svg></button>;
  return <div className="review-card" id={`review-${review.id}`} data-review-id={review.id} tabIndex={-1}>
    <div className="review-row"><Summary review={review} iteration={all.length} name={NAME[review.subject]} held={earlierOpen(task, review) > 0} extra={earlierNote(earlierOpen(task, review))} action={<span className="review-actions">{action}{toggle}</span>} /></div>
    {open ? <div className="review-body" id={body}>
      <ReviewBody review={review} controls={controls}>
        {earlier.length ? <div className="review-earlier"><p className="review-note">Earlier reviews</p>
          {earlier.map((entry, index) => <EarlierReview key={entry.id} review={entry} iteration={index + 1} controls={controls} />)}
        </div> : null}
      </ReviewBody>
    </div> : null}
  </div>;
}

/** The latest review of each kind, the only ones the conversation shows. */
export function latestReviews(task: TaskView): Set<string> {
  return new Set(SUBJECTS.map((subject) => task.review?.subjects[subject]?.latest?.id).filter((id): id is string => Boolean(id)));
}
