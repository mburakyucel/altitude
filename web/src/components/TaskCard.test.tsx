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
      .toEqual({ dot: "idle", meta: "Waiting for your answer" });
  });

  it.each(["l2", "l3", "altd", "operator", undefined])("shows an unassigned park recorded by %s as paused", (actor) => {
    expect(taskCardFacts({ ...blocked, block_actor: actor, waiting_on: null }, undefined, "atlas"))
      .toEqual({ dot: "idle", meta: "Paused" });
  });

  it.each([{ stop_id: "operator-stop" }, { steering: { state: "stopped" } }])("uses explicit stop evidence %j", (evidence) => {
    expect(taskCardFacts({ ...blocked, ...evidence, waiting_on: "l3" }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "Stopped" });
  });

  it("recognizes a stopped decision and preserves fault priority", () => {
    expect(taskCardFacts(blocked, undefined, "atlas", { ...question, kind: "stopped" }))
      .toEqual({ dot: "danger", meta: "Stopped" });
    expect(taskCardFacts({ ...blocked, block_actor: "altd", fault: "checkout", blocked_reason: "Checkout unavailable." }, undefined, "atlas"))
      .toEqual({ dot: "danger", meta: "Blocked: Checkout unavailable." });
  });
});
