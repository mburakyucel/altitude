import { describe, expect, it } from "vitest";
import type { Decision, TaskRow } from "./api";
import { statusExcerpt, taskExplanation } from "./taskStatus";

const blocked: TaskRow = { slug: "verify-release", state: "blocked" };
const asked = "2026-10-07T04:03:00Z";
const question = (audience: "operator" | "l3", extra: Record<string, unknown> = {}) => ({
  id: "access-question", status: "open", audience, asked,
  detail: "Arrange approved machine access before installation checks can continue.", ...extra,
});

describe("status excerpts", () => {
  it.each([undefined, null, {}, "", "   ", "system fault [l2-died]: worker failed", "L2 worker abc ended", "worker state=failed; output"])(
    "does not promote absent or diagnostic text to primary prose: %j", (value) => {
      expect(statusExcerpt(value)).toBe("");
    },
  );

  it("keeps a useful first sentence and readable link text", () => {
    expect(statusExcerpt("L3: Wait for [release rc.2](https://example.test/release) to finish. Then resume the owner."))
      .toBe("Wait for release rc.2 to finish");
  });

  it.each(["\n", "\\n", "\\\"", "{", "}"])("excludes trailing event output after %j", (separator) => {
    expect(statusExcerpt(`Waiting for the installation check${separator}worker_id=private-detail`))
      .toBe("Waiting for the installation check");
  });

  it("keeps long authored prose bounded without losing its beginning", () => {
    const result = statusExcerpt(`Wait for the installation check ${"with additional context ".repeat(30)}`);
    expect(result).toMatch(/^Wait for the installation check /);
    expect(result.length).toBeLessThanOrEqual(120);
    expect(result).toMatch(/…$/);
  });

  it("leaves source references out of the primary explanation", () => {
    expect(statusExcerpt("Arrange approved machine access (abcdef12, earlier approval) before resuming."))
      .not.toContain("abcdef12");
  });
});

