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
  title: "Fix the timer",
  dispatch_id: "d-1",
  session_id: "0123456789abcdef",
  agent_id: "a-9",
  worktree: ".claude/worktrees/fix-timer",
  spend: { turns: 12, subagent_launches_reported: 2, edits_hook: 7, retries: 0 },
  live: {
    state: "running",
    l1_runs: 2,
    edits: 7,
    context_percent: 34,
    agent: { status: "working" },
  },
  files: { request: "REQUEST BODY", report: "REPORT BODY" },
  events: [
    { at: "2026-08-29T12:00:00", kind: "dispatched", detail: "worker up" },
    { at: "2026-08-29T12:05:00", kind: "progress" },
  ],
  report_json: { ok: true },
  messages: [
    { id: "m-1", at: "2026-08-29T12:01:00Z", role: "burak", text: "Keep the change focused." },
    { id: "m-2", at: "2026-08-29T12:02:00Z", role: "l2", text: "I will use one focused PR." },
  ],
};

const queued = { ...running, state: "approved", live: null };

const held = {
  ...running,
  state: "blocked",
  live: null,
  blocked_reason: "usage limit: the subscription window is exhausted, resets 2026-08-30T02:00",
  resume_after: "2026-08-30T02:00",
};

const stuck = {
  ...running,
  state: "blocked",
  live: null,
  blocked_reason: "the test suite will not run",
  resume_after: null,
};

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
  it("renders the task detail and optional L1 activity", async () => {
    stub(running);
    renderApp({ route });

    await screen.findByText("Fix the timer");
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getAllByText(/L1 runs 2/).length).toBeGreaterThan(0);
    expect(screen.getByText(/dispatch d-1/)).toBeInTheDocument();
    expect(screen.getByText(/session 01234567 /)).toBeInTheDocument();
    expect(screen.getByText(/attach: claude attach a-9/)).toBeInTheDocument();
    expect(screen.getByText("REQUEST BODY")).toBeInTheDocument();
    expect(screen.getByText("REPORT BODY")).toBeInTheDocument();
    expect(screen.getByText("Events (2)")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Task conversation" })).toBeInTheDocument();
    expect(screen.getByText("Keep the change focused.")).toBeInTheDocument();
    expect(screen.getByText("I will use one focused PR.")).toBeInTheDocument();
    expect(document.querySelector('[data-role="burak"]')).toHaveTextContent("You");
    expect(document.querySelector('[data-role="l2"]')).toHaveTextContent("L2");
    expect(screen.getByText(/dispatched \{"detail":"worker up"\}/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "‹ altitude" })).toHaveAttribute(
      "href",
      "/projects/altitude",
    );
  });

  it("dispatches a queued task", async () => {
    const fetchMock = stub(queued);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    await user.click(screen.getByRole("button", { name: "Dispatch" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      action: "dispatch",
    });
  });

  it("sends the typed reason with a park", async () => {
    const fetchMock = stub(queued);
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

  // Park and Reject are irreversible from here and used to fire on one unconfirmed click with an
  // optional reason; they now stay disabled until a reason is typed.
  it("refuses park and reject until a reason is typed", async () => {
    const fetchMock = stub(queued);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    const park = screen.getByRole("button", { name: "Park" });
    const reject = screen.getByRole("button", { name: "Reject" });
    expect(park).toBeDisabled();
    expect(reject).toBeDisabled();
    // a non-destructive action in the same row is unaffected
    expect(screen.getByRole("button", { name: "Dispatch" })).toBeEnabled();

    await user.click(park);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(false);

    await user.type(screen.getByLabelText("Reason"), "   ");
    expect(park).toBeDisabled();

    await user.type(screen.getByLabelText("Reason"), "waiting on the API");
    expect(park).toBeEnabled();
    expect(reject).toBeEnabled();
  });

  // Held by Altitude (blocked + resume_after) is queued, not stuck on you: the sentence says who
  // resumes it, and the line drops the danger colour a real block keeps.
  it("reads a held task as queued rather than blocked", async () => {
    stub(held);
    renderApp({ route });

    await screen.findByText("Fix the timer");
    const line = screen.getByText(
      /^Queued: Altitude resumes this L2 itself when the WIP \/ one-rule-task-at-a-time hold clears \(usage limit: /,
    );
    expect(line).toHaveClass("text-ink-2");
    expect(line).not.toHaveClass("text-danger");
    expect(screen.queryByText(/^Blocked: /)).toBeNull();
  });

  it("keeps the danger line for a blocked task with no resume", async () => {
    stub(stuck);
    renderApp({ route });

    await screen.findByText("Fix the timer");
    const line = screen.getByText("Blocked: the test suite will not run");
    expect(line).toHaveClass("text-danger");
    expect(document.body.textContent).not.toContain("one-rule-task-at-a-time");
  });

  it("messages the L2 while the task is running", async () => {
    const fetchMock = stub(running);
    const { user } = renderApp({ route });

    await screen.findByText("Fix the timer");
    expect(screen.queryByRole("button", { name: "Dispatch" })).toBeNull();

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
