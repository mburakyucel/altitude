import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, sendL2Message, useDecide, useProject } from "../data/api";
import type { DecideInput, Decision, QuestionAnswer, QuestionGroup } from "../data/api";
import { useToast } from "../data/Toast";
import { decisionKind, questionPath } from "../data/decisions";
import { ageText, exactTime } from "../data/observed";
import { setSelectedProject } from "../shell/scope";
import { InlineProse, Prose, ProseScope, QuestionProse } from "./Prose";

type Option = { key: string; label: string; text: string };
type Draft = { option?: string; text?: string };
const draftKey = (q: Decision) => `${q.project}:${q.slug}:${q.id}:${q.revision}`;
function optionsFor(question: Decision): Option[] {
  if (question.options) return question.options;
  const recommended = question.recommendation;
  return recommended?.text ? [{ key: "recommended", label: recommended.label || "Accept", text: recommended.text }] : [];
}
function recommendedKey(question: Decision) {
  return question.recommended_key ?? (!question.options && question.recommendation?.text ? "recommended" : null);
}

type QuestionProps = {
  disabled?: boolean; onDenied?: () => void; onRefresh?: () => void;
  refreshKey?: number;
  chat?: boolean; from?: "needs" | "project";
};

/** Preset and custom answers share one conversational handoff in Needs you and chat. */
export function QuestionSet({ decisions, group, disabled = false, onDenied, onRefresh, refreshKey, chat = false, from = "project" }: QuestionProps & {
  decisions: Decision[]; group?: QuestionGroup | null;
}) {
  const decide = useDecide();
  useEffect(() => { decide.reset(); }, [refreshKey]);
  useEffect(() => {
    if (!decide.isError || !decide.variables) return;
    const input = decide.variables;
    const answers = "answers" in input ? input.answers : [input];
    if (answers.every((answer) => {
      const question = decisions.find((q) => q.project === input.project && q.slug === input.slug && q.id === answer.question_id && q.revision === answer.revision);
      if (!question?.response) return false;
      const text = "text" in answer ? answer.text : optionsFor(question).find((option) => option.key === answer.option_key)?.text;
      return question.response.text === text;
    })) decide.reset();
  }, [decisions, decide.isError, decide.variables]);
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const open = decisions.filter((q) => q.id && q.revision != null && q.status !== "resolved" && q.audience !== "l3" && !q.response);
  const signature = open.map(draftKey).join("|");
  useEffect(() => {
    const current = new Set(signature.split("|"));
    setDrafts((old) => Object.fromEntries(Object.entries(old).filter(([key]) => current.has(key))));
  }, [signature]);
  const first = decisions[0];
  if (!first) return null;
  const grouped = decisions.length > 1;
  const shown = chat ? decisions : decisions.filter((q) => q.status !== "resolved");
  const denied = decide.error instanceof ApiError && [401, 403].includes(decide.error.status);
  const stale = decide.error instanceof ApiError && decide.error.status === 409;
  const groupId = group?.id ?? first.group_id;
  const groupRevision = group?.revision ?? first.group_revision;
  const unavailable = disabled || denied || stale || decide.isPending || (grouped && (!groupId || groupRevision == null));
  const answer = (q: Decision, key: string): QuestionAnswer => ({ question_id: q.id!, revision: q.revision!, option_key: key });
  const selected: QuestionAnswer[] = open.flatMap((q) => {
    const draft = drafts[draftKey(q)];
    if (!q.id || q.revision == null || !draft) return [];
    if (draft.option) return [answer(q, draft.option)];
    return draft.text?.trim() ? [{ question_id: q.id, revision: q.revision, text: draft.text.trim() }] : [];
  });
  const perform = (input: DecideInput) => {
    decide.mutate(input, {
      onSuccess: () => {
        const sent = "answers" in input ? input.answers : [input];
        setDrafts((old) => Object.fromEntries(Object.entries(old).filter(([key]) => !sent.some((a) => key === draftKey({ ...first, id: a.question_id, revision: a.revision })))));
        if (!chat) toast.show({ message: "Sent to L2", action: { label: "Open L2 chat", onClick: () => {
          setSelectedProject(first.project);
          navigate(questionPath(first), { state: { from, tab: from === "needs" ? "needs" : "work" } });
        } } });
      },
      onError: (error) => { if (error instanceof ApiError && [401, 403].includes(error.status)) onDenied?.(); },
    });
  };
  const submit = (answers: QuestionAnswer[]) => {
    if (!answers.length || unavailable) return;
    const single = answers[0]!;
    if (groupId && groupRevision != null) {
      perform({ project: first.project, slug: first.slug, group_id: groupId, group_revision: groupRevision, answers });
    } else perform({ project: first.project, slug: first.slug, ...single });
  };
  const edit = (question: Decision, draft: Draft) => {
    decide.reset();
    setDrafts((old) => ({ ...old, [draftKey(question)]: draft }));
  };
  const refresh = () => {
    decide.reset();
    onRefresh?.();
    for (const queryKey of [["overview"], ["project", first.project], ["task", first.project, first.slug]]) void queryClient.invalidateQueries({ queryKey });
  };
  return (
    <div className="question-set" data-group-id={group?.id ?? first.group_id} data-grouped={grouped || undefined}>
      {decisions.length > 1 ? <p className="text-meta text-muted">{open.length ? `${open.length} question${open.length === 1 ? "" : "s"} to answer` : decisions.some((q) => q.response && q.status !== "resolved") ? "Responses sent to L2" : "Questions closed"}</p> : null}
      {shown.map((question) => {
        const resolved = question.status === "resolved";
        const withdrawn = question.resolution?.disposition === "withdrawn";
        const options = optionsFor(question);
        const draft = drafts[draftKey(question)];
        const custom = !options.length || draft?.text !== undefined;
        const actionable = open.includes(question);
        const inputDisabled = unavailable || !question.id || question.revision == null;
        const recommendation = <>
          {question.recommendation?.text ? <p className="decision-approach"><b>Recommended:</b> <InlineProse text={question.recommendation.text} /></p> : null}
          {question.recommendation?.why ? <p className="decision-why"><InlineProse text={question.recommendation.why} /></p> : null}
        </>;
        const content = <>
          <QuestionProse className="decision-question" text={question.question || question.title || question.slug} />
          {resolved ? <div className="decision-receipt" role="status">
            {!withdrawn ? <b>{question.resolution?.disposition === "answered" ? "Decision recorded" : "Question closed"}</b> : null}
            {question.resolution ? <><p><InlineProse text={question.resolution.text} /></p><span className="text-meta text-muted" title={exactTime(question.resolution.at)}>{question.resolution.by} · {ageText(question.resolution.at)}</span></> : null}
          </div> : null}
          {!resolved && question.response ? <div className="decision-receipt" role="status">
            <b>Sent to L2</b><p><InlineProse text={question.response.text} /></p>
            <span className="text-meta text-muted" title={exactTime(question.response.at)}>{ageText(question.response.at)}</span>
          </div> : null}
          {question.design_url ? <a className="text-meta" href={question.design_url} target="_blank" rel="noopener noreferrer">View preview · v{question.revision}</a> : null}
          {(resolved && !withdrawn || question.response) && question.recommendation?.text ? <details className="question-context"><summary>Earlier recommendation</summary>{recommendation}</details> : recommendation}
          {chat && question.detail && question.detail !== question.question ? <details className="question-context">
            <summary>More context</summary>
            <Prose text={question.detail} />
          </details> : null}
          {actionable && options.length ? <div className="decision-options" role="group" aria-label={question.question || "Quick answers"}>
            {options.map((option) => {
              // The recommendation is marked on its own choice; only the operator's pick is pressed.
              const recommended = option.key === recommendedKey(question);
              return <button key={option.key} className="btn btn-ghost" type="button" data-recommended={recommended || undefined}
                aria-pressed={draft?.option === option.key} aria-description={recommended ? "Recommended" : undefined} title={recommended ? "Recommended" : undefined} disabled={inputDisabled}
                onClick={() => edit(question, { option: draft?.option === option.key ? undefined : option.key })}>
                {option.label}{recommended ? <span className="option-recommended" aria-hidden="true">★</span> : null}
              </button>;
            })}
            <button className="btn btn-ghost" type="button" aria-pressed={custom} disabled={inputDisabled}
              onClick={() => edit(question, custom ? {} : { text: "" })}>Other…</button>
          </div> : null}
          {actionable && custom ? <textarea className="question-answer" rows={2} aria-label={`Your answer to: ${question.question || question.title || question.slug}`}
            placeholder="Your answer or a follow-up question…" value={draft?.text ?? ""} disabled={inputDisabled}
            autoFocus={options.length > 0} onChange={(event) => edit(question, { text: event.target.value })} /> : null}
        </>;
        return <div key={`${question.id}:${question.revision}`} className="question-body" data-question-id={question.id ?? undefined} data-question-revision={question.revision ?? undefined} data-status={question.status}>
          {withdrawn ? <details className="question-history">
            <summary>Question withdrawn</summary>
            <div className="question-body">{content}</div>
          </details> : content}
        </div>;
      })}
      {open.length ? <div className="question-batch">
        <button type="button" className="btn btn-primary" disabled={unavailable || !selected.length} onClick={() => submit(selected)}>{decide.isPending ? "Sending…" : selected.length ? `Send ${selected.length} answer${selected.length === 1 ? "" : "s"}` : "Send answers"}</button>
        <p className="text-meta text-muted">{selected.length ? "Only these answers will be sent. You can answer the rest later." : "Choose an answer or write your own. Follow-up questions are welcome."}</p>
      </div> : null}
      {decide.isError ? <p className="text-meta text-danger" role="alert">
        {denied ? "You cannot send answers here. Refresh after access is restored." : stale ? "These questions have changed. Refresh and review the current choices." : "Could not send answers. Your responses are kept here."}{" "}
        {denied || stale ? (!denied || !onDenied ? <button type="button" className="link" onClick={refresh}>Refresh</button> : null) : <button type="button" className="link" onClick={() => decide.variables && perform(decide.variables)} disabled={disabled || decide.isPending}>Retry</button>}
      </p> : null}
    </div>
  );
}

