import { useState } from "react";
import { Link } from "react-router";
import { useTask } from "../data/api";
import type { ChatMessage } from "../data/api";
import { stampText } from "../data/observed";
import { InlineProse, Prose, ProseProject, ProseRepository, lastParagraph } from "./Prose";
import { Stamp } from "./Stamp";

/*
 * The system line (SPEC.md §3.4, §4.1): every chat row whose trigger is not "chat" is a system turn,
 * folded to one centred line with a dot, its recorded time, the reply's last paragraph, and Show. A run of them folds to
 * one line naming the count; selected heads-ups stay separate. Expanded cards keep full text and links.
 */

/** One system turn as the conversation reads it from the chat rows. */
export interface SystemTurn {
  id: string;
  trigger: string;
  at: string | null;
  /** What altd sent L3 (the user row), or the FYI's own text. */
  prompt: string;
  /** L3's reply, once the assistant row exists. */
  reply: string | null;
  /** The error row's text when the turn failed. */
  error: string | null;
  inProgress: boolean;
  /** The task the turn is about, when a row names one. */
  slug: string | null;
  fyi: boolean;
  headsUp: boolean;
  projectMessage?: ChatMessage["project_message"];
}

const DANGER = new Set(["incident", "system-recovery", "project-message-error"]);

export function dangerTrigger(trigger: string): boolean {
  return DANGER.has(trigger) || trigger.includes("fault");
}

/** What L3 is handling during a system turn (SPEC.md §4.1), from the turn's trigger and its task. */
export function handling(trigger: string, task?: string | null): string {
  const on = task ? ` ${trigger === "report-landed" ? "for" : "on"} ${task}` : "";
  switch (trigger) {
    case "report-landed":
      return `a landed report${on}`;
    case "block":
      return `a block${on}`;
    case "incident":
      return `a fault${on}`;
    case "system-recovery":
      return `a recovery${on}`;
    case "restart":
      return "the restart";
    case "start":
      return "the start";
    case "fyi":
      return `an FYI${on}`;
    case "terminal":
      return "a terminal notice";
    default:
      return `a system event${on}`;
  }
}

/** The card header's first word for a trigger. */
export function kindLabel(trigger: string): string {
  switch (trigger) {
    case "project-message":
      return "Coordinator message";
    case "project-message-error":
      return "Coordinator message receipt";
    case "report-landed":
      return "Report landed";
    case "block":
      return "Block";
    case "incident":
      return "Fault";
    case "system-recovery":
      return "Recovery";
    case "restart":
      return "Restart";
    case "start":
      return "Start";
    case "fyi":
      return "FYI";
    case "terminal":
      return "Terminal";
    default:
      return "System event";
  }
}

/**
 * The task a system row is about: the row's own `slug`, else the prompt's "Task: <slug>" row, else
 * the older prompt shapes ("Report landed for `slug`", "[worker:slug]", "<project>/<slug>").
 */
export function subjectOf(row: Pick<ChatMessage, "text" | "slug">, project?: string): string | null {
  if (row.slug) return row.slug;
  const text = row.text;
  const patterns = [
    /^Task: ([A-Za-z0-9][A-Za-z0-9_.-]*)$/m,
    /^Report landed for `?([A-Za-z0-9][A-Za-z0-9_.-]*)`?[.:]/m,
    /\[(?:worker|task):([A-Za-z0-9][A-Za-z0-9_.-]*)\]/,
  ];
  if (project) {
    patterns.push(new RegExp(`\\b${project.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/([A-Za-z0-9][A-Za-z0-9_.-]*)`));
  }
  for (const pattern of patterns) {
    const slug = pattern.exec(text)?.[1];
    if (slug) return slug;
  }
  return null;
}

const FIELD = /^([A-Z][A-Za-z -]{1,30}): (.*)$/;

/** The prompt's "Label: value" rows when it has structured fields (three or more in a run), else null. */
export function fieldsOf(prompt: string): { label: string; value: string }[] | null {
  const rows: { label: string; value: string }[] = [];
  let seen = false;
  for (const line of prompt.split("\n")) {
    const match = FIELD.exec(line.trim());
    if (match) {
      seen = true;
      const [, label = "", value = ""] = match;
      if (label !== "Task") rows.push({ label, value });
    } else if (seen && line.trim() !== "") break;
  }
  return rows.length >= 3 ? rows : null;
}

/** The folded line's text (SPEC.md §4.1). */
export function lineText(turn: SystemTurn, task: string | null): string {
  if (turn.projectMessage) {
    const message = turn.projectMessage;
    const state = message.status === "sent" ? "Sent" : message.status === "queued" ? "Queued · next ordinary turn"
      : message.status === "registration-changed" ? "Registration changed · not supplied" : "Incoming";
    return `${message.sender} → ${message.recipient} · ${message.summary} · ${state}`;
  }
  if (turn.headsUp) return turn.prompt;
  if (turn.fyi) return lastParagraph(turn.prompt) || turn.prompt;
  if (turn.inProgress) return `L3 is handling ${handling(turn.trigger, task)}`;
  if (turn.reply) return lastParagraph(turn.reply) || `L3 handled ${handling(turn.trigger, task)}`;
  return `L3 could not handle ${handling(turn.trigger, task)}`;
}

