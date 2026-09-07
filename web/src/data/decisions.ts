import type { ChatMessage, Decision, DecisionOption, QueuedMessage, TaskMessage } from "./api";

/*
 * What a decision card and the decision page derive from the queue row (SPEC.md §3.8, §4.3): the kind
 * row, the options with the recommended one, and the follow-ups mirrored from the rows that carry the
 * decision's slug. Nothing here parses the question text; the server names the kind and the options.
 */

export type DecisionTone = "accent" | "claimed" | "danger";

export interface DecisionKind {
  label: string;
  tone: DecisionTone;
}

/** The kind row's label and colour (SPEC.md §3.8). */
export function decisionKind(decision: Pick<Decision, "kind" | "asked_by">): DecisionKind {
  switch (decision.kind) {
    case "fault":
      return { label: "Fault", tone: "danger" };
    case "stopped":
      return { label: "Stopped mid-task", tone: "danger" };
    case "review":
      return { label: "Ready for review", tone: "claimed" };
    default:
      return decision.asked_by === "l2" ? { label: "The L2 asks", tone: "accent" } : { label: "L3 asks", tone: "accent" };
  }
}

/** Who raised the decision, as the page and the follow-up pill name them. */
export function askerOf(decision: Pick<Decision, "asked_by">): "l3" | "l2" {
  return decision.asked_by === "l2" ? "l2" : "l3";
}

export function askerLabel(asker: "l3" | "l2"): string {
  return asker === "l2" ? "the L2" : "L3";
}

/** A block recorded without labelled options offers the two the lifecycle always has. */
export const DEFAULT_OPTIONS: DecisionOption[] = [
  { key: "resume", label: "Resume", text: "" },
  { key: "reject", label: "Reject", text: "" },
];

export function decisionOptions(decision: Pick<Decision, "options">): DecisionOption[] {
  return decision.options?.length ? decision.options : DEFAULT_OPTIONS;
}

/** The recommended option: the one the asker named, else the first (SPEC.md §3.8 "recommended one primary"). */
export function recommendedOption(decision: Pick<Decision, "options" | "recommendation">): DecisionOption {
  const options = decisionOptions(decision);
  const wanted = decision.recommendation?.option?.toLowerCase();
  return (wanted && options.find((o) => o.key?.toLowerCase() === wanted || o.label.toLowerCase() === wanted)) || options[0]!;
}

/** One follow-up and, once it exists, its answer (SPEC.md §4.3). */
/** Altitude's own actors on a task; any other author of a message or event is the operator. */
const AGENTS = new Set(["l2", "l3", "altd", "system"]);

export function isOperator(who: unknown): boolean {
  return typeof who === "string" && who !== "" && !AGENTS.has(who);
}

export interface FollowUp {
  id: string;
  to: "l3" | "l2";
  at: string | null;
  question: string;
  answer: string | null;
  /** When the answer arrived; null while it is still awaited. */
  answeredAt: string | null;
  /** The asker could not answer this one (an L3 turn failed). */
  failed: boolean;
  /** Sent while L3 was busy; it runs at the next turn boundary. */
  queued: boolean;
}

/**
 * The follow-ups mirrored on the card: chat rows carrying the decision's slug (To L3, one per turn)
 * and the task conversation's operator messages since the decision opened, each with the L2's next
 * reply (To the L2). Sorted by time.
 */
export function followUpsOf(
  decision: Pick<Decision, "slug" | "since">,
  chat: ChatMessage[],
  queued: QueuedMessage[],
  messages: TaskMessage[],
): FollowUp[] {
  const since = decision.since ?? "";
  const out: FollowUp[] = [];

  const turns = new Map<string, FollowUp>();
  for (const row of chat) {
    if (row.slug !== decision.slug || (row.trigger && row.trigger !== "chat")) continue;
    if (row.at && since && row.at < since) continue;
    const id = row.turn_id ?? `${row.at ?? ""}:${row.role}`;
    const turn = turns.get(id) ?? {
      id: `l3:${id}`,
      to: "l3" as const,
      at: row.at ?? null,
      question: "",
      answer: null,
      answeredAt: null,
      failed: false,
      queued: false,
    };
    if (row.role === "user") {
      turn.question = row.text;
      turn.at = row.at ?? turn.at;
    } else if (row.role === "assistant") {
      turn.answer = row.text;
      turn.answeredAt = row.at ?? null;
    } else if (row.role === "error") turn.failed = true;
    turns.set(id, turn);
  }
  for (const turn of turns.values()) if (turn.question) out.push(turn);
  for (const row of queued) {
    if (row.slug === decision.slug && row.trigger === "chat") {
      out.push({
        id: `queued:${row.id}`,
        to: "l3",
        at: row.at ?? null,
        question: row.text,
        answer: null,
        answeredAt: null,
        failed: false,
        queued: true,
      });
    }
  }
  const rows = messages.filter((m) => !since || !m.at || m.at >= since);
  rows.forEach((m, index) => {
    if (!isOperator(m.role)) return;
    const reply = rows.slice(index + 1).find((r) => r.role === "l2" || isOperator(r.role));
    out.push({
      id: `l2:${m.id}`,
      to: "l2",
      at: m.at ?? null,
      question: m.text,
      answer: reply?.role === "l2" ? reply.text : null,
      answeredAt: reply?.role === "l2" ? (reply.at ?? null) : null,
      failed: false,
      queued: false,
    });
  });

  return out.sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
}