export function Question({ decision, ...props }: QuestionProps & { decision: Decision }) {
  return <QuestionSet decisions={[decision]} {...props} />;
}

/** #419: a held review-ready PR asks for review. Approve sends the operator's own message; the L2 merges with it. */
export function ReviewDecision({ decision, repository, disabled = false, chat = false }: {
  decision: Decision; repository?: string | null; disabled?: boolean; chat?: boolean;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const approve = useMutation({
    mutationFn: () => sendL2Message({ project: decision.project, slug: decision.slug, text: `Approved: merge PR #${decision.pr}.` }),
    onSuccess: () => {
      for (const queryKey of [["overview"], ["project", decision.project], ["task", decision.project, decision.slug]]) void queryClient.invalidateQueries({ queryKey });
      if (!chat) toast.show({ message: "Approval sent to L2" });
    },
  });
  const denied = approve.error instanceof ApiError && [401, 403].includes(approve.error.status);
  return <div className="question-set" data-review-pr={decision.pr ?? undefined}>
    <QuestionProse className="decision-question" text={decision.question || `Review PR #${decision.pr} before merge`} />
    {decision.detail ? <QuestionProse className="decision-why" text={decision.detail} /> : null}
    {approve.isSuccess ? <p className="text-meta text-muted" role="status">Approval sent · the L2 merges after a final check of the same PR.</p> : <>
      <div className="decision-options" role="group" aria-label="Merge review">
        <button className="btn btn-primary" type="button" disabled={disabled || denied || approve.isPending} onClick={() => approve.mutate()}>{approve.isPending ? "Sending…" : "Approve merge"}</button>
        {repository && decision.pr != null ? <a className="btn btn-ghost" href={`${repository}/pull/${decision.pr}`} target="_blank" rel="noopener noreferrer">View PR #{decision.pr}</a> : null}
      </div>
      <p className="text-meta text-muted">{chat ? "Or ask below. " : ""}Approving sends your message; nothing merges before the L2 checks the same PR again.</p>
    </>}
    {approve.isError ? <p className="text-meta text-danger" role="alert">{denied ? "You cannot approve here." : "Not sent."}{" "}
      {!denied ? <button type="button" className="link" onClick={() => approve.mutate()} disabled={disabled}>Retry</button> : null}</p> : null}
  </div>;
}

export function DecisionCard({ decision, decisions = [decision], selected = false, from = "project", disabled = false }: {
  decision: Decision; decisions?: Decision[]; selected?: boolean;
  from?: "needs" | "project"; disabled?: boolean;
}) {
  const project = useProject(decision.project);
  const kind = decisionKind(decision);
  const title = decision.title || decision.slug;
  const to = questionPath(decision);
  const state = { from, tab: from === "needs" ? "needs" : "work" };
  const navigate = useNavigate();
  const open = () => { setSelectedProject(decision.project); navigate(to, { state }); };
  return <ProseScope project={decision.project} repository={project.data?.repository}>
    <article className="decision" data-selected={selected || undefined} aria-label={title} onClick={(event) => {
      if (!(event.target as HTMLElement).closest("a,button,input,textarea,summary")) open();
    }}>
      <div className="decision-kind" data-tone={kind.tone}>
        <span className="kind-label">{kind.label}</span>
        <span className="ml-auto text-muted" title={exactTime(decision.asked)}>{ageText(decision.asked)}</span>
      </div>
      <Link className="decision-task" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>{title}</Link>
      {decision.kind === "review" ? <ReviewDecision decision={decision} repository={project.data?.repository} disabled={disabled} />
        : <QuestionSet decisions={decisions} disabled={disabled} from={from} />}
      <Link className="text-meta" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>Open L2 chat</Link>
    </article>
  </ProseScope>;
}
