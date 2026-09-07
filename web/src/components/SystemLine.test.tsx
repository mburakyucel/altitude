import { describe, expect, it } from "vitest";
import { fieldsOf, handling, lineText, subjectOf } from "./SystemLine";
import type { SystemTurn } from "./SystemLine";
import { lastParagraph } from "./Prose";

const turn = (over: Partial<SystemTurn>): SystemTurn => ({
  id: "t1",
  trigger: "report-landed",
  at: null,
  prompt: "",
  reply: null,
  error: null,
  inProgress: false,
  slug: null,
  fyi: false,
  ...over,
});

describe("system line text (SPEC.md §3.4, §4.1)", () => {
  it("reads the reply's last paragraph, skipping code blocks", () => {
    expect(lastParagraph("First.\n\nSecond line\ncontinues.\n\n```\ncode\n```\n")).toBe("Second line continues.");
    expect(lineText(turn({ reply: "Checked it.\n\nClosed as done." }), "Fix the timer")).toBe("Closed as done.");
  });

  it("says what L3 is handling while the turn runs and what it could not handle after a failure", () => {
    expect(lineText(turn({ inProgress: true }), "Fix the timer")).toBe("L3 is handling a landed report for Fix the timer");
    expect(lineText(turn({ trigger: "block", inProgress: true }), "Fix the timer")).toBe("L3 is handling a block on Fix the timer");
    expect(lineText(turn({ trigger: "incident", inProgress: true }), null)).toBe("L3 is handling a fault");
    expect(lineText(turn({ trigger: "restart", inProgress: true }), null)).toBe("L3 is handling the restart");
    expect(lineText(turn({ trigger: "system-recovery", error: "engine timed out" }), null)).toBe("L3 could not handle a recovery");
    expect(handling("start")).toBe("the start");
    expect(handling("other")).toBe("a system event");
  });

  it("finds the turn's task from the row's slug, the Task row, or the older prompt shapes", () => {
    expect(subjectOf({ text: "anything", slug: "fix-timer" })).toBe("fix-timer");
    expect(subjectOf({ text: "Report landed for fix-timer.\nTask: fix-timer\nVerdict: done" })).toBe("fix-timer");
    expect(subjectOf({ text: "Report landed for `old-shape`: verdict **done**" })).toBe("old-shape");
    expect(subjectOf({ text: "System fault [worker:give-the-chat] in altitude/give-the-chat: boom" })).toBe("give-the-chat");
    expect(subjectOf({ text: "L2 blocked on altitude/persist-paths with a question" }, "altitude")).toBe("persist-paths");
    expect(subjectOf({ text: "Altitude restarted with the code now on main." }, "altitude")).toBeNull();
  });

  it("reads label/value rows from a structured prompt and leaves the Task id to the links", () => {
    const prompt = [
      "Report landed for fix-timer.",
      "Task: fix-timer",
      "Verdict: done",
      "Problems: none",
      "Post-mortem signals: none",
      "PRs: #12 merged",
      "Spend: 3 turns",
      "",
      "Read the full report with `alt task report fix-timer`.",
    ].join("\n");
    expect(fieldsOf(prompt)).toEqual([
      { label: "Verdict", value: "done" },
      { label: "Problems", value: "none" },
      { label: "Post-mortem signals", value: "none" },
      { label: "PRs", value: "#12 merged" },
      { label: "Spend", value: "3 turns" },
    ]);
    expect(fieldsOf("Report landed for `x`: verdict **done** {json}")).toBeNull();
  });
});
