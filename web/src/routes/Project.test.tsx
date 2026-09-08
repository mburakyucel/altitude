import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

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
  archive: [
    { slug: "old-thing", state: "done", title: "Old thing", updated: ago(60 * 24 * 2) },
    { slug: "older-thing", state: "done", title: "Older thing", updated: ago(60 * 24 * 20) },
  ],
  decisions: [],
  incidents: [],
  hold: null,
  state_md: "# STATE\nall good",
};

const decision = {
  id: "q-badge", revision: 1, anchor_id: "question-message", status: "open", audience: "operator",
  project: "altitude",
  slug: "add-badge",
  title: "Add the badge",
  kind: "asks",
  asked_by: "l3",
  question: "Which badge colour should the count use?",
  detail: "The boards show accent; the old build used amber.",
  asked: ago(4),
  since: ago(4),
  recommendation: { text: "Use accent.", label: "Resume", why: "The boards show accent; the old build used amber." },
};

const overview = {
  projects: [{ name: "altitude", managed: true, counts: { running: 1 } }],
  queue: [decision],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: 52, known: true, stale: false, at: ago(1) }],
  roots: ["~/Projects"],
  operator: "Ada",
};

const chatView = {
  history: [
    { at: ago(5), role: "user", text: "how is it going?" },
    { at: ago(4), role: "assistant", text: "two tasks running.", trigger: "chat", engine: "alpha" },
  ],
  active: null,
  busy: false,
  l3: { session_id: "abcdef1234567890" },
};

type Fixtures = { overview?: unknown; project?: unknown; chat?: unknown };

