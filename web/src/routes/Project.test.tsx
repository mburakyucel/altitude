import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const project = {
  name: "altitude",
  config: { approval: "ask", wip: 2 },
  l3: { session_id: "abcdef1234567890", turns: 12, context_percent: 33, last_turn: ago(7) },
  busy: false,
  tasks: [
    {
      slug: "fix-timer",
      state: "running",
      title: "Fix the timer",
      updated: ago(2),
      live: {
        agent: { status: "active", state: "tool" },
        context_percent: 44,
        l1_runs: 1,
        edits: 7,
      },
    },
    { slug: "add-badge", state: "queued", title: "Add the badge", updated: ago(30) },
  ],
  archive: [{ slug: "old-thing", state: "done", title: "Old thing" }],
  inbox: [{ at: ago(9), slug: "fix-timer", text: "L2 picked it up" }],
  decisions: [
    {
      slug: "add-badge",
      title: "Add the badge",
      question: "Stopped mid-task: Which badge color should be used?",
      asked: ago(4),
      options: ["Resume", "Reject"],
      kind: "blocked",
    },
  ],
  incidents: [{ id: "INC-1", title: "altd restarted", tags: ["restart"] }],
  hold: null,
  state_md: "# STATE\nall good",
};

const overview = {
  projects: [],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
};

function mockFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/project/altitude")) return jsonResponse(project);
    if (url.includes("/api/task/action")) return jsonResponse({ ok: true });
    if (url.includes("/api/l2/message")) return jsonResponse({ ok: true });
    if (url.includes("/api/decide")) return jsonResponse({ ok: true });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The <article> a task title link sits in — the element carrying the card's styling. */
function cardFor(title: HTMLElement): HTMLElement {
  const card = title.closest("article");
  if (!card) throw new Error("task title is not inside a card");
  return card;
}

describe("Project", () => {
  it("renders the L3 card, the task list and optional L1 activity", async () => {
    mockFetch();
    renderApp({ route: "/projects/altitude" });

    await screen.findByRole("link", { name: "Fix the timer" });
    expect(screen.getByText("Tasks (2)")).toBeInTheDocument();
    expect(screen.getByText(/session abcdef12 · 12 turns · context 33% · last 7m/)).toBeInTheDocument();
    expect(screen.getByText(/approval: ask · WIP 2/)).toBeInTheDocument();
    expect(screen.getByText(/L1 runs 1/)).toBeInTheDocument();
    expect(screen.getByText(/L2 active\/tool · ctx 44%/)).toBeInTheDocument();
    expect(screen.getByText("Needs you (1)")).toBeInTheDocument();
    expect(screen.getByText("L2 picked it up")).toBeInTheDocument();
    expect(screen.getByText(/INC-1/)).toBeInTheDocument();
    expect(screen.getByText("Done / rejected (1)")).toBeInTheDocument();
  });

  it("dispatches a queued task through /api/task/action", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await user.click(await screen.findByRole("button", { name: "Dispatch" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "add-badge",
      action: "dispatch",
    });
  });

  it("sends a note into a running L2", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await user.click(await screen.findByRole("button", { name: "Message L2" }));
    await user.type(screen.getByLabelText("Message the L2 on fix-timer"), "check the toast timer");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      text: "check the toast timer",
    });
  });

  // A blocked task with resume_after is held by Altitude, not stuck on you: it says so and keeps
  // the neutral card. A blocked task without one is still a real block, danger border and all.
  it("separates a held task from a blocked one", async () => {
    const held = {
      slug: "held-task",
      state: "blocked",
      title: "Held task",
      updated: ago(3),
      blocked_reason: "usage limit: the subscription window is exhausted, resets 2026-08-30T02:00",
      resume_after: "2026-08-30T02:00",
    };
    const stuck = {
      slug: "stuck-task",
      state: "blocked",
      title: "Stuck task",
      updated: ago(4),
      blocked_reason: "the test suite will not run",
      resume_after: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/project/altitude"))
          return jsonResponse({ ...project, tasks: [held, stuck], decisions: [] });
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route: "/projects/altitude" });

    const heldCard = cardFor(await screen.findByRole("link", { name: "Held task" }));
    expect(
      within(heldCard).getByText(
        /^queued: Altitude resumes this L2 itself when the operational hold clears \(usage limit: /,
      ),
    ).toBeInTheDocument();
    expect(within(heldCard).queryByText(/^blocked: /)).toBeNull();
    expect(heldCard).not.toHaveClass("border-danger/40");

    const stuckCard = cardFor(screen.getByRole("link", { name: "Stuck task" }));
    expect(within(stuckCard).getByText("blocked: the test suite will not run")).toBeInTheDocument();
    expect(within(stuckCard).queryByText(/operational hold/)).toBeNull();
    expect(stuckCard).toHaveClass("border-danger/40");
  });

  it("creates a new task directly", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await user.type(await screen.findByLabelText("New request"), "Ship the badge");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/task/action"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "",
      action: "new",
      title: "Ship the badge",
      request: "Ship the badge",
    });
  });
});
