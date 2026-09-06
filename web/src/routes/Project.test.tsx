import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { installVoiceBrowser } from "../components/voiceTest";

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
    if (url.includes("/api/transcribe")) return jsonResponse({ text: "spoken check" });
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
  it("renders the L3 card and the task list", async () => {
    mockFetch();
    renderApp({ route: "/projects/altitude" });

    await screen.findByRole("link", { name: "Fix the timer" });
    expect(screen.getByText("Tasks (2)")).toBeInTheDocument();
    expect(screen.getByText(/session abcdef12 · 12 turns · context 33% · last 7m/)).toBeInTheDocument();
    expect(screen.getByText(/approval: ask · WIP 2/)).toBeInTheDocument();
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

  it("uses the shared voice review in the project quick-message surface", async () => {
    installVoiceBrowser();
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await user.click(await screen.findByRole("button", { name: "Message L2" }));
    await user.type(screen.getByLabelText("Message the L2 on fix-timer"), "Typed lead");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    await screen.findByRole("region", { name: "Voice transcript review" });
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true);
    });
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      text: "Typed lead spoken check",
    });
  });

  it("drops a quick-message voice review when the project destination changes", async () => {
    installVoiceBrowser();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/transcribe")) return jsonResponse({ text: "altitude-only words" });
        if (url.includes("/api/project/sibling")) return jsonResponse({ ...project, name: "sibling" });
        if (url.includes("/api/project/altitude")) return jsonResponse(project);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );

    const { router, user } = renderApp({ route: "/projects/altitude" });
    await user.click(await screen.findByRole("button", { name: "Message L2" }));
    await user.type(screen.getByLabelText("Message the L2 on fix-timer"), "Private draft");
    await user.click(screen.getByRole("button", { name: "Start voice recording" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice recording" }));
    await screen.findByRole("region", { name: "Voice transcript review" });

    await router.navigate("/projects/sibling");
    await screen.findByRole("heading", { name: "sibling" });
    expect(screen.queryByRole("region", { name: "Voice transcript review" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Message L2" }));
    expect(screen.getByLabelText("Message the L2 on fix-timer")).toHaveValue("");
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

  // The link is present only when the project checkout has boards: it is the server's answer, not
  // a guess the page makes from the project name.
  it("opens the wireframe viewer in a new tab when the project has one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/project/altitude"))
          return jsonResponse({
            ...project,
            design_viewer: "/design/altitude/design/wireframes/index.html",
          });
        return jsonResponse({ error: "not found" }, 404);
      }),
    );
    renderApp({ route: "/projects/altitude" });

    const link = await screen.findByRole("link", { name: "Design" });
    expect(link).toHaveAttribute("href", "/design/altitude/design/wireframes/index.html");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link.className).toContain("hover:border-accent");
    expect(link.className).toContain("active:bg-accent-tint");
    expect(link.className).not.toContain("hover:text-accent"); // the label is accent already
  });

  it("shows no Design link for a project without boards", async () => {
    mockFetch();
    renderApp({ route: "/projects/altitude" });

    await screen.findByRole("link", { name: "Fix the timer" });
    expect(screen.queryByRole("link", { name: "Design" })).toBeNull();
  });
});
