import { Fragment, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, sendAnswers, sendApproval, useProject } from "../data/api";
import type { DecideInput, Decision, QuestionAnswer, QuestionGroup, Step } from "../data/api";
import { chatOnScreen, dropPending, shownOnScreen, usePending } from "../data/pending";
import type { PendingAnswer, PendingWrite } from "../data/pending";
import { notDone } from "./Bubbles";
import { useToast } from "../data/Toast";
import { decisionKind, optionsFor, questionPath } from "../data/decisions";
import { requestCommand } from "../data/terminalCommand";
import { ProseTerminal } from "./CodeBlock";
import { ageText, exactTime } from "../data/observed";
import { setSelectedProject } from "../shell/scope";
import { Choice, deliveryChoice } from "./Choice";
import type { ChoiceState } from "./Choice";
import { InlineProse, Prose, ProseRepository, ProseScope, QuestionProse } from "./Prose";
import { ReviewStatus, StateIcon, reviewLine } from "./TaskReview";
import { ShownFiles } from "./Shown";
import { PrPictures } from "./PrPictures";
import { keepForVisit, useVisitMemory } from "./visitMemory";

type Draft = { option?: string; text?: string };
const draftKey = (q: Decision) => `${q.project}:${q.slug}:${q.id}:${q.revision}`;
/** Who closed a question; any actor other than the agents and Altitude is the operator. */
const closedBy = (by: string) => by === "l2" || by === "l3" ? by.toUpperCase() : by === "altd" ? "Altitude" : "You";
/** The question whose preview or review was opened from a history entry, so returning there brings it into view;
 * no id is the task's merge review card. */
export type QuestionOrigin = Pick<Decision, "project" | "slug" | "id">;
const plainClick = (event: React.MouseEvent) => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
function recommendedKey(question: Decision) {
  return question.recommended_key ?? (!question.options && question.recommendation?.text ? "recommended" : null);
}

/** A proposal or merge question's review in its own small box (SPEC.md §3.8.3), opening its review card in the task conversation. */
function ReviewLink({ decision, from }: { decision: Decision; from?: "needs" | "project" }) {
  const location = useLocation();
  const review = decision.review;
  if (!review) return null;
  if (!review.id) return <p className="question-review"><ReviewStatus review={review} opens={false} /></p>;
  const { name, state } = reviewLine(review);
  return <Link className="question-review" aria-label={`${name} · ${state}`} to={`/projects/${decision.project}/tasks/${decision.slug}?${new URLSearchParams({ review: review.id })}`}
    state={from ? { from, tab: from === "needs" ? "needs" : "work" } : location.state} onClick={(event) => {
      if (!plainClick(event)) return;
      setSelectedProject(decision.project);
      keepForVisit(location.key, "origin", { project: decision.project, slug: decision.slug, id: decision.id } satisfies QuestionOrigin);
    }}><ReviewStatus review={review} opens /></Link>;
}

type QuestionProps = {
  disabled?: boolean; onDenied?: () => void; onRefresh?: () => void;
  refreshKey?: number;
  chat?: boolean; from?: "needs" | "project";
  /** Delivery states of the conversation's saved messages; unknown (Needs you) reads as not yet read. */
  deliveries?: Record<string, string | undefined>;
};

/** A saved answer before its decision is recorded (SPEC.md §3.8.2): a pressed option as its pill, typed words as text. */
function Receipt({ response, picked, delivery }: { response: NonNullable<Decision["response"]>; picked?: string; delivery?: string }) {
  const shown = deliveryChoice(delivery);
  if (picked) return <Choice {...shown} label={picked} />;
  return <div className="decision-receipt" role="status">
    <b>{shown.spoken[0]!.toUpperCase() + shown.spoken.slice(1)}</b><p><InlineProse text={response.text} /></p>
    <span className="text-meta text-muted" title={exactTime(response.at)}>{ageText(response.at)}</span>
  </div>;
}

/** The task terminal's notice for the question's `run` command (SPEC.md §3.8.5); the question stays open meanwhile. */
function Ran({ ran }: { ran: NonNullable<Decision["ran"]> }) {
  return <div className="decision-receipt question-ran" role="status">
    <StateIcon tone={ran.finished ? "wait" : "warn"} />
    <div>
      <b>{ran.finished ? "Ran in the terminal" : "The terminal closed before this command finished"}</b>
      <p className="text-meta text-muted" title={exactTime(ran.at)}>{ran.finished ? "Waiting for the L2 to check it · " : ""}{ageText(ran.at)}</p>
    </div>
  </div>;
}

