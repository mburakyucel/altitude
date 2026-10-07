import { describe, expect, it } from "vitest";
import type { Decision, TaskRow } from "../data/api";
import { taskCardFacts } from "./TaskCard";

const blocked: TaskRow = { slug: "landing-window", state: "blocked" };
const question: Decision = { project: "atlas", slug: blocked.slug, id: "scope", kind: "asks" };

describe("blocked task card facts", () => {
  it("keeps coordination and operator questions distinct", () => {
    expect(taskCardFacts({ ...blocked, waiting_on: "l3" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "Waiting for the coordinator to resolve a blocker." });
    expect(taskCardFacts(blocked, undefined, "atlas", question))
      .toEqual({ dot: "idle", meta: "Waiting for your answer to the task’s question." });
    expect(taskCardFacts({ ...blocked, waiting_on: "l3" }, undefined, "atlas", question))
      .toEqual({ dot: "idle", meta: "Waiting for your answer to the task’s question." });
  });

  it.each(["l2", "l3", "altd", "operator", undefined])("shows an unassigned park recorded by %s as paused", (actor) => {
    expect(taskCardFacts({ ...blocked, block_actor: actor, waiting_on: null }, undefined, "atlas"))
      .toEqual({ dot: "idle", meta: "Work is paused; no reason is recorded." });
  });

  it.each([{ stop_id: "operator-stop" }, { steering: { state: "stopped" } }])("uses explicit stop evidence %j", (evidence) => {
    expect(taskCardFacts({ ...blocked, ...evidence, waiting_on: "l3" }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "steering" in evidence ? "Stopped by you; continue when you’re ready." : "You requested a stop; confirmation is in the task." });
  });

  it("recognizes a stopped decision and preserves fault priority", () => {
    expect(taskCardFacts(blocked, undefined, "atlas", { ...question, kind: "stopped" }))
      .toEqual({ dot: "danger", meta: "You requested a stop; confirmation is in the task." });
    expect(taskCardFacts({ ...blocked, block_actor: "altd", fault: "checkout", blocked_reason: "Checkout unavailable." }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "A system problem paused work. Waiting for the coordinator to check the blocker." });
  });

  it("does not call a requested or unconfirmed Stop working, or hide a requested continuation", () => {
    expect(taskCardFacts({ ...blocked, state: "running", stop_id: "stop" }, undefined, "atlas").meta)
      .toMatch(/requested a stop/);
    expect(taskCardFacts({ ...blocked, state: "running", steering: { state: "stop_unconfirmed" } }, undefined, "atlas").meta)
      .toMatch(/may still be running/);
    expect(taskCardFacts({ ...blocked, stop_id: "stop", resume_after: "2026-10-07T05:00:00Z" }, undefined, "atlas").meta)
      .toMatch(/Waiting to resume/);
  });

  it("says the L2 is working or replying, and a report waiting on a review is the operator's turn", () => {
    expect(taskCardFacts({ slug: "landing-window", state: "running" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "L2 working" });
    expect(taskCardFacts({ slug: "landing-window", state: "running", handed_back: "2026-09-01T10:00:00Z" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "L2 replying to you" });
    expect(taskCardFacts({ ...blocked, resume_after: "operator", handed_back: "2026-09-01T10:00:00Z" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "L2 replying to you" });
    const review: Decision = { project: "atlas", slug: blocked.slug, kind: "review", pr: 12 };
    expect(taskCardFacts({ slug: "landing-window", state: "reported" }, undefined, "atlas", review))
      .toEqual({ dot: "running", meta: "Waiting for your review before merge." });
    expect(taskCardFacts({ slug: "landing-window", state: "reported" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "Waiting for the coordinator to check the task’s report." });
  });
});
