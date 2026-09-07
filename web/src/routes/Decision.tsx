import { useCallback, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { sendL2Message, streamChat, useChat, useDecide, useOverview, useProject, useTask } from "../data/api";
import type { Decision, DecisionOption, TaskView } from "../data/api";
import Composer from "../components/Composer";
import { DecisionOptions } from "../components/DecisionCard";
import { Prose } from "../components/Prose";
import { clock } from "../components/Bubbles";
import { askerLabel, askerOf, decisionKind, decisionOptions, followUpsOf, isOperator, recommendedOption } from "../data/decisions";
import type { FollowUp } from "../data/decisions";
import { ageText, exactTime, when } from "../data/observed";
import { l2Label } from "../components/TaskCard";
import { useViewport } from "../shell/breakpoints";
import { Overlay } from "../shell/Overlay";
import { decisionsFor } from "../shell/projects";
import { WorkPanel } from "./Project";
import { PanelIcon } from "./Task";

type Event = Record<string, unknown>;

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The first sentence of a text, for a timeline line that must stay short. */
export function firstSentence(text: string): string {
  const match = /^(.*?[.!?])(\s|$)/s.exec(text.trim());
  return (match?.[1] ?? text.trim()).replace(/\s+/g, " ");
}

/** What a task recorded when the operator decided (tasks.decide). */
export interface DecisionRecord {
  option: string;
  note: string;
  at: string | null;
}

export function decisionRecord(task: TaskView | undefined): DecisionRecord | null {
  const raw = task?.["decision"];
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const option = str(record["option"]) || str(record["key"]);
  return option ? { option, note: str(record["note"]), at: str(record["at"]) || null } : null;
}

/**
 * Where the current decision's window starts when the queue no longer names it: the block that the
 * last recorded decision answered, so the page stays readable after the choice (§3.9 already decided).
 */
export function windowStart(events: Event[]): string | null {
  let lastDecided = -1;
  events.forEach((event, index) => {
    if (event["kind"] === "decided") lastDecided = index;
  });
  const before = lastDecided >= 0 ? events.slice(0, lastDecided) : events;
  for (let i = before.length - 1; i >= 0; i -= 1) {
    const event = before[i];
    if (event?.["kind"] === "state" && event["to"] === "blocked") return str(event["at"]) || null;
  }
  return null;
}

export interface TimelineItem {
  id: string;
  at: string | null;
  who: string;
  text: string;
  quote?: string;
  /** The "now" marker: the operator's turn. */
  you?: boolean;
}

/**
 * Where this came from (SPEC.md §3.9): the task's events since the decision's window opened, the
 * follow-ups and their answers, and the "now" line while the task waits.
 */
export function timelineOf(
  task: TaskView | undefined,
  since: string | null,
  followUps: FollowUp[],
  waiting: boolean,
  /** The L2's model and engine as the task card words them ("Opus on Alpha"), or "". */
  l2Label = "",
): TimelineItem[] {
  const items: TimelineItem[] = [];
  const events = (task?.events ?? []) as Event[];
  const start = when(since);
  const inWindow = (at: string) => start == null || (when(at) ?? 0) >= start - 1_000;
  const l2 = l2Label ? ` (${l2Label})` : "";
  events.forEach((event, index) => {
    const at = str(event["at"]) || null;
    if (at && !inWindow(at)) return;
    const kind = str(event["kind"]);
    const by = str(event["by"]);
    const reason = str(event["reason"]).trim();
    const id = `event:${index}`;
    if (kind === "state" && event["to"] === "blocked") {
      if (by === "l2") items.push({ id, at, who: `The L2${l2}`, text: "asked L3", quote: reason });
      else if (isOperator(by)) items.push({ id, at, who: "You", text: `stopped the task${reason ? `: ${reason}` : ""}` });
      else items.push({ id, at, who: "Altitude", text: `blocked the task${reason ? `: ${reason}` : ""}` });
    } else if (kind === "escalated") {
      items.push({ id, at, who: "L3", text: `escalated to you: ${firstSentence(str(event["question"]))}` });
    } else if (kind === "decided") {
      const note = str(event["note"]).trim();
      items.push({ id, at, who: "You", text: `chose ${str(event["option"]) || str(event["key"])}`, quote: note || undefined });
    } else if (kind === "state" && event["to"] === "running" && by !== "l2") {
      items.push({ id, at, who: isOperator(by) ? "You" : "Altitude", text: "resumed the task" });
    } else if (kind === "fyi") {
      items.push({ id, at, who: "L3", text: `FYI: ${str(event["text"])}` });
    }
  });
  for (const item of followUps) {
    items.push({ id: `${item.id}:q`, at: item.at, who: "You", text: `asked ${askerLabel(item.to)}`, quote: item.question });
    if (item.answer != null) {
      items.push({ id: `${item.id}:a`, at: item.answeredAt ?? item.at, who: item.to === "l2" ? "The L2" : "L3", text: "answered", quote: item.answer });
    }
  }
  items.sort((a, b) => (when(a.at) ?? 0) - (when(b.at) ?? 0));
  if (waiting) items.push({ id: "now", at: null, who: "", text: "The task is blocked until you choose.", you: true });
  return items;
}

export interface EvidenceChip {
  href: string;
  label: string;
  external?: boolean;
}

/** The evidence chips (SPEC.md §3.9): the task's conversation, live session, report, and the PRs and issues named. */
export function evidenceOf(
  project: string,
  slug: string,
  task: TaskView | undefined,
  decision: Decision | undefined,
  repository: string | null | undefined,
): EvidenceChip[] {
  const base = `/projects/${project}/tasks/${slug}`;
  const chips: EvidenceChip[] = [{ href: base, label: "Task conversation" }];
  if (task?.["session_id"] || task?.live) chips.push({ href: `${base}/live`, label: "Live session at the failing step" });
  if (task?.files?.["report"]) chips.push({ href: `${base}/report`, label: "Full report" });
  const repo = repository?.replace(/\/+$/, "") ?? "";
  const seen = new Set<number>();
  const prs = Array.isArray(task?.["prs"]) ? (task?.["prs"] as unknown[]) : [];
  for (const pr of prs) {
    if (typeof pr !== "number" || seen.has(pr)) continue;
    seen.add(pr);
    chips.push(repo ? { href: `${repo}/pull/${pr}`, label: `PR #${pr}`, external: true } : { href: base, label: `PR #${pr}` });
  }
  if (repo && decision) {
    for (const match of `${decision.question} ${decision.detail ?? ""}`.matchAll(/(?<![\w/])#(\d+)\b/g)) {
      const n = Number(match[1]);
      if (seen.has(n)) continue;
      seen.add(n);
      chips.push({ href: `${repo}/issues/${n}`, label: `#${n}`, external: true });
    }
  }
  return chips;
}

function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="tl" aria-label="Where this came from">
      {items.map((item) => (
        <li key={item.id} className="tli">
          <span className="tl-time" title={item.at ? exactTime(item.at) : undefined}>
            {item.at ? clock(when(item.at) ?? 0) : "now"}
          </span>
          <span className="tl-mark" aria-hidden>
            <i data-you={item.you || undefined} />
          </span>
          <div className="tl-body">
            {item.who ? <b>{item.who}</b> : null}
            {item.who ? " " : ""}
            {item.text}
            {item.quote ? <q>{item.quote}</q> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The decision page (SPEC.md §3.9): the project layout with the decision in place of the conversation. */
export default function DecisionPage() {
  const { name = "", slug = "" } = useParams();
  const location = useLocation();
  const state = location.state && typeof location.state === "object" ? (location.state as Record<string, unknown>) : {};
  const from = state["from"] === "needs" ? "needs" : "project";
  const tab = from === "needs" ? "needs" : "work";
  const overview = useOverview();
  const project = useProject(name);
  const task = useTask(name, slug);
  const chat = useChat(name);
  const queryClient = useQueryClient();
  const decide = useDecide();
  const { phone, panelInline } = useViewport();
  const [panelOpen, setPanelOpen] = useState(false);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const [chosen, setChosen] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState("");
  const [picked, setPicked] = useState<"l3" | "l2" | null>(null);
  const [pending, setPending] = useState<{ to: "l3" | "l2"; question: string; at: string } | null>(null);

  const decisions = decisionsFor(overview.data, name);
  const decision = decisions.find((d) => d.slug === slug);
  const record = decisionRecord(task.data);
  const events = (task.data?.events ?? []) as Event[];
  const since = decision?.since ?? windowStart(events);
  const followUps = followUpsOf({ slug, since }, chat.data?.history ?? [], chat.data?.queued ?? [], task.data?.messages ?? []).concat(
    pending ? [{ id: "pending", to: pending.to, at: pending.at, question: pending.question, answer: null, answeredAt: null, failed: false, queued: false }] : [],
  );
  const recipient = picked ?? (decision ? askerOf(decision) : "l3");
  const taskPath = `/projects/${name}/tasks/${slug}`;
  const backTo = from === "needs" ? "/" : `/projects/${name}?tab=work`;

  const choose = (option: DecisionOption) => {
    if (!decision) return;
    setChosen(option.label);
    decide.mutate({ project: name, slug, option: option.key ?? option.label, note: note.trim() || undefined });
  };

  const send = useCallback(
    async (text: string) => {
      const at = new Date().toISOString();
      setPending({ to: recipient, question: text, at });
      try {
        if (recipient === "l2") {
          await sendL2Message({ project: name, slug, text });
          await queryClient.invalidateQueries({ queryKey: ["task", name, slug] });
        } else {
          await streamChat(name, text, { onText: () => {} }, { slug });
          await queryClient.invalidateQueries({ queryKey: ["chat", name] });
        }
      } finally {
        setPending(null);
      }
    },
    [name, slug, recipient, queryClient],
  );

  const loading = overview.isPending || (!decision && task.isPending);
  const failed = overview.isError || (!decision && task.isError);
  const gone = !loading && !decision && task.isSuccess && (task.data.state === "done" || task.data.state === "rejected");
  const decided = !loading && !decision && !gone && task.isSuccess;
  const waiting = Boolean(decision);
  const kind = decision ? decisionKind(decision) : null;
  // Without the queue row (already decided, gone) the question is the escalation's first sentence.
  const question = decision?.question ?? firstSentence(str([...events].reverse().find((e) => e["kind"] === "escalated")?.["question"]));
  const title = decision?.title || task.data?.title || slug;
  const options = decision ? decisionOptions(decision) : [];
  const recommended = decision ? recommendedOption(decision) : null;
  const why = decision?.recommendation?.why?.trim() || "";
  const described = options.filter((o) => o.text && o.text.trim() && o.text.trim() !== o.label);
  const evidence = evidenceOf(name, slug, task.data, decision, project.data?.repository);
  const timeline = timelineOf(task.data, since, followUps, waiting, task.data ? l2Label(task.data, overview.data) : "");
  const asked = decision?.asked ?? decision?.since ?? null;

  const body = loading ? (
    <div className="decision-col" aria-label="Loading">
      <div className="skeleton h-5 w-48" />
      <div className="skeleton h-6" />
      <div className="skeleton h-10 w-72" />
      <div className="skeleton h-24" />
    </div>
  ) : failed ? (
    <p className="decision-col text-meta text-danger">
      Could not read the decision.{" "}
      <button
        type="button"
        className="link"
        onClick={() => {
          void overview.refetch();
          void task.refetch();
        }}
      >
        Retry
      </button>
    </p>
  ) : (
    <div className="decision-col">
      {gone ? (
        <p className="decision-banner" role="status">
          This task was {task.data?.state}.{" "}
          <Link className="link" to={taskPath} state={{ tab }}>
            Open the archived task
          </Link>
        </p>
      ) : decided ? (
        <p className="decision-banner" role="status">
          {record
            ? `Decided ${ageText(record.at)}: ${record.option}${record.note ? ` · ${record.note}` : ""}`
            : `This task was resumed elsewhere; it is ${task.data?.state ?? "no longer blocked"} now.`}
        </p>
      ) : null}
      <div className="decision-kind decision-chips" data-tone={kind?.tone ?? "accent"}>
        <span className="chip">{name}</span>
        {kind ? <span className="kind-label">{kind.label}</span> : null}
        <span className="decision-task truncate">{title}</span>
        {asked ? (
          <span className="text-muted decision-age" title={exactTime(asked)}>
            {ageText(asked)}
          </span>
        ) : null}
      </div>
      <h1 className="decision-title">{question || title}</h1>
      {decision && recommended ? (
        <div className="decision-options decision-options-page">
          <DecisionOptions
            options={options}
            recommended={recommended}
            chosen={chosen}
            deciding={decide.isPending}
            size="lg"
            onChoose={choose}
          />
          <input
            className="field decision-note"
            aria-label="Note for the L2"
            placeholder="Add a note for the L2 (optional)"
            value={note}
            disabled={decide.isPending}
            onChange={(event) => setNote(event.target.value)}
          />
          {decide.isError ? (
            <p className="text-meta text-danger decision-error" role="alert">
              Could not record the decision.{" "}
              <button type="button" className="link" onClick={() => decide.reset()}>
                Retry
              </button>
            </p>
          ) : null}
        </div>
      ) : null}
      {decision && (why || described.length > 0) ? (
        <section aria-label="Why L3 recommends">
          <h2 className="dsh">Why L3 recommends {recommended?.label}</h2>
          {why ? <Prose text={why} /> : null}
          {described.map((option) => (
            <p key={option.key ?? option.label} className="dp">
              <b>{option.label}:</b> {option.text}
            </p>
          ))}
        </section>
      ) : null}
      <section aria-label="Where this came from">
        <h2 className="dsh">Where this came from</h2>
        <Timeline items={timeline} />
      </section>
      <section aria-label="Evidence">
        <h2 className="dsh">Evidence</h2>
        <div className="evidence">
          {evidence.map((chip) =>
            chip.external ? (
              <a key={chip.label} className="chip evidence-chip" href={chip.href} target="_blank" rel="noreferrer">
                {chip.label}
              </a>
            ) : (
              <Link key={chip.label} className="chip evidence-chip" to={chip.href} state={{ tab }}>
                {chip.label}
              </Link>
            ),
          )}
        </div>
      </section>
    </div>
  );

  const composer = waiting ? (
    <div className="convo-dock decision-dock">
      <Composer
        value={draft}
        onChange={setDraft}
        onSubmit={send}
        placeholder="Ask a follow-up before you decide"
        ariaLabel="Ask a follow-up"
        busy={Boolean(chat.data?.active || chat.data?.busy)}
        pill={
          <select
            className="composer-pill-select"
            aria-label="Recipient"
            value={recipient}
            onChange={(event) => setPicked(event.target.value === "l2" ? "l2" : "l3")}
          >
            <option value="l3">To L3</option>
            <option value="l2">To the L2</option>
          </select>
        }
        hint={
          pending ? (
            <span role="status">{pending.to === "l2" ? "The L2 sees it when it resumes…" : "L3 is answering…"}</span>
          ) : (
            "Your question and the answer appear here and on the card. The L2 stays blocked until you choose."
          )
        }
      />
    </div>
  ) : null;

  const panel = <WorkPanel name={name} project={project} decisions={decisions} selected={slug} />;
  const main = (
    <div className="convo decision-page">
      <div className="convo-scroll decision-scroll">{body}</div>
      {composer}
    </div>
  );

  if (phone) return <div className="project-page">{main}</div>;
  return (
    <div className="project-page">
      <header className="task-header decision-head">
        <div className="task-crumb-row">
          <Link className="task-crumb" to={backTo}>
            ‹ {from === "needs" ? "Needs you" : name}
          </Link>
          <div className="task-actions">
            <Link className="btn btn-ghost task-action" to={taskPath} state={{ tab }}>
              Open task
            </Link>
            {!panelInline ? (
              <button
                type="button"
                className="icon-btn"
                aria-label="Work"
                aria-pressed={panelOpen}
                onClick={() => setPanelOpen((open) => !open)}
              >
                <PanelIcon />
              </button>
            ) : null}
          </div>
        </div>
      </header>
      <div className="project-body">
        {main}
        {panelInline ? (
          panel
        ) : panelOpen ? (
          <Overlay label="Work" side="right" onClose={closePanel}>
            {panel}
          </Overlay>
        ) : null}
      </div>
    </div>
  );
}
