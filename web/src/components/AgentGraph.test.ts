import { describe, expect, it } from "vitest";
import { ACTIVE_MS, FRESH_MS, nodeLabel, ringOrder, ringPoints, stateLine, taskActivity, trackLength } from "./AgentGraph";
import type { GraphNode } from "./AgentGraph";
import type { TaskRow } from "../data/api";

const now = Date.parse("2026-10-22T12:00:00Z");
const ago = (ms: number) => new Date(now - ms).toISOString();
const helper = (owner: string, at: string) => ({ engine: "fixture", session_id: `${owner}-h-${at}`, role: "delegated",
  owner_session_id: owner, observed_at: at });
const running = (usage: Record<string, unknown> | null, state = "running"): TaskRow => ({
  slug: "sync", state, session_id: "owner",
  token_usage: usage && {
    checked_at: ago(5_000),
    sessions: [{ engine: "fixture", session_id: "owner", role: "owner", observed_at: ago(10_000) }],
    helpers: { status: "partial", observed_count: 3, direct_count: 3, descendant_count: 0, total_tokens: null,
      sessions: [helper("owner", ago(5_000)), helper("owner", ago(ACTIVE_MS + 1)), helper("earlier-owner", ago(1_000))] },
    ...usage,
  },
});

describe("live cues from the usage reading", () => {
  it("counts the current worker's helpers that made a request within a minute", () => {
    expect(taskActivity(running({}), now, false)).toEqual({ live: true, helpers: 1 });
  });

  it("claims nothing from an aged reading or a saved read", () => {
    expect(taskActivity(running({ checked_at: ago(FRESH_MS + 1) }), now, false)).toEqual({ live: false, helpers: 0 });
    expect(taskActivity(running({}), now, true)).toEqual({ live: false, helpers: 0 });
  });

  it("is quiet when the worker's newest request is over a minute old", () => {
    const quiet = running({ sessions: [{ engine: "fixture", session_id: "owner", role: "owner", observed_at: ago(ACTIVE_MS + 1) }] });
    expect(taskActivity(quiet, now, false).live).toBe(false);
  });

  it("tells no helper evidence from no helpers", () => {
    expect(taskActivity(running({ helpers: { status: "unknown", observed_count: null, direct_count: null, descendant_count: null,
      total_tokens: null, sessions: [] } }), now, false).helpers).toBeNull();
    expect(taskActivity(running(null), now, false).helpers).toBeNull();
    expect(taskActivity(running({ helpers: { status: "partial", observed_count: 0, direct_count: 0, descendant_count: 0,
      total_tokens: null, sessions: [] } }), now, false).helpers).toBe(0);
  });

  it("shows nothing for work that is not running", () => {
    expect(taskActivity(running({}, "blocked"), now, false)).toEqual({ live: false, helpers: 0 });
    expect(taskActivity(running(null, "queued"), now, false)).toEqual({ live: false, helpers: 0 });
  });
});

describe("node words", () => {
  it("keeps the list's words: attention whole, else the meta's head or a fault's first sentence", () => {
    expect(stateLine("Your turn · 1 question", "Waiting for your answer.")).toBe("Your turn · 1 question");
    expect(stateLine(null, "L2 working · Opus on Fixture · started 2 min ago")).toBe("L2 working");
    expect(stateLine(null, "Planned · Waiting for “Rework” to finish.")).toBe("Planned");
    expect(stateLine(null, "The task session ended before completion. Waiting for the coordinator.")).toBe("The task session ended before completion");
  });

  const node = (key: string, dot: GraphNode["dot"], helpers: number | null = 0): GraphNode => ({
    key, title: key, attention: dot === "waiting" ? "Your turn · 1 question" : null, meta: "L2 working", dot, to: "/", linkState: null,
    questions: 0, run: false, after: null, live: false, helpers,
  });

  it("names helpers, or that their activity is unknown", () => {
    expect(nodeLabel(node("Sync", "running", 2))).toBe("Sync · L2 working · 2 helpers active");
    expect(nodeLabel(node("Sync", "running", 1))).toBe("Sync · L2 working · 1 helper active");
    expect(nodeLabel(node("Sync", "running", null))).toBe("Sync · L2 working · helper activity unknown");
  });

  it("puts the operator's turns first and keeps the rest in the list's order", () => {
    const order = ringOrder([node("a", "running"), node("b", "idle"), node("c", "waiting"), node("d", "danger")]);
    expect(order.map((n) => n.key)).toEqual(["c", "a", "b", "d"]);
  });
});

describe("the ring", () => {
  it("is a circle up to seven tasks and a racetrack that keeps neighbours apart beyond", () => {
    expect(trackLength(7)).toBe(0);
    expect(trackLength(13)).toBeGreaterThan(0);
    for (const n of [3, 7, 13]) {
      const points = ringPoints(n, 224, 300);
      expect(points[0]).toEqual([224, 300 - 150 - trackLength(n) / 2]);
      const gaps = points.map(([x, y], i) => Math.hypot(x - points[(i + 1) % n]![0], y - points[(i + 1) % n]![1]));
      expect(Math.min(...gaps)).toBeGreaterThan(100);
    }
  });
});
