import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, taskReview } from "../data/api";
import type { Review, ReviewAction, ReviewSubject, TaskView } from "../data/api";
import { InlineProse } from "./Prose";
import "./task-review.css";

export function reviewStatus(review: Review): string {
  const engine = review.engine_label || "Independent";
  const count = review.result?.findings.length;
  switch (review.state) {
    case "requested": return `${review.requested_by === "l2" ? "L2" : "You"} requested ${review.subject} review · waiting for L2`;
    case "running": return `${engine} is reviewing ${review.subject}`;
    case "completed": return `${engine} ${review.subject} review complete · ${count == null ? "result unavailable" : count ? `${count} ${count === 1 ? "finding" : "findings"}` : "no findings"}${review.reconciled ? "" : " · awaiting L2"}`;
    case "failed": return `${review.subject === "proposal" ? "Proposal" : "Changes"} review failed · request still needs a decision`;
    case "cancelled": return "Review cancelled · request still needs a decision";
    case "withdrawn": return "Review request withdrawn";
  }
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
    mutationFn: (input: { action: ReviewAction; review?: Review; reason?: string; subject?: ReviewSubject }) => taskReview({ project, slug: task.slug, action: input.action,
      ...(input.subject ? { subject: input.subject } : {}),
      ...(["request", "retry", "rerun"].includes(input.action) ? { request_id: crypto.randomUUID().replaceAll("-", "") } : {}),
      ...(input.review ? { review_id: input.review.id } : {}), ...(input.reason ? { reason: input.reason } : {}),
    }),
    onSuccess: async (review, input) => {
      await client.cancelQueries({ queryKey: ["task", project, task.slug] });
      client.setQueryData<TaskView>(["task", project, task.slug], (cached) => cached?.review && !cached.review.history.some((entry) => entry.id === review.id) ? { ...cached, review: { ...cached.review,
        latest: review, history: [...cached.review.history.filter((entry) => entry.id !== review.id), review],
        subjects: { ...cached.review.subjects, [review.subject]: { ...cached.review.subjects[review.subject], latest: review } },
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
    run: (action: ReviewAction, review?: Review, reason?: string, subject?: ReviewSubject) => { setError(""); mutation.mutate({ action, review, reason, subject }); },
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

export function ReviewMenu({ task, controls, view }: { task: TaskView; controls: ReviewControls; view: (id: string) => void }) {
  const review = task.review;
  if (!review) return null;
  return <section className="task-review-menu" aria-label="Adversarial review">
    {(["proposal", "changes"] as const).map((subject) => {
      const { latest, available, why } = review.subjects[subject];
      const status = latest ? latest.state === "completed" && latest.coverage === "earlier" ? "Earlier version" : ({ requested: "Requested", running: "In progress", completed: "Complete", failed: "Failed", cancelled: "Cancelled", withdrawn: "Withdrawn" })[latest.state] : "Not reviewed";
      return <div key={subject}>
        <button className="btn btn-ghost" disabled={!latest && (controls.disabled || !available)} onClick={() => latest ? view(latest.id) : controls.run("request", undefined, undefined, subject)}>{latest ? `View ${subject} review` : `Review ${subject}`}</button>
        <p className="text-meta text-muted">{latest ? status : available ? status : why}</p>
      </div>;
    })}
    <p className="text-meta text-muted">{[review.engine_label, review.model, review.same_engine ? "Separate same-engine reviewer." : "Independent reviewer."].filter(Boolean).join(" · ")} {review.fallback_reason}</p>
    <p className="text-meta text-muted">Focused, read-only review. Uses the configured allowance{review.allowance_known ? "." : "; remaining allowance is unknown."} Review grants no approval.</p>
    <ReviewFeedback controls={controls} />
  </section>;
}

export function ReviewRow({ review, controls, availability, onRead }: { review: Review; controls: ReviewControls; availability: TaskView["review"]; onRead: () => void }) {
  const [withdraw, setWithdraw] = useState(false);
  const [reason, setReason] = useState("");
  const details = useRef<HTMLDetailsElement>(null);
  const coverage = review.subject === "proposal" ? (review.coverage === "assessed" ? "Reviewed an earlier proposal; L2 assessed the later version." : review.coverage === "earlier" ? "The proposal or context changed. L2 still needs to assess the later version." : review.coverage === "unknown" ? "Current proposal coverage is unknown." : "Review covers the captured proposal.") : review.coverage === "assessed" ? "Reviewed an earlier revision; L2 assessed the later edits."
    : review.coverage === "earlier" ? "Work changed after review. L2 still needs to assess the later edits before merging."
    : review.coverage === "unknown" ? "Current revision coverage is unknown."
    : "Review covers the current revision.";
  return <div className="task-review-row" id={`review-${review.id}`} data-review-id={review.id} tabIndex={-1}>
    <p className="task-review-status">{reviewStatus(review)}</p>
    {review.state === "completed" ? <p className="task-review-coverage">{coverage}</p> : null}
    {review.subject === "proposal" ? <p className="task-review-coverage">Implementation is not reviewed by this proposal review. Review grants no implementation approval.</p> : null}
    {review.snapshot?.proposal ? <p><a href={`#review-${review.id}-proposal`} onClick={() => { onRead(); if (details.current) details.current.open = true; }}>View captured proposal</a></p> : null}
    <details ref={details} onToggle={(event) => { if (!event.currentTarget.open) setWithdraw(false); }}>
      <summary onClick={onRead}>Review details</summary>
      <div className="task-review-detail">
        <p>Requested by {review.requested_by === "l2" ? "L2" : "you"}. {[review.engine_label, review.model].filter(Boolean).join(" · ")}</p>
        <p>{review.same_engine ? "Separate same-engine reviewer." : "Cross-engine reviewer."} {review.fallback_reason}</p>
        <p>Focused, read-only review. Uses the configured allowance{review.allowance_known ? "." : "; remaining allowance was unknown when requested."} Review does not approve merging or release other holds.</p>
        {review.focus ? <p><strong>Focus:</strong> <InlineProse text={review.focus} /></p> : null}
        {review.snapshot?.proposal ? <div id={`review-${review.id}-proposal`}><p><strong>Captured proposal</strong> · {review.snapshot.proposal.at}</p><p><InlineProse text={review.snapshot.proposal.text} /></p></div> : null}
        {review.error ? <p className="text-danger"><InlineProse text={review.error} /></p> : null}
        {review.result?.text ? <p><InlineProse text={review.result.text} /></p> : null}
        {review.result?.findings.map((finding) => {
          const disposition = review.dispositions.find((entry) => entry.finding_id === finding.id);
          return <div className="task-review-finding" key={finding.id}>
            <p><strong>{finding.severity} · {finding.title}</strong></p><p><InlineProse text={finding.body} /></p>
            {finding.path ? <p className="text-meta"><InlineProse text={`${finding.path}${finding.line ? `:${finding.line}` : ""}`} /></p> : null}
            <p>{disposition ? <><strong>L2 — {disposition.disposition}:</strong> <InlineProse text={disposition.reason} /></> : "Awaiting L2’s response."}</p>
          </div>;
        })}
        {review.result?.limitations?.length ? <p><strong>Limitations:</strong> <InlineProse text={review.result.limitations.join("\n")} /></p> : null}
        {review.snapshot ? <p className="text-meta task-review-evidence">Reviewed head: {review.snapshot.head}<br />Base: {review.snapshot.base}<br />Tree: {review.snapshot.tree}<br />Context: {review.snapshot.context_hash}</p> : null}
        {review.snapshot?.captured_at ? <p className="text-meta task-review-evidence">Captured: {review.snapshot.captured_at}<br />Selected messages: {review.snapshot.context_ids?.join(", ") || "None"}<br />Inputs: {review.snapshot.input_hash}</p> : null}
        {review.snapshot?.selected_owner_evidence ? <p className="text-meta">Selected L2 evidence; all operator and coordinator messages included.</p> : null}
        {review.snapshot?.captured_context_hash ? <p className="text-meta task-review-evidence">Captured context: {review.snapshot.captured_context_hash}</p> : null}
        {review.snapshot?.limitations?.length ? <p className="text-meta"><strong>Capture limitations:</strong> <InlineProse text={review.snapshot.limitations.join("\n")} /></p> : null}
        {review.reconciled ? <p className="text-meta task-review-evidence">L2 assessed head: {review.reconciled.head}<br />Base: {review.reconciled.base}<br />Tree: {review.reconciled.tree}<br />Context: {review.reconciled.context_hash}<br /><InlineProse text={review.reconciled.reason} /></p> : null}
        {(review.can_retry || review.can_review_latest || review.can_review_again) && availability ? <p className="text-meta">Next review: {[availability.engine_label, availability.model].filter(Boolean).join(" · ")}. {availability.same_engine ? "Separate same-engine reviewer. " : ""}{availability.fallback_reason} Focused, read-only review. Uses the configured allowance{availability.allowance_known ? "." : "; remaining allowance is unknown."}</p> : null}
        <div className="task-review-actions">
          {review.can_cancel ? <button className="link" disabled={controls.disabled} onClick={() => controls.run("cancel", review)}>Cancel review</button> : null}
          {review.can_retry ? <button className="link" disabled={controls.disabled} onClick={() => controls.run("retry", review)}>Retry review</button> : null}
          {review.can_review_latest ? <button className="link" disabled={controls.disabled} onClick={() => controls.run("rerun", review)}>Review latest</button> : null}
          {review.can_review_again ? <button className="link" disabled={controls.disabled} onClick={() => controls.run("rerun", review)}>Review again</button> : null}
          {review.can_withdraw && !withdraw ? <button className="link" disabled={controls.disabled} onClick={() => setWithdraw(true)}>Skip review…</button> : null}
        </div>
        {withdraw && review.can_withdraw ? <div role="group" aria-label="Skip requested review">
          <p>Skip this requested review? Existing checks and merge holds still apply.</p>
          <input className="field" aria-label="Reason for skipping review" placeholder="Why skip this review?" value={reason} onChange={(event) => setReason(event.target.value)} />
          <div className="task-review-actions"><button className="link" disabled={controls.disabled || !reason.trim()} onClick={() => controls.run("withdraw", review, reason.trim())}>Skip review</button><button className="link" disabled={controls.pending} onClick={() => setWithdraw(false)}>Keep review</button></div>
        </div> : null}
      </div>
    </details>
  </div>;
}