function mockFetch(fixtures: Fixtures = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(fixtures.overview ?? overview);
    if (url.includes("/api/transcribe")) return jsonResponse({ text: "spoken check" });
    if (url.includes("/api/project/sibling")) return jsonResponse({ ...project, name: "sibling" });
    if (url.includes("/api/project/altitude")) return jsonResponse(fixtures.project ?? project);
    if (url.includes("/api/chat/")) return jsonResponse(fixtures.chat ?? chatView);
    if (url.includes("/api/task/action")) return jsonResponse({ ok: true });
    if (url.includes("/api/l2/message")) return jsonResponse({ ok: true });
    if (url.includes("/api/l3/")) return jsonResponse({ ok: true });
    if (url.includes("/api/project/remove")) return jsonResponse({ ok: true });
    if (url.includes("/api/decide")) return jsonResponse({ ok: true, question: { ...decision, status: "resolved" } });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posted(fetchMock: ReturnType<typeof vi.fn>, path: string) {
  const call = fetchMock.mock.calls.find(([u]) => String(u).includes(path));
  return call ? JSON.parse(String((call[1] as RequestInit | undefined)?.body)) : null;
}

async function openPanel(user: ReturnType<typeof renderApp>["user"]) {
  await user.click(await screen.findByRole("button", { name: "Work panel" }));
  return screen.findByRole("dialog", { name: "Work" });
}

describe("Project page", () => {
  it("removal denial keeps the project, selected scope and history intact", async () => {
    const fetchMock = mockFetch();
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => String(input) === "/api/project/remove"
      ? Promise.resolve(jsonResponse({ error: "Finish or reject fix-timer before removing this project." }, 409)) : base(input, init));
    const { router, user } = renderApp({ route: "/projects/altitude" });
    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Remove project" }));
    expect(screen.getByText(/Removing this project detaches L3/)).toHaveTextContent("remaining worktrees, saved history and queued messages stay on disk");
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Finish or reject fix-timer");
    expect(router.state.location.pathname).toBe("/projects/altitude");
    expect(localStorage.getItem("altitude.project")).toBe("altitude");
    expect(screen.getByText("two tasks running.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("removes the last project from navigation, selection and cached history, leaving First run", async () => {
    const data = { ...overview, queue: [], projects: [{ name: "altitude", managed: true, path: "/tmp/altitude" }] };
    const fetchMock = mockFetch({ overview: data, project: { ...project, tasks: [] } });
    const base = fetchMock.getMockImplementation()!;
    let finish!: (value: Response) => void;
    fetchMock.mockImplementation((input, init) => String(input) === "/api/project/remove"
      ? new Promise<Response>((resolve) => { finish = resolve; }) : base(input, init));
    const { router, user, queryClient } = renderApp({ route: "/projects/altitude" });
    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Remove project" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("button", { name: "Removing…" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Reset L3 conversation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    data.projects[0]!.managed = false;
    finish(jsonResponse({ ok: true }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects"));
    await screen.findByText("Altitude found 1 folder under ~/Projects");
    await waitFor(() => expect(localStorage.getItem("altitude.project")).toBeNull());
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
    expect(queryClient.getQueryData(["chat", "altitude"])).toBeUndefined();
    expect(screen.queryByRole("link", { name: "altitude" })).toBeNull();
  });

  it("a removed project route shows a missing-project state while other projects remain", async () => {
    mockFetch({ overview: { ...overview, queue: [], projects: [{ name: "sibling", managed: true }] } });
    localStorage.setItem("altitude.project", "altitude");
    setViewport(390);
    renderApp({ route: "/projects/altitude" });
    await screen.findByRole("heading", { name: "Project not managed" });
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
    await waitFor(() => expect(localStorage.getItem("altitude.project")).toBe("sibling"));
    expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute("href", "/projects/sibling");
  });

  it("composes the header status line from the chat, the tasks, and the decisions", async () => {
    mockFetch();
    renderApp({ route: "/projects/altitude" });

    expect(await screen.findByRole("heading", { name: "altitude" })).toBeInTheDocument();
    await screen.findByText("L3 answered 4 min ago on Alpha · 2 tasks in flight · 1 waits for your review");
    expect(screen.getByRole("region", { name: "Conversation" })).toBeInTheDocument();
  });

  it("says what L3 is doing while a turn runs", async () => {
    mockFetch({ chat: { ...chatView, active: { id: "t1", started_at: ago(0), trigger: "report-landed" } } });
    renderApp({ route: "/projects/altitude" });
    await screen.findByText(/^L3 is handling a landed report ·/);
  });

  it("offers Start L3 when L3 never ran and posts the start", async () => {
    const fetchMock = mockFetch({
      project: { ...project, l3: {} },
      chat: { ...chatView, history: [], l3: {} },
    });
    const { user } = renderApp({ route: "/projects/altitude" });

    await screen.findByText("L3 has not started");
    await user.click(screen.getByRole("button", { name: "Start L3" }));
    await waitFor(() => expect(posted(fetchMock, "/api/l3/start")).toEqual({ project: "altitude" }));
  });

  it("shows the read error in the status line and keeps the conversation", async () => {
    mockFetch({ project: null });
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overview);
      if (url.includes("/api/project/altitude")) return jsonResponse({ error: "state file unreadable" }, 500);
      if (url.includes("/api/chat/")) return jsonResponse(chatView);
      return jsonResponse({ error: "not found" }, 404);
    });
    renderApp({ route: "/projects/altitude" });

    expect(await screen.findByText("state file unreadable")).toHaveClass("text-danger");
    expect(await screen.findByText("two tasks running.")).toBeInTheDocument();
  });

  // Below the inline width the panel is an overlay from the header button; Esc closes it.
  it("opens the work panel as an overlay at 1024 and closes it with Escape", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    const panel = await openPanel(user);
    expect(within(panel).getByText("1 active · 1 done this week")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: /^Fix the timer/ })).toBeInTheDocument();
    expect(within(panel).getByRole("heading", { name: "Needs you (1)" })).toBeInTheDocument();
    expect(within(panel).getByText("Done this week (1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Work panel" })).toHaveAttribute("aria-pressed", "true");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Work" })).toBeNull();
  });

  it("keeps the panel inline at 1440 with no toggle", async () => {
    mockFetch();
    setViewport(1440);
    renderApp({ route: "/projects/altitude" });

    const panel = await screen.findByRole("region", { name: "Work" });
    expect(within(panel).getByRole("link", { name: /^Fix the timer/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Work panel" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when nothing runs", async () => {
    mockFetch({ overview: { ...overview, queue: [] }, project: { ...project, tasks: [], archive: [] } });
    setViewport(1440);
    renderApp({ route: "/projects/altitude" });

    const panel = await screen.findByRole("region", { name: "Work" });
    await within(panel).findByText("Nothing running. Ask L3 for something.");
    expect(within(panel).getByText("0 active · 0 done this week")).toBeInTheDocument();
  });

  it("shows the Work tab on the phone under the project header", async () => {
    mockFetch();
    setViewport(390);
    renderApp({ route: "/projects/altitude?tab=work" });

    expect(await screen.findByRole("heading", { name: "altitude", level: 1 })).toHaveClass("phone-title");
    const panel = await screen.findByRole("region", { name: "Work" });
    expect(within(panel).getByRole("link", { name: /^Fix the timer/ })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Work panel" })).toBeNull();
    expect(screen.getByRole("link", { name: /^Work/ })).toHaveAttribute("aria-current", "page");
  });

  // §3.5: the row's meta comes from the task's state and the queue's own hold text, never a lease.
  it("reads each row's state: running with its engine, queued with the hold, waits for L3, done with its PR", async () => {
    const waitsL3 = { slug: "ask-l3", state: "blocked", title: "Ask L3", updated: ago(1), waiting_on: "l3", blocked_reason: "which suite?" };
    const queued = { slug: "later", state: "queued", title: "Later", updated: ago(5) };
    const runner = { ...project.tasks[0], l2_engine: "alpha", engine_model: "opus", dispatched: ago(2) };
    const done = { slug: "shipped", state: "done", title: "Shipped", updated: ago(30), prs: [212] };
    mockFetch({
      overview: {
        ...overview,
        queue: [],
        wip: { per_project: {}, machine: 1, waiting: [{ project: "altitude", slug: "later", why: "dispatch", hold: "WIP limit 1 reached for altitude (1 running)" }] },
      },
      project: { ...project, tasks: [runner, waitsL3, queued], archive: [done] },
    });
    setViewport(1440);
    renderApp({ route: "/projects/altitude" });

    const panel = await screen.findByRole("region", { name: "Work" });
    const active = within(panel).getByRole("region", { name: "Active" });
    const row = (name: RegExp) => within(active).getByRole("link", { name });
    expect(row(/^Fix the timer/)).toHaveAccessibleName("Fix the timer · Running · Opus on Alpha · started 2 min ago");
    expect(row(/^Ask L3/)).toHaveAccessibleName("Ask L3 · Waits for L3");
    expect(row(/^Ask L3/).querySelector(".dot")).toHaveAttribute("data-state", "running");
    expect(row(/^Later/)).toHaveAccessibleName("Later · Queued · waits for a slot · WIP limit 1 reached for altitude (1 running)");
    expect(within(panel).queryByRole("region", { name: "Needs you" })).toBeNull();

    const fold = within(panel).getByText("Done this week (1)");
    expect(fold.closest("details")).not.toHaveAttribute("open");
    fold.click();
    expect(fold.closest("details")).toHaveAttribute("open");
    expect(within(panel).getByRole("link", { name: /^Shipped/ })).toHaveAccessibleName("Shipped · Done · PR #212 merged");
  });

  // §3.7: a decision answered from the panel collapses its card; the task lands in Active on the next read.
  it("answers a decision from the panel and moves the task to Active", async () => {
    const fixtures: Fixtures = {};
    const fetchMock = mockFetch(fixtures);
    setViewport(1440);
    const { user } = renderApp({ route: "/projects/altitude" });

    const panel = await screen.findByRole("region", { name: "Work" });
    const card = within(panel).getByRole("article", { name: "Add the badge" });
    expect(within(card).getByText("L3 brought this to you")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open L2 chat" })).toHaveAttribute("href", "/projects/altitude/tasks/add-badge?question=q-badge&revision=1");
    expect(within(panel).getByRole("link", { name: /^Add the badge/ }).closest("article")).toBe(card);

    fixtures.overview = { ...overview, queue: [] };
    await user.click(within(card).getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(posted(fetchMock, "/api/decide")).toEqual({ project: "altitude", slug: "add-badge", question_id: "q-badge", revision: 1, option_key: "recommended" }));
    await waitFor(() => expect(within(panel).queryByRole("article", { name: "Add the badge" })).toBeNull());
    const row = await within(panel).findByRole("link", { name: /^Add the badge/ });
    expect(row).toHaveAccessibleName("Add the badge · Queued · waits for dispatch");
    expect(row.closest(".wp-row")).toHaveAttribute("data-moved");
  });

  // The item is present only when the project checkout has boards: it is the server's answer, not
  // a guess the page makes from the project name.
  it("offers Design boards in the overflow menu only when the project has a viewer", async () => {
    mockFetch({ project: { ...project, design_viewer: "/design/altitude/design/wireframes/index.html" } });
    const { user } = renderApp({ route: "/projects/altitude" });

    await screen.findByRole("heading", { name: "altitude" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    const link = await screen.findByRole("menuitem", { name: "Design boards" });
    expect(link).toHaveAttribute("href", "/design/altitude/design/wireframes/index.html");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("shows no Design boards item for a project without boards", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await screen.findByRole("heading", { name: "altitude" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await screen.findByRole("menuitem", { name: "Reset L3 conversation" });
    expect(screen.queryByRole("menuitem", { name: "Design boards" })).toBeNull();
  });

  it("resets the L3 conversation after an inline confirm", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });

    await screen.findByRole("heading", { name: "altitude" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Reset L3 conversation" }));
    expect(posted(fetchMock, "/api/l3/reset")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(posted(fetchMock, "/api/l3/reset")).toEqual({ project: "altitude" }));
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });

  it("removes the project after an inline confirm and leaves for Needs you", async () => {
    const data = { ...overview, projects: [{ name: "altitude", managed: true }, { name: "sibling", managed: true }] };
    const fetchMock = mockFetch({ overview: data });
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => {
      if (String(input) === "/api/project/remove") data.projects[0]!.managed = false;
      return base(input, init);
    });
    const { router, user } = renderApp({ route: "/projects/altitude" });

    await screen.findByRole("heading", { name: "altitude" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove project" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Remove altitude from Altitude?")).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "Remove project" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(posted(fetchMock, "/api/project/remove")).toEqual({ name: "altitude" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("shows First run on a project route when nothing is managed", async () => {
    mockFetch({
      overview: {
        ...overview,
        queue: [],
        projects: [
          { name: "alpha", managed: false, path: "/home/ada/Projects/alpha" },
          { name: "beta", managed: false, path: "/home/ada/Projects/beta" },
        ],
      },
    });
    renderApp({ route: "/projects/altitude" });

    await screen.findByText("Altitude found 2 folders under ~/Projects");
    expect(screen.getAllByRole("button", { name: "Start L3" })).toHaveLength(3);
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
  });
});
