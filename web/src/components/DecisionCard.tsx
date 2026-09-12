import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, useDecide, useProject } from "../data/api";
import type { DecideInput, Decision, QuestionGroup } from "../data/api";
import { useToast } from "../data/Toast";
import { decisionKind, questionPath } from "../data/decisions";
import { ageText, exactTime } from "../data/observed";
import { setSelectedProject } from "../shell/scope";
import { InlineProse, Prose, ProseRepository } from "./Prose";

type Option = { key: string; label: string; text: string };
type Answer = { question_id: string; revision: number; option_key: string };
function optionsFor(question: Decision): Option[] {
  if (question.options) return question.options;
  const recommended = question.recommendation;
  return recommended?.text ? [{ key: "recommended", label: recommended.label || "Accept & resume", text: recommended.text }] : [];
}
function recommendedKey(question: Decision) {
  return question.recommended_key ?? (!question.options && question.recommendation?.text ? "recommended" : null);
}

type QuestionProps = {
  disabled?: boolean; onDenied?: () => void; onRefresh?: () => void;
  chat?: boolean; from?: "needs" | "project";
};

/** The same plain question, immediate choices, or explicit answer batch in Needs you and chat. */
export function QuestionSet({ decisions, group, disabled = false, onDenied, onRefresh, chat = false, from = "project" }: QuestionProps & {
  decisions: Decision[]; group?: QuestionGroup | null;
}) {
  const decide = useDecide();
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [picked, setPicked] = useState<Record<string, string>>({});
  const signature = decisions.map((q) => `${q.id}:${q.revision}:${q.status}:${q.group_revision}`).join("|");
  useEffect(() => { setPicked({}); }, [signature]);
  const first = decisions[0];
  if (!first) return null;
  const open = decisions.filter((q) => q.status !== "resolved" && q.audience !== "l3");
  const grouped = open.length > 1;
  const shown = chat ? decisions : decisions.filter((q) => q.status !== "resolved");
  const denied = decide.error instanceof ApiError && [401, 403].includes(decide.error.status);
  const stale = decide.error instanceof ApiError && decide.error.status === 409;
  const groupId = group?.id ?? first.group_id;
  const groupRevision = group?.revision ?? first.group_revision;
  const unavailable = disabled || denied || stale || decide.isPending || (grouped && (!groupId || groupRevision == null));
  const answer = (q: Decision, key: string): Answer => ({ question_id: q.id!, revision: q.revision!, option_key: key });
  const selected = open.filter((q) => q.id && picked[q.id]).map((q) => answer(q, picked[q.id!]!));
  const recommendations = open.filter((q) => q.id && q.revision != null && optionsFor(q).some((o) => o.key === recommendedKey(q)))
    .map((q) => answer(q, recommendedKey(q)!));
  const perform = (input: DecideInput) => {
    decide.mutate(input, {
      onSuccess: () => {
        setPicked({});
        if (!chat) toast.show({ message: "answers" in input && input.answers.length > 1 ? "Answers recorded" : "Decision recorded", action: { label: "Open L2 chat", onClick: () => {
          setSelectedProject(first.project);
          navigate(questionPath(first), { state: { from, tab: from === "needs" ? "needs" : "work" } });
        } } });
      },
      onError: (error) => { if (error instanceof ApiError && [401, 403].includes(error.status)) onDenied?.(); },
    });
  };
  const submit = (answers: Answer[]) => {
    if (!answers.length || unavailable) return;
    const single = answers[0]!;
    if (grouped) {
      if (!groupId || groupRevision == null) return;
      perform({ project: first.project, slug: first.slug, group_id: groupId, group_revision: groupRevision, answers });
    } else perform({ project: first.project, slug: first.slug, question_id: single.question_id, revision: single.revision,
      option_key: single.option_key });
  };
  const refresh = () => {
    decide.reset();
    onRefresh?.();
    for (const queryKey of [["overview"], ["project", first.project], ["task", first.project, first.slug]]) void queryClient.invalidateQueries({ queryKey });
  };
  return (
    <div className="question-set" data-group-id={group?.id ?? first.group_id} data-grouped={grouped || undefined}>
      {decisions.length > 1 ? <p className="text-meta text-muted">{open.length ? `${open.length} question${open.length === 1 ? "" : "s"} to answer` : decisions.every((q) => q.resolution?.disposition === "answered") ? "Answers recorded" : "Questions closed"}</p> : null}
      {shown.map((question) => {
        const resolved = question.status === "resolved";
        const withdrawn = question.resolution?.disposition === "withdrawn";
        const options = optionsFor(question);
        const recommendation = <>
          {question.recommendation?.text ? <p className="decision-approach"><b>Recommended:</b> <InlineProse text={question.recommendation.text} /></p> : null}
          {question.recommendation?.why ? <p className="decision-why"><InlineProse text={question.recommendation.why} /></p> : null}
        </>;
        const content = <>
          <p className="decision-question"><InlineProse text={question.question || question.title || question.slug} /></p>
          {resolved ? <div className="decision-receipt" role="status">
            {!withdrawn ? <b>{question.resolution?.disposition === "answered" ? "Decision recorded" : "Question closed"}</b> : null}
            {question.resolution ? <><p><InlineProse text={question.resolution.text} /></p><span className="text-meta text-muted" title={exactTime(question.resolution.at)}>{question.resolution.by} · {ageText(question.resolution.at)}</span></> : null}
          </div> : null}
          {question.design_url ? <a className="text-meta" href={question.design_url} target="_blank" rel="noopener noreferrer">View preview · v{question.revision}</a> : null}
          {resolved && !withdrawn && question.recommendation?.text ? <details className="question-context"><summary>Earlier recommendation</summary>{recommendation}</details> : recommendation}
          {chat && question.detail && question.detail !== question.question ? <details className="question-context">
            <summary>More context</summary>
            <Prose text={question.detail} />
          </details> : null}
          {!resolved && question.audience !== "l3" && options.length ? <div className="decision-options" role="group" aria-label={question.question || "Quick answers"}>
            {options.map((option) => {
              const recording = decide.isPending && !grouped && decide.variables && "option_key" in decide.variables && decide.variables.option_key === option.key;
              return <button key={option.key} className={`btn ${!grouped && option.key === recommendedKey(question) ? "btn-primary" : "btn-ghost"}`} type="button"
              aria-label={recording ? "Recording…" : option.label} aria-pressed={grouped ? picked[question.id!] === option.key : undefined}
              disabled={unavailable || !question.id || question.revision == null}
              onClick={() => grouped ? setPicked((old) => ({ ...old, [question.id!]: old[question.id!] === option.key ? "" : option.key })) : submit([answer(question, option.key)])}>
              {recording ? "Recording…" : option.label}
            </button>; })}
          </div> : null}
        </>;
        return <div key={`${question.id}:${question.revision}`} className="question-body" data-question-id={question.id ?? undefined} data-question-revision={question.revision ?? undefined} data-status={question.status}>
          {withdrawn ? <details className="question-history">
            <summary>Question withdrawn</summary>
            <div className="question-body">{content}</div>
          </details> : content}
        </div>;
      })}
      {grouped && open.length ? <div className="question-batch">
        {selected.length ? <button type="button" className="btn btn-primary" disabled={unavailable} onClick={() => submit(selected)}>{decide.isPending ? "Recording…" : `Send ${selected.length} answer${selected.length === 1 ? "" : "s"}`}</button>
          : recommendations.length ? <button type="button" className="btn btn-primary" disabled={unavailable} onClick={() => submit(recommendations)}>{decide.isPending ? "Recording…" : "Use recommendations"}</button> : null}
        <p className="text-meta text-muted">{selected.length ? "Only your selected answers will be sent." : "Choose quick answers together, or reply in the L2 chat."}</p>
      </div> : null}
      {decide.isError ? <p className="text-meta text-danger" role="alert">
        {denied ? "You cannot record a decision here. Refresh after access is restored." : stale ? "These questions have changed. Refresh and review the current choices." : "Could not record the decision."}{" "}
        {denied || stale ? (!denied || !onDenied ? <button type="button" className="link" onClick={refresh}>Refresh</button> : null) : <button type="button" className="link" onClick={() => decide.variables && perform(decide.variables)} disabled={disabled || decide.isPending}>Retry</button>}
      </p> : null}
    </div>
  );
}

