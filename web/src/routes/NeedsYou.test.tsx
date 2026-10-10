import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { TaskMessageSchema } from "../data/api";
import { setSelectedProject } from "../shell/scope";

const OPERATOR = "operator";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const asks = {
  id: "q-badge", revision: 1, anchor_id: "q-message", status: "open", audience: "operator",
  project: "altitude",
  slug: "add-badge",
  title: "Add the badge",
  kind: "asks",
  asked_by: "l3",
  question: "Which badge colour should the count use?",
  detail: "Accent matches the boards; amber matches the old build.",
  asked: ago(4),
  since: ago(4),
  options: [
    { key: "A", label: "Accent", text: "Accent matches the boards." },
    { key: "B", label: "Amber", text: "Amber matches the old build." },
  ],
  recommended_key: "A",
  recommendation: { text: "Use accent.", label: "Accent", why: "Accent matches the boards; amber matches the old build." },
};

const stopped = {
  project: "harbor",
  slug: "fix-audio",
  title: "Fix the audio",
  kind: "stopped",
  asked_by: "l2",
  question: "the recording upload fails at 10 minutes",
  detail: "the recording upload fails at 10 minutes",
  asked: ago(60 * 30),
  since: ago(60 * 30),
  recommendation: null,
};

const l2Asks = {
  ...asks,
  project: "harbor",
  slug: "score-phonemes",
  title: "Score pronunciation per phoneme",
  asked_by: "l2",
  question: "Which scorer should the phoneme pass use?",
  detail: "",
  recommendation: { option: "A", why: "" },
};

function overview(queue: unknown[]) {
  return {
    projects: [
      { name: "altitude", managed: true },
      { name: "harbor", managed: true },
      { name: "notes", managed: true },
    ],
    queue,
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: [],
    roots: ["~/Projects"],
  };
}

const chatView = {
  history: [
    { at: ago(3), role: "user", text: "Why not amber?", trigger: "chat", turn_id: "c7", slug: "add-badge" },
    { at: ago(2), role: "assistant", text: "Amber is the old build's colour; the boards moved on.", trigger: "chat", turn_id: "c7", slug: "add-badge" },
    { at: ago(1), role: "user", text: "unrelated", trigger: "chat", turn_id: "c8" },
  ],
  queued: [{ id: "q1", at: ago(1), text: "And on the phone?", trigger: "chat", slug: "add-badge" }],
  active: null,
  busy: false,
};