/** Refused sends whose Not sent line was already brought into view, so returning to the card does not scroll again. */
const noticed = new Set<string>();

/** Preset and custom answers share one conversational handoff in Needs you and chat. */
export function QuestionSet({ decisions, group, target, disabled = false, onDenied, onRefresh, refreshKey, chat = false, from = "project", deliveries }: QuestionProps & {
  decisions: Decision[]; group?: QuestionGroup | null; target?: Decision;
}) {
  const queryClient = useQueryClient();
  // A send shows its answers everywhere at the press (SPEC.md §3.8); a refused one returns here with its picks
  // chosen again and Not sent, until Retry or an edit.
  const keys = new Set(decisions.map(draftKey));
  const refused: PendingWrite | undefined = usePending().filter((entry) => entry.kind === "answers" && entry.state === "refused"
    && Object.keys(entry.picks ?? {}).some((key) => keys.has(key))).at(-1);
  const alert = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!refused || noticed.has(refused.id)) return;
    noticed.add(refused.id);
    alert.current?.scrollIntoView({ block: "nearest" });
  }, [refused?.id]);
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const repository = useContext(ProseRepository);
  const terminal = useContext(ProseTerminal);
  // Unsent answers return with Back/Forward to this history entry; the signature below drops any whose revision closed.
  const kept: Record<string, Draft> = useVisitMemory<Draft>(decisions.map((q) => `answer:${draftKey(q)}`), (item): Draft | undefined => {
    const draft: Draft | undefined = drafts[item.slice("answer:".length)];
    return draft?.option || draft?.text ? draft : undefined;
  });
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() => Object.fromEntries(Object.entries(kept).map(([item, draft]) => [item.slice("answer:".length), draft])));
  const open = decisions.filter((q) => q.id && q.revision != null && q.status !== "resolved" && q.audience !== "l3" && !q.response);
  const signature = open.map(draftKey).join("|");
  useEffect(() => {
    const current = new Set(signature.split("|"));
    setDrafts((old) => Object.fromEntries(Object.entries(old).filter(([key]) => current.has(key))));
  }, [signature]);
  // Editing a refused send's picks, or Retry, makes them the card's own drafts again.
  const release = () => {
    if (!refused) return;
    setDrafts((old) => ({ ...refused.picks, ...old }));
    dropPending(queryClient, refused.id);
  };
  useEffect(() => { if (refreshKey) release(); }, [refreshKey]);
  const first = decisions[0];
  if (!first) return null;
  const grouped = decisions.length > 1;
  // The held PR's merge question links the PR beside Send, as the review card does (#419).
  const pr = open.find((q) => q.pr != null)?.pr;
  const shown = chat ? decisions : decisions.filter((q) => q.status !== "resolved");
  // While a member is open, closed members fold into one collapsed row so the card leads with what is current.
  const earlier = shown.some((q) => q.status !== "resolved") ? shown.filter((q) => q.status === "resolved") : [];
  const error = refused?.error;
  const denied = error instanceof ApiError && [401, 403].includes(error.status);
  const stale = error instanceof ApiError && error.status === 409;
  const groupId = group?.id ?? first.group_id;
  const groupRevision = group?.revision ?? first.group_revision;
  const unavailable = disabled || denied || stale || (grouped && (!groupId || groupRevision == null));
  const draftOf = (q: Decision): Draft | undefined => drafts[draftKey(q)] ?? refused?.picks?.[draftKey(q)];
  const answer = (q: Decision, key: string): QuestionAnswer => ({ question_id: q.id!, revision: q.revision!, option_key: key });
  const selected: QuestionAnswer[] = open.flatMap((q) => {
    const draft = draftOf(q);
    if (!q.id || q.revision == null || !draft) return [];
    if (draft.option) return [answer(q, draft.option)];
    return draft.text?.trim() ? [{ question_id: q.id, revision: q.revision, text: draft.text.trim() }] : [];
  });
  const perform = (input: DecideInput) => {
    const sent = "answers" in input ? input.answers : [input];
    const keyOf = (a: QuestionAnswer) => draftKey({ ...first, id: a.question_id, revision: a.revision });
    const answers: PendingAnswer[] = sent.map((a) => ({ question_id: a.question_id, revision: a.revision,
      text: a.text ?? optionsFor(decisions.find((q) => q.id === a.question_id && q.revision === a.revision) ?? first).find((option) => option.key === a.option_key)?.text ?? "" }));
    const picks = Object.fromEntries(sent.map((a) => [keyOf(a), a.option_key ? { option: a.option_key } : { text: a.text }]));
    const again = refused && JSON.stringify(refused.picks) === JSON.stringify(picks) ? refused.id : undefined;
    if (refused) dropPending(queryClient, refused.id);
    setDrafts((old) => Object.fromEntries(Object.entries(old).filter(([key]) => !sent.some((a) => key === keyOf(a)))));
    // The outcome may come after the card has left this page, so where it opens is fixed at the press.
    const { project, slug } = first;
    const open = () => {
      setSelectedProject(project);
      navigate(questionPath(first), { state: { from, tab: from === "needs" ? "needs" : "work" } });
    };
    const noun = sent.length === 1 ? "Answer" : "Answers";
    void sendAnswers(queryClient, input, answers, picks, again).then((outcome) => {
      if (outcome.state === "saved") {
        if (!chatOnScreen(project, slug)) toast.show({ message: `${noun} saved`, action: { label: "Open L2 chat", onClick: open } });
        return;
      }
      if (outcome.error instanceof ApiError && [401, 403].includes(outcome.error.status)) onDenied?.();
      // Where the card returns on screen, its Not sent line comes into view instead.
      if (!shownOnScreen(project, slug)) toast.show({ message: `${noun} not sent`, action: { label: "Show", onClick: open } });
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
    release();
    setDrafts((old) => ({ ...old, [draftKey(question)]: draft }));
  };
  const refresh = () => {
    release();
    onRefresh?.();
    for (const queryKey of [["overview"], ["project", first.project], ["task", first.project, first.slug]]) void queryClient.invalidateQueries({ queryKey });
  };
  // Open questions that are not the operator's (waits on L3) need no summary; their turn line says who answers.
  const summary = open.length ? `${open.length} question${open.length === 1 ? "" : "s"} to answer`
    : decisions.some((q) => q.response && q.status !== "resolved") ? null
    : decisions.every((q) => q.status === "resolved") ? "Questions closed" : null;
  const render = (question: Decision) => {
    const resolved = question.status === "resolved";
    const withdrawn = question.resolution?.disposition === "withdrawn";
    const options = optionsFor(question);
    const draft = draftOf(question);
    const custom = !options.length || draft?.text !== undefined;
    const actionable = open.includes(question);
    // A question whose shown files are gone takes no answer until the owner shows them again (SPEC.md §3.9.1).
    const inputDisabled = unavailable || !question.id || question.revision == null || question.shown?.available === false;
    const recommendation = <>
      {question.recommendation?.text ? <p className="decision-approach"><b>Recommended:</b> <InlineProse text={question.recommendation.text} /></p> : null}
      {question.recommendation?.why ? <p className="decision-why"><InlineProse text={question.recommendation.why} /></p> : null}
    </>;
    const prose = <QuestionProse className="decision-question" text={question.question || question.title || question.slug} />;
    const content = <>
      {question.ran?.finished && terminal && "open" in terminal ? <ProseTerminal value={{ ...terminal, ran: question.ran.command }}>{prose}</ProseTerminal> : prose}
      {!resolved && !question.response && question.audience === "l3" ? <p className="text-meta text-muted">L3 is handling this</p> : null}
      {resolved ? <div className="decision-receipt" role="status">
        {!withdrawn ? <b>{question.resolution?.disposition === "answered" ? "Decision recorded" : "Question closed"}</b> : null}
        {question.resolution ? <><p><InlineProse text={question.resolution.text} /></p><span className="text-meta text-muted" title={exactTime(question.resolution.at)}>{closedBy(question.resolution.by)} · {ageText(question.resolution.at)}</span></> : null}
      </div> : null}
      {!resolved && !question.response && question.ran ? <Ran ran={question.ran} /> : null}
      {!resolved && question.response ? <Receipt response={question.response} picked={options.find((option) => option.text === question.response!.text)?.label}
        delivery={deliveries?.[question.response.message_id]} /> : null}
      {question.shown ? <ShownFiles shown={question.shown} state={from ? { from, tab: from === "needs" ? "needs" : "work" } : undefined} onOpen={(event) => {
        if (!plainClick(event)) return;
        setSelectedProject(question.project);
        keepForVisit(location.key, "origin", { project: question.project, slug: question.slug, id: question.id } satisfies QuestionOrigin);
      }} /> : null}
      {question.pr_images ? <PrPictures pictures={question.pr_images} /> : null}
      <ReviewLink decision={question} from={chat ? undefined : from} />
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
  };
  return (
    <div className="question-set" data-group-id={group?.id ?? first.group_id} data-grouped={grouped || undefined}>
      {decisions.length > 1 && summary ? <p className="text-meta text-muted">{summary}</p> : null}
      {earlier.length ? <details className="question-history question-earlier" open={earlier.some((q) => q.id === target?.id && q.revision === target?.revision) || undefined}>
        <summary>{earlier.length} earlier question{earlier.length === 1 ? "" : "s"}</summary>
        {earlier.map(render)}
      </details> : null}
      {shown.filter((question) => !earlier.includes(question)).map(render)}
      {open.length ? <div className="question-batch">
        <button type="button" className="btn btn-primary" disabled={unavailable || !selected.length} onClick={() => submit(selected)}>{selected.length ? `Send ${selected.length} answer${selected.length === 1 ? "" : "s"}` : "Send answers"}</button>
        {repository && pr != null ? <a className="btn btn-ghost" href={`${repository}/pull/${pr}`} target="_blank" rel="noopener noreferrer">View PR #{pr}</a> : null}
        {refused ? <p ref={alert} className="text-meta text-danger" role="alert">
          {denied ? "You cannot send answers here. Refresh after access is restored." : stale ? "These questions have changed. Refresh and review the current choices." : notDone("Not sent", error instanceof ApiError ? error.message : undefined)}{" "}
          {denied || stale ? (!denied || !onDenied ? <button type="button" className="link" onClick={refresh}>Refresh</button> : null) : <button type="button" className="link" onClick={() => submit(selected)} disabled={unavailable || !selected.length}>Retry</button>}
        </p> : null}
        {selected.length ? <p className="text-meta text-muted">Only these answers will be sent. You can answer the rest later.</p> : null}
      </div> : null}
    </div>
  );
}

export function Question({ decision, ...props }: QuestionProps & { decision: Decision }) {
  return <QuestionSet decisions={[decision]} {...props} />;
}

/** #419: a held review-ready PR asks for review. Approve sends the operator's own message; the L2 merges with it. */
export function ReviewDecision({ decision, repository, disabled = false, chat = false, from = "project" }: {
  decision: Decision; repository?: string | null; disabled?: boolean; chat?: boolean; from?: "needs" | "project";
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  // The approval shows in the chat at the press and the card leaves (SPEC.md §3.8.2); a refused one returns here
  // with Not sent and Retry, unless the review changed meanwhile and the task's current state replaces the card.
  const sent = usePending().filter((entry) => entry.kind === "approval" && entry.project === decision.project
    && entry.slug === decision.slug && entry.pr === decision.pr && entry.hold === (decision.review_hold_id ?? null)).at(-1);
  const error = sent?.state === "refused" ? sent.error : undefined;
  const denied = error instanceof ApiError && [401, 403].includes(error.status);
  const state: ChoiceState = !sent ? "ready" : sent.state === "refused" ? denied ? "ready" : "fail" : "wait";
  const approve = () => {
    if (sent) dropPending(queryClient, sent.id);
    const { project, slug } = decision;
    void sendApproval(queryClient, decision).then((outcome) => {
      if (outcome.state === "saved") {
        if (!chatOnScreen(project, slug)) toast.show({ message: "Approval saved", action: { label: "Open L2 chat", onClick: () => {
          setSelectedProject(project);
          navigate(`/projects/${project}/tasks/${slug}`, { state: { from, tab: from === "needs" ? "needs" : "work" } });
        } } });
      } else if (!shownOnScreen(project, slug)) {
        toast.show({ message: "Approval not sent", action: { label: "Show", onClick: () => {
          setSelectedProject(project);
          navigate(`/projects/${project}/tasks/${slug}`, { state: { from, tab: from === "needs" ? "needs" : "work" } });
        } } });
      }
    });
  };
  return <div className="question-set" data-review-pr={decision.pr ?? undefined} data-review-task={`${decision.project}/${decision.slug}`}>
    <QuestionProse className="decision-question" text={decision.question || `Review PR #${decision.pr} before merge`} />
    {decision.detail ? <QuestionProse className="decision-why" text={decision.detail} /> : null}
    {decision.pr_images ? <PrPictures pictures={decision.pr_images} /> : null}
    <ReviewLink decision={decision} from={chat ? undefined : from} />
    <div className="decision-options" role="group" aria-label="Merge review">
      <Choice primary label="Approve merge" state={state} disabled={disabled || denied}
        spoken={state === "wait" ? "waiting for the L2" : state === "fail" ? "retry" : undefined}
        error={error ? denied ? "You cannot approve here." : notDone("Not sent", error instanceof ApiError ? error.message : undefined) : null} onPress={approve} />
      {repository && decision.pr != null ? <a className="btn btn-ghost" href={`${repository}/pull/${decision.pr}`} target="_blank" rel="noopener noreferrer">View PR #{decision.pr}</a> : null}
    </div>
    <p className="text-meta text-muted">{chat ? "Or ask below. " : ""}Approving sends your message; nothing merges before the L2 checks the same PR again.</p>
  </div>;
}

/**
 * An operator step's body (SPEC.md §3.8.4): L3's words with its command, whose Open in terminal types it at the
 * project terminal. Needs you lists the tasks waiting on it; a waiting task's own page does not.
 */
export function StepBody({ step, tasks = true, disabled = false }: { step: Step; tasks?: boolean; disabled?: boolean }) {
  const navigate = useNavigate();
  // A saved read may show a step that already ran, so the command waits for a fresh one (review finding).
  const terminal = useMemo(() => disabled ? { unavailable: "Refresh Needs you before running this." } : { open: (command: string) => {
    setSelectedProject(step.project);
    requestCommand(step.project, undefined, command);
    void navigate(`/projects/${step.project}/terminal`);
  } }, [step.project, navigate, disabled]);
  return <div className="question-set" data-step-id={step.id}>
    <ProseTerminal value={terminal}><QuestionProse className="decision-question" text={step.question} /></ProseTerminal>
    {tasks && step.tasks.length ? <p className="text-meta">Waiting on this: {step.tasks.map((task, index) => <Fragment key={task.slug}>
      {index ? " · " : null}<Link className="prose-link" to={`/projects/${step.project}/tasks/${task.slug}`} onClick={() => setSelectedProject(step.project)}>{task.title}</Link>
    </Fragment>)}</p> : null}
    <p className="text-meta text-muted">This leaves Needs you once the command finishes in the project terminal.</p>
    <Link className="text-meta" to={`/projects/${step.project}`} onClick={() => setSelectedProject(step.project)}>Open project chat</Link>
  </div>;
}

/** The Needs you card of an operator step: no task, so it opens the project chat where L3 handed it over. */
export function StepCard({ step, disabled = false }: { step: Step; disabled?: boolean }) {
  const project = useProject(step.project);
  const navigate = useNavigate();
  const to = `/projects/${step.project}`;
  const open = () => { setSelectedProject(step.project); navigate(to); };
  return <ProseScope project={step.project} repository={project.data?.repository}>
    <article className="decision" aria-label={`Command for ${step.project}`} data-step={step.id} onClick={(event) => {
      if (!(event.target as HTMLElement).closest("a,button,input,textarea,summary")) open();
    }}>
      <div className="decision-kind" data-tone={decisionKind(step).tone}>
        <span className="kind-label">{decisionKind(step).label}</span>
        <span className="ml-auto text-muted" title={exactTime(step.asked)}>{ageText(step.asked)}</span>
      </div>
      <StepBody step={step} disabled={disabled} />
    </article>
  </ProseScope>;
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
  // A question's `run` block opens the task's terminal with its command typed, as in the task's chat (SPEC.md §3.3).
  const terminal = useMemo(() => ({ open: (command: string) => {
    setSelectedProject(decision.project);
    requestCommand(decision.project, decision.slug, command);
    void navigate(`/projects/${decision.project}/tasks/${decision.slug}/terminal`, { state: { from, tab: from === "needs" ? "needs" : "work" } });
  } }), [decision.project, decision.slug, navigate, from]);
  return <ProseScope project={decision.project} repository={project.data?.repository}><ProseTerminal value={terminal}>
    <article className="decision" data-selected={selected || undefined} aria-label={title} onClick={(event) => {
      if (!(event.target as HTMLElement).closest("a,button,input,textarea,summary")) open();
    }}>
      <div className="decision-kind" data-tone={kind.tone}>
        <span className="kind-label">{kind.label}</span>
        <span className="ml-auto text-muted" title={exactTime(decision.asked)}>{ageText(decision.asked)}</span>
      </div>
      <Link className="decision-task" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>{title}</Link>
      {decision.kind === "review" ? <ReviewDecision decision={decision} repository={project.data?.repository} disabled={disabled} from={from} />
        : <QuestionSet decisions={decisions} disabled={disabled} from={from} />}
      <Link className="text-meta" to={to} state={state} onClick={() => setSelectedProject(decision.project)}>Open L2 chat</Link>
    </article>
  </ProseTerminal></ProseScope>;
}
