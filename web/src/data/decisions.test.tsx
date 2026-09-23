import { describe, expect, it } from "vitest";
import type { Decision } from "./api";
import { attentionSummary, decisionKind, turnLabel } from "./decisions";

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

  it("counts reviews apart from questions, stops and faults", () => {
    const stopped: Decision = { project: "atlas", slug: "halted", kind: "stopped" };
    const fault: Decision = { project: "atlas", slug: "broken", kind: "fault" };
    expect(attentionSummary([review])).toBe("1 review");
    expect(attentionSummary([question("scope"), review, { ...review, slug: "other", pr: 13 }, stopped, fault]))
      .toBe("1 question · 2 reviews · 1 stopped task · 1 fault");
    expect(decisionKind(review)).toEqual({ label: "Review before merge", tone: "claimed" });
  });
});
