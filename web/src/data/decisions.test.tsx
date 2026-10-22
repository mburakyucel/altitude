import { describe, expect, it } from "vitest";
import type { AgentRow, Decision, Step } from "./api";
import { attentionPath, attentionSummary, decisionGroups, decisionKind, headline, isAgentRow, isStep, isTaskDecision, turnLabel } from "./decisions";

const question = (id: string): Decision => ({ project: "atlas", slug: "landing-window", id, kind: "asks" });
const review: Decision = { project: "atlas", slug: "landing-window", kind: "review", pr: 12, question: "Review PR #12 before merge" };

describe("the operator's turn", () => {
  it("names questions, a review, or both", () => {
    expect(turnLabel([])).toBeNull();
    expect(turnLabel([question("scope")])).toBe("Your turn · 1 question");
    expect(turnLabel([question("scope"), question("region")])).toBe("Your turn · 2 questions");
    expect(turnLabel([review])).toBe("Your turn · review PR #12");
    expect(turnLabel([question("scope"), review])).toBe("Your turn · 1 question · review PR #12");
  });

  it("names a lone question on a task card, so a wait on the operator's step says the step", () => {
    const step = { ...question("sign-in"), question: "Run `example-tool sign-in` in this task's terminal.\n\n```run\nexample-tool sign-in\n```" };
    expect(turnLabel([step], true)).toBe("Your turn · Run `example-tool sign-in` in this task's terminal.");
    expect(turnLabel([step, review], true)).toBe("Your turn · Run `example-tool sign-in` in this task's terminal. · review PR #12");
    // altitude/tasks.py `headline` (tests/test_decisions.py) cuts these same examples to the same text.
    const long = `Restore the checkout to 1a2b3c4d5e from the step posted in project chat, then ${"wait ".repeat(20)}`;
    expect(["Run this in the task terminal: `example-tool sign-in`.\nThen wait.", long].map(headline)).toEqual([
      "Run this in the task terminal: `example-tool sign-in`.",
      "Restore the checkout to 1a2b3c4d5e from the step posted in project chat, then wait wait wait wait wait wait wait wait…",
    ]);
    expect(turnLabel([step, question("region")], true)).toBe("Your turn · 2 questions");
    expect(turnLabel([step])).toBe("Your turn · 1 question");
    const choice = { ...step, options: [{ key: "a", label: "Approve", text: "Approve it." }] };
    expect(turnLabel([choice], true)).toBe("Your turn · 1 question");
  });

  it("names an operator step on the task it names, and counts steps apart from questions", () => {
    const step: Step = {
      project: "atlas", slug: null, kind: "step", id: "s1", asked_by: "l3", asked: "2026-10-15T09:00:00Z", alert_key: "atlas:step:s1",
      question: "Restore the service checkout.\n\n```run\nexample-tool repair-checkout\n```", tasks: [{ slug: "landing-window", title: "Landing window" }],
    };
    expect(isStep(step)).toBe(true);
    expect(isStep(review)).toBe(false);
    expect(turnLabel([step], true)).toBe("Your turn · Restore the service checkout.");
    expect(turnLabel([question("scope"), step], true)).toBe("Your turn · 1 question · Restore the service checkout.");
    expect(turnLabel([step])).toBe("Your turn · 1 step");
    expect(attentionSummary([question("scope"), step, review])).toBe("1 question · 1 step · 1 review");
    expect(attentionPath(step)).toBe("/");
  });

  it("counts a routine that paused itself apart from questions, and never as a task's turn", () => {
    const paused: AgentRow = { project: "atlas", slug: null, kind: "agent", id: "agent:flaky", agent: "flaky", title: "Flaky tests", run: 7,
      question: "3 runs in a row couldn't finish.", asked_by: "altitude", asked: "2026-10-15T09:00:00Z", since: "2026-10-15T09:00:00Z" };
    expect([isAgentRow(paused), isStep(paused), isTaskDecision(paused)]).toEqual([true, false, false]);
    expect(isTaskDecision(review)).toBe(true);
    expect(attentionSummary([question("scope"), paused])).toBe("1 question · 1 paused routine");
    expect(attentionSummary([paused, { ...paused, id: "agent:other" }])).toBe("2 paused routines");
    expect(turnLabel([paused])).toBeNull();
    expect(decisionGroups([paused, { ...paused, id: "agent:other" }, question("scope")])).toHaveLength(3);
    expect(decisionKind(paused)).toEqual({ label: "Routine paused itself", tone: "danger" });
    expect(attentionPath(paused)).toBe("/");
  });

  it("counts reviews apart from questions, stops and faults", () => {
    const stopped: Decision = { project: "atlas", slug: "halted", kind: "stopped" };
    const fault: Decision = { project: "atlas", slug: "broken", kind: "fault" };
    expect(attentionSummary([review])).toBe("1 review");
    expect(attentionSummary([question("scope"), review, { ...review, slug: "other", pr: 13 }, stopped, fault]))
      .toBe("1 question · 2 reviews · 1 stopped task · 1 fault");
    expect(decisionKind(review)).toEqual({ label: "Review before merge", tone: "claimed" });
  });
});
