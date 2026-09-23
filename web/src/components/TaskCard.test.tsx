import { describe, expect, it } from "vitest";
import type { Decision, TaskRow } from "../data/api";
import { taskCardFacts } from "./TaskCard";

const blocked: TaskRow = { slug: "landing-window", state: "blocked" };
const question: Decision = { project: "atlas", slug: blocked.slug, id: "scope", kind: "asks" };

describe("blocked task card facts", () => {
  it("keeps coordination and operator questions distinct", () => {
    expect(taskCardFacts({ ...blocked, waiting_on: "l3" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "Waits for L3" });
    expect(taskCardFacts(blocked, undefined, "atlas", question))
      .toEqual({ dot: "idle", meta: "Waiting for you" });
    expect(taskCardFacts({ ...blocked, waiting_on: "l3" }, undefined, "atlas", question))
      .toEqual({ dot: "idle", meta: "Waiting for you" });
  });

  it.each(["l2", "l3", "altd", "operator", undefined])("shows an unassigned park recorded by %s as paused", (actor) => {
    expect(taskCardFacts({ ...blocked, block_actor: actor, waiting_on: null }, undefined, "atlas"))
      .toEqual({ dot: "idle", meta: "Paused" });
  });

  it.each([{ stop_id: "operator-stop" }, { steering: { state: "stopped" } }])("uses explicit stop evidence %j", (evidence) => {
    expect(taskCardFacts({ ...blocked, ...evidence, waiting_on: "l3" }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "Stopped by you" });
  });

  it("recognizes a stopped decision and preserves fault priority", () => {
    expect(taskCardFacts(blocked, undefined, "atlas", { ...question, kind: "stopped" }))
      .toEqual({ dot: "danger", meta: "Stopped by you" });
    expect(taskCardFacts({ ...blocked, block_actor: "altd", fault: "checkout", blocked_reason: "Checkout unavailable." }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "Paused · Checkout unavailable." });
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
      .toEqual({ dot: "running", meta: "Report landed · waiting for you" });
    expect(taskCardFacts({ slug: "landing-window", state: "reported" }, undefined, "atlas"))
      .toEqual({ dot: "running", meta: "Report landed · waits for L3" });
  });
});