describe("task explanations", () => {
  it("uses the current coordinator prerequisite alongside an interrupted session, preserving all evidence", () => {
    const raw = 'system fault [l2-died]: L2 worker aabbccddeeff00112233445566778899 (attempt 1) ended without a fresh report: worker state=failed; elta\\\":100,\\\"session_id\\\":\\\"worker-fragment\\\"}';
    const task: TaskRow = {
      ...blocked, fault: "l2-died", waiting_on: "l3", blocked_reason: raw,
      questions: [question("l3")], hold_merge: "Operator reviews the completed change.",
    };
    const before = JSON.stringify(task);
    const explanation = taskExplanation(task);
    expect(explanation).toMatch(/session.*ended|interrupted/i);
    expect(explanation).toMatch(/coordinator/i);
    expect(explanation).toMatch(/approved machine access/i);
    expect(explanation).not.toMatch(/aabbccdd|attempt 1|worker state|l2-died|session_id|\\|reboot|restart|retry|recovered/i);
    expect(JSON.stringify(task)).toBe(before);
    expect(task["blocked_reason"]).toBe(raw);
  });

  it("keeps an unknown fault unknown, with raw details available unchanged", () => {
    const task = { ...blocked, fault: "unrecognized-code", waiting_on: "l3", blocked_reason: "Private diagnostic log 123456789abcdef012345" };
    const explanation = taskExplanation(task);
    expect(explanation).toMatch(/system problem/i);
    expect(explanation).toMatch(/coordinator/i);
    expect(explanation).not.toMatch(/unrecognized-code|Private diagnostic|reboot|retry|restart/i);
    expect(task.blocked_reason).toBe("Private diagnostic log 123456789abcdef012345");
  });

  it("states when no cause is recorded", () => {
    expect(taskExplanation(blocked)).toMatch(/no reason.*recorded/i);
  });

  it("distinguishes the operator's question from a coordinator prerequisite", () => {
    expect(taskExplanation({ ...blocked, questions: [question("operator")] })).toMatch(/your answer/i);
    const coordinator = taskExplanation({ ...blocked, waiting_on: "l3", questions: [question("l3")] });
    expect(coordinator).toMatch(/coordinator.*approved machine access/i);
    expect(coordinator).not.toMatch(/your answer|your input/i);
  });

  it("uses task-page question groups as well as row questions", () => {
    const member = question("l3");
    expect(taskExplanation({ ...blocked, question_group: { questions: [member] } }))
      .toBe(taskExplanation({ ...blocked, questions: [member] }));
  });

  it("does not ask again after a response or a handback", () => {
    const response = { message_id: "answer", text: "Approved." };
    expect(taskExplanation({ ...blocked, questions: [question("operator", { response })] }))
      .toMatch(/task owner.*reply/i);
    const handedBack = { ...blocked, handed_back: "2026-10-07T04:04:00Z", questions: [question("operator")] };
    expect(taskExplanation(handedBack)).not.toMatch(/your answer|your input/i);
    expect(taskExplanation({ ...handedBack, questions: [question("operator", { asked: "2026-10-07T04:05:00Z" })] }))
      .toMatch(/your answer/i);
  });

  it("does not revive an answered question from an older overview read", () => {
    const decision: Decision = { project: "atlas", slug: blocked.slug, id: "access-question", kind: "asks" };
    expect(taskExplanation({ ...blocked, questions: [question("operator", { response: { text: "Approved" } })] }, decision))
      .toMatch(/task owner.*reply/i);
    expect(taskExplanation({ ...blocked, handed_back: "2026-10-07T04:04:00Z", questions: [question("operator")] }, decision))
      .not.toMatch(/your answer/i);
  });

  it("shows a new overview question before the project read observes it", () => {
    const decision: Decision = { project: "atlas", slug: blocked.slug, id: "new-question", kind: "asks", asked: "2026-10-07T04:05:00Z" };
    expect(taskExplanation({ ...blocked, questions: [], handed_back: "2026-10-07T04:04:00Z" }, decision))
      .toMatch(/your answer/i);
  });

  it("ignores resolved coordinator questions", () => {
    const explanation = taskExplanation({ ...blocked, fault: "l2-died", questions: [question("l3", { status: "resolved" })] });
    expect(explanation).not.toMatch(/approved machine access/i);
  });

  it("retains an unanswered coordinator prerequisite after operator guidance hands back the turn", () => {
    expect(taskExplanation({ ...blocked, fault: "l2-died", handed_back: "2026-10-07T04:04:00Z", questions: [question("l3")] }))
      .toMatch(/Coordinator needed: Arrange approved machine access/);
  });

  it("keeps an unanswered prerequisite visible after another question is answered", () => {
    expect(taskExplanation({ ...blocked, questions: [question("operator", { response: { text: "Approved" } }), question("l3")] }))
      .toMatch(/Waiting for the coordinator: Arrange approved machine access/);
  });

  it("uses a prerequisite's human title without changing its original identifier", () => {
    const wait = { reason: "publish-package", after: "publish-package", after_title: "Publish the installation package" };
    expect(taskExplanation({ ...blocked, state: "queued", planned_wait: wait }))
      .toBe("Waiting for “Publish the installation package” to finish.");
    expect(wait.after).toBe("publish-package");
  });

  it.each(["done", "rejected", "running"])("does not revive an old blocker when state is %s", (state) => {
    expect(taskExplanation({ ...blocked, state, fault: "l2-died", questions: [question("operator")] })).toBeNull();
  });

  it("names a planned prerequisite without inventing a schedule", () => {
    const explanation = taskExplanation({ ...blocked, state: "queued", planned_wait: { reason: "the installation checks to finish", after: null } });
    expect(explanation).toMatch(/Waiting for the installation checks to finish/);
    expect(explanation).not.toMatch(/scheduled|retry|coordinator|your answer/i);
  });

  it("distinguishes an ordinary queue from a recorded resume", () => {
    expect(taskExplanation({ ...blocked, state: "queued" })).toMatch(/start the task/i);
    expect(taskExplanation({ ...blocked, resume_after: "2026-10-07T05:00:00Z" })).toMatch(/resume the task/i);
  });

  it("does not claim recovery merely because a fault has a resume time", () => {
    const explanation = taskExplanation({ ...blocked, fault: "l2-died", resume_after: "2026-10-07T05:00:00Z", questions: [question("l3")] });
    expect(explanation).toMatch(/approved machine access/i);
    expect(explanation).not.toMatch(/recovered|fixed|retry|Waiting for Altitude to resume/i);
  });

  it.each([
    ["stopping", /waiting for the session to end/i],
    ["stop_unconfirmed", /may still be running/i],
    ["stopped", /Stopped by you/i],
    ["resuming", /resume the task/i],
  ] as const)("respects recorded Stop state %s", (state, expected) => {
    expect(taskExplanation({ ...blocked, stop_id: "stop-request", steering: { state } })).toMatch(expected);
  });

  it("describes a Stop awaiting termination even while task state is running", () => {
    expect(taskExplanation({ ...blocked, state: "running", steering: { state: "stopping" }, stop_id: "stop-request" }))
      .toMatch(/waiting for the session to end/i);
  });

  it("does not infer completed stopping from a request identifier alone", () => {
    expect(taskExplanation({ ...blocked, stop_id: "stop-request" })).not.toMatch(/Stopped by you/i);
  });

  it("keeps a merge hold independent from faults, Stop and actual review readiness", () => {
    const hold = { hold_merge: "Wait for operator review before merging." };
    const fault = { ...blocked, fault: "l2-died" };
    expect(taskExplanation({ ...fault, ...hold })).toBe(taskExplanation(fault));
    const stopped = { ...blocked, steering: { state: "stopped" }, stop_id: "stop-request" };
    expect(taskExplanation({ ...stopped, ...hold })).toBe(taskExplanation(stopped));
    expect(taskExplanation({ ...blocked, ...hold })).not.toMatch(/your review/i);
    const review: Decision = { project: "atlas", slug: blocked.slug, kind: "review", pr: 12 };
    expect(taskExplanation({ ...blocked, ...hold }, review)).toMatch(/your review before merge/i);
  });
});
