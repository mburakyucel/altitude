import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

const running = {
  slug: "fix-timer",
  state: "running",
  class: "S",
  title: "Fix the timer",
  dispatch_id: "d-1",
  session_id: "0123456789abcdef",
  agent_id: "a-9",
  worktree: ".claude/worktrees/fix-timer",
  envelope: { l1_in_flight: 1, subagent_launches: 3, max_turns: 40, verification: "reviewer" },
  spend: { turns: 12, subagent_launches_hook: 2, edits_hook: 7, retries: 0, cap: 3 },
  live: {
    state: "running",
    subagent_launches: 2,
    cap: 3,
    edits: 7,
    context_percent: 34,
    agent: { status: "working" },
  },
  files: { proposal: "PROPOSAL BODY", report: "REPORT BODY" },
  events: [
    { at: "2026-08-29T12:00:00", kind: "dispatched", detail: "worker up" },
    { at: "2026-08-29T12:05:00", kind: "progress" },
  ],
  critique: { verdict: "revise", notes: ["tighten the brief"] },
  report_json: { ok: true },
};

const proposed = { ...running, state: "proposed", live: null };

function stub(task: unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/task/action")) return jsonResponse({ ok: true, state: "approved" });
    if (url.includes("/api/task/")) return jsonResponse(task);
    if (url.includes("/api/l2/message")) return jsonResponse({ ok: true });
    if (url.includes("/api/project/")) return jsonResponse({ name: "altitude", tasks: [] });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const route = "/projects/altitude/tasks/fix-timer";

describe("Task", () => {
  it("renders the task detail and spells subagent launches as 'launches N · cap M'", async () => {
    stub(running);
    renderApp({ route });

    await screen.findByText("Fix the timer");
    expect(screen.getByText("S")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getAllByText("launches 2 · cap 3").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("2/3");
    expect(screen.getByText(/dispatch d-1/)).toBeInTheDocument();
    expect(screen.getByText(/session 01234567 /)).toBeInTheDocument();
    expect(screen.getByText(/attach: claude attach a-9/)).toBeInTheDocument();
    expect(screen.getByText("PROPOSAL BODY")).toBeInTheDocument();
    expect(screen.getByText("REPORT BODY")).toBeInTheDocument();
    expect(screen.getByText("critique (revise)")).toBeInTheDocument();
    expect(screen.getByText("Events (2)")).toBeInTheDocument();
    expect(screen.getByText(/dispatched \{"detail":"worker up"\}/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "‹ altitude" })).toHaveAttribute(
      "href",
      "/projects/altitude",
    );
  });

  it("posts the build-now override for a proposed task", async () => {
    const fetchMock = stub(proposed);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    await user.click(screen.getByRole("button", { name: "Build now" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      action: "build",
    });
  });

  it("sends the typed reason with a park", async () => {
    const fetchMock = stub(proposed);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    await user.type(screen.getByLabelText("Reason"), "waiting on the API");
    await user.click(screen.getByRole("button", { name: "Park" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      action: "park",
      reason: "waiting on the API",
    });
  });

  it("messages the L2 while the task is running", async () => {
    const fetchMock = stub(running);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    expect(screen.queryByRole("button", { name: "Build now" })).toBeNull();

    await user.type(screen.getByLabelText("Message the L2"), "prefer the smaller diff");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      text: "prefer the smaller diff",
    });
  });
});