function mockFetch(initialQueue: unknown[], decideStatus = 200) {
  let queue = initialQueue;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
    if (url.includes("/api/project/")) {
      const name = url.split("/").at(-1);
      return jsonResponse({ name, tasks: [], repository: `https://github.com/example/${name}` });
    }
    if (url.includes("/api/chat/altitude")) return jsonResponse(chatView);
    if (url.includes("/api/chat/")) return jsonResponse({ history: [], queued: [], active: null, busy: false });
    if (url.includes("/api/task/harbor/score-phonemes")) {
      return jsonResponse({
        slug: "score-phonemes",
        state: "blocked",
        messages: [
          { id: "m1", at: ago(2), role: OPERATOR, text: "Is the old scorer still wired?" },
          { id: "m2", at: ago(1), role: "l2", text: "Yes, behind the flag." },
        ],
      });
    }
    if (url.includes("/api/task/")) return jsonResponse({ slug: "x", state: "blocked", messages: [] });
    if (url.includes("/api/decide")) {
      if (decideStatus !== 200) return jsonResponse({ error: "Could not save" }, decideStatus);
      queue = [];
      return jsonResponse({ ok: true, question: { ...asks, response: { text: "Use accent.", message_id: "answer", at: ago(0) } } });
    }
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Needs you", () => {
  it("puts the selected project first, follows a selection change, and keeps question groups whole", async () => {
    const first = { ...l2Asks, title: "Shared task title", group_id: "scope", group_revision: 1 };
    const other = { ...asks, title: "Shared task title", group_id: "scope", group_revision: 1 };
    mockFetch([first, other, { ...first, id: "follow-up", question: "Which regions?" }, stopped,
      { ...other, id: "review", group_id: "review", kind: "review" }]);
    localStorage.setItem("altitude.project", "altitude");
    renderApp({ route: "/" });
    const list = await screen.findByLabelText("Decisions");
    const labels = () => within(list).getAllByRole("region").map((section) => section.getAttribute("aria-label"));
    expect(labels()).toEqual(["Project altitude", "Project harbor"]);
    const [altitude, harbor] = within(list).getAllByRole("region");
    expect(within(altitude!).getAllByRole("article")).toHaveLength(2);
    expect(within(harbor!).getAllByRole("article")).toHaveLength(2);
    const group = within(harbor!).getByRole("article", { name: "Shared task title" });
    expect(within(group).getByText("2 questions to answer")).toBeInTheDocument();
    expect(within(group).getByText("Which regions?")).toBeInTheDocument();
    expect(within(group).getByRole("link", { name: "Open L2 chat" })).toHaveAttribute("href", "/projects/harbor/tasks/score-phonemes?question=q-badge&revision=1");
    expect(within(harbor!).getByRole("article", { name: "Fix the audio" })).toBeInTheDocument();
    expect(screen.getByText("4 questions · 1 stopped task across 2 projects")).toBeInTheDocument();
    act(() => setSelectedProject("harbor"));
    expect(labels()).toEqual(["Project harbor", "Project altitude"]);
    expect(within(list).getAllByRole("article")).toHaveLength(4);
  });

  it("keeps first-appearance order when the selected project has nothing waiting", async () => {
    mockFetch([stopped, asks]);
    localStorage.setItem("altitude.project", "notes");
    renderApp({ route: "/" });
    const list = await screen.findByLabelText("Decisions");
    const labels = () => within(list).getAllByRole("region").map((section) => section.getAttribute("aria-label"));
    expect(labels()).toEqual(["Project harbor", "Project altitude"]);
    act(() => setSelectedProject("altitude"));
    expect(labels()).toEqual(["Project altitude", "Project harbor"]);
  });

  it("collapses and reopens a project from its heading without losing staged answers", async () => {
    mockFetch([asks, l2Asks, stopped]);
    const { user, queryClient } = renderApp({ route: "/" });
    const list = await screen.findByLabelText("Decisions");
    const altitude = within(list).getByRole("region", { name: "Project altitude" });
    const harbor = within(list).getByRole("region", { name: "Project harbor" });
    await user.click(within(altitude).getByRole("button", { name: "Accent" }));
    expect(within(altitude).getByRole("button", { name: "Send 1 answer" })).toBeEnabled();

    const toggle = within(altitude).getByRole("button", { name: "altitude" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(altitude).getByRole("heading", { level: 2 })).toHaveTextContent("altitude1 question");
    expect(within(altitude).queryByRole("article")).toBeNull();
    expect(within(harbor).getAllByRole("article")).toHaveLength(2);

    // A background refresh keeps the collapsed state; keyboard reopens it with the staged pick intact.
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent("altitude");
    expect(within(altitude).getByRole("button", { name: "Accent" })).toHaveAttribute("aria-pressed", "true");
    expect(within(altitude).getByRole("button", { name: "Send 1 answer" })).toBeEnabled();

    const harborToggle = within(harbor).getByRole("button", { name: "harbor" });
    harborToggle.focus();
    await user.keyboard(" ");
    expect(harborToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(harbor).getByRole("heading", { level: 2 })).toHaveTextContent("1 question · 1 stopped task");
    expect(within(harbor).queryByRole("article")).toBeNull();
    expect(screen.getByText("2 questions · 1 stopped task across 2 projects")).toBeInTheDocument();
  });

  it("removes an old group's saved receipt while retaining the new group even if the follow-up read fails", async () => {
    const old = { ...asks, group_id: "old-group", group_revision: 1 };
    const next = { ...asks, id: "q-label", group_id: "new-group", group_revision: 1, question: "What should the badge say?" };
    let recorded = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const route = String(input);
      if (route === "/api/decide") {
        recorded = true;
        return jsonResponse({ question: { ...old, status: "resolved", resolution: { disposition: "answered", text: "Use accent.", by: OPERATOR, at: ago(0) } }, question_group: { id: "new-group", revision: 1, anchor_id: "new-anchor", questions: [next] } });
      }
      if (route === "/api/overview") return recorded ? jsonResponse({ error: "read unavailable" }, 503) : jsonResponse(overview([old]));
      if (route === "/api/project/altitude") return jsonResponse({ name: "altitude", tasks: [] });
      return jsonResponse({}, 404);
    }));
    const { user, queryClient } = renderApp({ route: "/" });
    await user.click(await screen.findByRole("button", { name: "Accent" }));
    await user.click(screen.getByRole("button", { name: "Send 1 answer" }));
    await screen.findByText("What should the badge say?");
    await waitFor(() => expect(queryClient.getQueryState(["overview"])?.status).toBe("error"));
    expect(screen.queryByText(asks.question)).toBeNull();
    expect(queryClient.getQueryData<{ queue: { id: string }[] }>(["overview"])?.queue.map((q) => q.id)).toEqual(["q-label"]);
    expect(screen.getByRole("button", { name: "Accent" })).toBeDisabled();
  });

  it("resolves the same reference against each decision card's own project", async () => {
    mockFetch([{ ...asks, question: "Review PR #250?" }, { ...l2Asks, question: "Review PR #250?" }]);
    renderApp({ route: "/" });
    await waitFor(() => expect(screen.getAllByRole("link", { name: "PR #250" })
      .map((link) => link.getAttribute("href")).sort()).toEqual([
        "https://github.com/example/altitude/pull/250", "https://github.com/example/harbor/pull/250",
      ]));
  });

  it("says so when nothing waits", async () => {
    mockFetch([]);
    renderApp({ route: "/" });
    expect(await screen.findByText("Nothing needs you.")).toHaveClass("text-muted");
    expect(screen.queryByText(/That is everything/)).toBeNull();
  });

  it("shows one sentence and a Retry that repeats the read when the overview fails", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ error: "altd is restarting" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const { user } = renderApp({ route: "/" });

    await screen.findByText(/Could not read what needs you\./);
    const reads = fetchMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(reads));
  });

  // §3.8: named project sections precede their questions and answer controls.
  it("lists cards in named project sections with the kind row, why and unselected options", async () => {
    mockFetch([asks, stopped]);
    renderApp({ route: "/" });

    expect(await screen.findByText("1 question · 1 stopped task across 2 projects")).toBeInTheDocument();
    const list = screen.getByLabelText("Decisions");
    const card = within(list).getByRole("article", { name: "Add the badge" });
    expect(within(card).getByText("L3 brought this to you")).toHaveClass("kind-label");
    const project = within(list).getByRole("region", { name: "Project altitude" });
    expect(within(project).getByRole("heading", { name: "altitude", level: 2 })).toBeInTheDocument();
    expect(project).toContainElement(card);
    expect(within(card).getByText("Which badge colour should the count use?")).toHaveClass("decision-question");
    expect(within(card).getByText("Accent matches the boards; amber matches the old build.")).toHaveClass("decision-why");
    expect(within(card).getByText("4 min ago")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Accent" })).toHaveAttribute("aria-pressed", "false");
    expect(within(card).getByRole("button", { name: "Send answers" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Amber" })).toHaveClass("btn-ghost");
    expect(within(card).getByRole("link", { name: "Open L2 chat" })).toHaveAttribute("href", "/projects/altitude/tasks/add-badge?question=q-badge&revision=1");

    const stoppedCard = within(list).getByRole("article", { name: "Fix the audio" });
    expect(within(list).getByRole("region", { name: "Project harbor" })).toContainElement(stoppedCard);
    expect(within(stoppedCard).getByText("Stopped mid-task").closest(".decision-kind")).toHaveAttribute("data-tone", "danger");
    expect(within(stoppedCard).getByText("yesterday")).toBeInTheDocument();
    expect(within(stoppedCard).getAllByText("the recording upload fails at 10 minutes")).toHaveLength(1);
    expect(within(stoppedCard).queryByRole("button")).toBeNull();
    expect(screen.queryByText(/That is everything\. Running work stays in each project\./)).toBeNull();
  });

  it("keeps discussion in the owning chat and does not fetch per-card conversation rows", async () => {
    const fetchMock = mockFetch([asks, l2Asks]);
    renderApp({ route: "/" });
    await screen.findByRole("article", { name: "Add the badge" });
    expect(screen.queryByLabelText("Follow-ups")).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => /\/api\/(chat|task)\//.test(String(url)))).toBe(false);
    expect(screen.getAllByRole("link", { name: "Open L2 chat" })).toHaveLength(2);
  });

  it("sends the exact recommendation revision and removes the submitted item", async () => {
    const fetchMock = mockFetch([asks]);
    const { user, router } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Accent" }));
    await user.click(within(card).getByRole("button", { name: "Send 1 answer" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
      expect(JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))).toEqual({
        project: "altitude",
        slug: "add-badge",
        question_id: "q-badge",
        revision: 1,
        option_key: "A",
      });
    });
    await waitFor(() => expect(screen.queryByRole("article", { name: "Add the badge" })).toBeNull());
    await user.click(screen.getByRole("button", { name: "Open L2 chat" }));
    expect(router.state.location.pathname + router.state.location.search).toBe("/projects/altitude/tasks/add-badge?question=q-badge&revision=1");
    expect(router.state.location.state).toEqual({ from: "needs", tab: "needs" });
  });

  it("keeps quick acceptance available during a background overview refresh", async () => {
    const fetchMock = mockFetch([asks]);
    const { queryClient } = renderApp({ route: "/" });
    const accept = await screen.findByRole("button", { name: "Accent" });
    await waitFor(() => expect(accept).toBeEnabled());
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    let refresh!: Promise<void>;
    act(() => { refresh = queryClient.invalidateQueries({ queryKey: ["overview"] }); });
    await waitFor(() => expect(queryClient.isFetching({ queryKey: ["overview"] })).toBe(1));
    expect(accept).toBeEnabled();
    await act(async () => { finish(jsonResponse(overview([asks]))); await refresh; });
  });

  it("keeps the card with one line and a Retry when the decision fails", async () => {
    const fetchMock = mockFetch([asks], 503);
    const { user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Accent" }));
    await user.click(within(card).getByRole("button", { name: "Send 1 answer" }));

    await within(card).findByText(/Could not send answers\./);
    expect(within(card).getByRole("button", { name: "Accent" })).toBeEnabled();
    const decides = () => fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/decide")).length;
    expect(decides()).toBe(1);
    await user.click(within(card).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(decides()).toBe(2));
  });

  it("selects the card's project when Open L2 chat opens the owner", async () => {
    mockFetch([stopped]);
    const { router, user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Fix the audio" });
    await user.click(within(card).getByRole("link", { name: "Open L2 chat" }));
    expect(router.state.location.pathname).toBe("/projects/harbor/tasks/fix-audio");
    expect(router.state.location.state).toEqual({ from: "needs", tab: "needs" });
    expect(localStorage.getItem("altitude.project")).toBe("harbor");
  });
});