function Dot({ trigger }: { trigger: string }) {
  return <span className="sys-dot" data-tone={dangerTrigger(trigger) ? "danger" : undefined} aria-hidden />;
}

/** The expanded card: header, what altd sent L3, the reply, the links. */
function SystemCard({
  turn,
  project,
  title,
  onHide,
}: {
  turn: SystemTurn;
  project: string;
  title: string | null;
  onHide: () => void;
}) {
  // The task's record names it and says whether a digest exists; read only while the card is open.
  const task = useTask(project, turn.slug ?? "");
  const name = task.data?.title || title || turn.slug;
  const at = stampText(turn.at) || "time unavailable";
  const fields = turn.fyi ? null : fieldsOf(turn.prompt);
  const digest = Boolean(task.data?.files?.digest);
  const label = kindLabel(turn.trigger);
  return (
    <article className="sys-card" aria-label={`${label}${name ? ` · ${name}` : ""}`}>
      <header className="sys-card-head">
        <span className="sys-card-title">
          <Dot trigger={turn.trigger} />
          {label}
          {name ? ` · ${name}` : ""}
          {` · ${at}`}
        </span>
        <button type="button" className="link" onClick={onHide}>
          Hide
        </button>
      </header>
      {turn.projectMessage ? (
        <div className="sys-card-body">
          <p>{lineText(turn, null)}</p>
          <p className="text-muted">Information only · Exchange {turn.projectMessage.exchange_id}</p>
          <ProseProject value={undefined}><ProseRepository value={null}>
            <Prose text={turn.prompt} document showLinkTargets />
          </ProseRepository></ProseProject>
        </div>
      ) : turn.fyi ? (
        <div className="sys-card-body">
          <Prose text={turn.prompt} />
        </div>
      ) : (
        <>
          <p className="sys-card-label">What altd sent L3</p>
          {fields ? (
            <dl className="sys-card-fields">
              {fields.map((row) => (
                <div key={row.label}>
                  <dt>{row.label}</dt>
                  <dd><InlineProse text={row.value} /></dd>
                </div>
              ))}
            </dl>
          ) : (
            <pre className="sys-card-pre">{turn.prompt}</pre>
          )}
          {turn.reply ? (
            <>
              <p className="sys-card-label">L3 replied</p>
              <div className="sys-card-body">
                <Prose text={turn.reply} />
              </div>
            </>
          ) : turn.error ? (
            <>
              <p className="sys-card-label">L3 could not answer</p>
              <p className="sys-card-error text-danger">{turn.error}</p>
            </>
          ) : null}
        </>
      )}
      {turn.slug ? (
        <footer className="sys-card-links">
          <Link to={`/projects/${project}/tasks/${turn.slug}`}>Open task</Link>
          {turn.trigger === "report-landed" ? (
            <Link to={`/projects/${project}/tasks/${turn.slug}/report`}>Full report</Link>
          ) : null}
          {digest ? <Link to={`/projects/${project}/tasks/${turn.slug}/report#digest`}>Digest</Link> : null}
        </footer>
      ) : null}
    </article>
  );
}

/** One system turn: the folded line, or the card. An in-progress turn has no Show yet. */
export function SystemLine({
  turn,
  project,
  titles,
}: {
  turn: SystemTurn;
  project: string;
  titles: Map<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const title = turn.slug ? (titles.get(turn.slug) ?? null) : null;
  if (open) return <SystemCard turn={turn} project={project} title={title} onHide={() => setOpen(false)} />;
  const text = lineText(turn, title ?? turn.slug);
  return (
    <div className="sys-line" data-turn={turn.id} data-progress={turn.inProgress || undefined}>
      <Dot trigger={turn.trigger} />
      <Stamp at={turn.at} className="sys-time" />
      <span className="sys-text">
        {turn.projectMessage ? text : <InlineProse text={text} />}
      </span>
      {turn.inProgress ? null : (
        <button type="button" className="link" onClick={() => setOpen(true)}>
          Show
        </button>
      )}
    </div>
  );
}

/** A run of routine system turns: one line, expanding to the list. */
export function SystemGroup({
  turns,
  project,
  titles,
}: {
  turns: SystemTurn[];
  project: string;
  titles: Map<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const danger = turns.some((turn) => dangerTrigger(turn.trigger));
  if (!open) {
    return (
      <div className="sys-line" data-group={turns.length}>
        <span className="sys-dot" data-tone={danger ? "danger" : undefined} aria-hidden />
        <Stamp at={turns.at(-1)?.at} className="sys-time" />
        <span className="sys-text">L3 handled {turns.length} system events between your messages</span>
        <button type="button" className="link" onClick={() => setOpen(true)}>
          Show
        </button>
      </div>
    );
  }
  return (
    <div className="sys-group" role="group" aria-label={`${turns.length} system events`}>
      {turns.map((turn) => (
        <SystemLine key={turn.id} turn={turn} project={project} titles={titles} />
      ))}
      <div className="sys-line">
        <span className="sys-text" />
        <button type="button" className="link" onClick={() => setOpen(false)}>
          Hide
        </button>
      </div>
    </div>
  );
}
