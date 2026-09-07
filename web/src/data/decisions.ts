import type { Decision } from "./api";

/**
 * The kind row of a decision card (SPEC.md §3.8). Today's queue rows carry the kind inside the
 * question ("L3 asks: …", "Stopped mid-task: …"); slice 3 gives it its own field, and this is the
 * one place that reads the prefix until then.
 */
export type DecisionTone = "accent" | "claimed" | "danger";

export interface DecisionKind {
  label: string;
  tone: DecisionTone;
  /** The question with the kind prefix removed. */
  question: string;
}

const KINDS: Array<[prefix: string, label: string, tone: DecisionTone]> = [
  ["L3 asks: ", "L3 asks", "accent"],
  ["Stopped mid-task: ", "Stopped mid-task", "danger"],
  ["Fault: ", "Fault", "danger"],
  ["Ready for review: ", "Ready for review", "claimed"],
];

/** Sentence case for the stripped question (SPEC.md §4.5): the prefix carried the capital. */
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function decisionKind(decision: Pick<Decision, "question" | "kind">): DecisionKind {
  const question = decision.question ?? "";
  for (const [prefix, label, tone] of KINDS) {
    if (question.startsWith(prefix)) return { label, tone, question: sentence(question.slice(prefix.length)) };
  }
  if (decision.kind === "fault") return { label: "Fault", tone: "danger", question };
  return { label: "L3 asks", tone: "accent", question };
}

export const DEFAULT_OPTIONS = ["Resume", "Reject"];