export function Question({ decision, ...props }: QuestionProps & { decision: Decision }) {
  return <QuestionSet decisions={[decision]} {...props} />;
}

export function DecisionCard({ decision, decisions = [decision], chip = false, selected = false, from = "project", disabled = false }: {
  decision: Decision; decisions?: Decision[]; chip?: boolean; selected?: boolean;
  from?: "needs" | "project"; disabled?: boolean;
}) {
  const project = useProject(decision.project);
  const kind = decisionKind(decision);
  const title = decision.title || decision.slug;
  const to = questionPath(decision);
  const state = { from, tab: from === "needs" ? "needs" : "work" };
  const navigate = useNavigate();
  const open = () => { setSelectedProject(decision.project); navigate(to, { state }); };
  return <ProseRepository value={project.data?.repository}>
    <article className="decision" data-selected={selected || undefined} aria-label={title} onClick={(event) => {
      if (!(event.target as HTMLElement).closest("a,button,input,textarea,summary")) open();
    }}>
      <div className="decision-kind" data-tone={kind.tone}>
        <span className="kind-label">{kind.label}</span>
        {chip ? <span className="chip">{decision.project}</span> : null}
        <span className="ml-auto text-muted" title={exactTime(decision.asked)}>{ageText(decision.asked)}</span>
      </div>
      <Link className="decision-task" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>{title}</Link>
      <QuestionSet decisions={decisions} disabled={disabled} from={from} />
      <Link className="text-meta" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>Open L2 chat</Link>
    </article>
  </ProseRepository>;
}
