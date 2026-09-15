import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { TaskMessageSchema } from "../data/api";

const OPERATOR = TaskMessageSchema.shape.role.options.find((role) => role !== "l2" && role !== "l3") ?? "";

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
  project: "tutor",
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
  project: "tutor",
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
      { name: "tutor", managed: true },
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
    if (url.includes("/api/task/tutor/score-phonemes")) {
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
        "https://github.com/example/altitude/pull/250", "https://github.com/example/tutor/pull/250",
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

  // §3.8: one card per decision with the project chip, the kind row, the why, and the asker's options.
  it("lists the cards with a project chip, the kind row, the why, and unselected options", async () => {
    mockFetch([asks, stopped]);
    renderApp({ route: "/" });

    expect(await screen.findByText("1 question · 1 stopped task across 2 projects")).toBeInTheDocument();
    const list = screen.getByLabelText("Decisions");
    const card = within(list).getByRole("article", { name: "Add the badge" });
    expect(within(card).getByText("L3 brought this to you")).toHaveClass("kind-label");
    expect(within(card).getByText("altitude")).toHaveClass("chip");
    expect(within(card).getByText("Which badge colour should the count use?")).toHaveClass("decision-question");
    expect(within(card).getByText("Accent matches the boards; amber matches the old build.")).toHaveClass("decision-why");
    expect(within(card).getByText("4 min ago")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Accent" })).toHaveAttribute("aria-pressed", "false");
    expect(within(card).getByRole("button", { name: "Send answers" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Amber" })).toHaveClass("btn-ghost");
    expect(within(card).getByRole("link", { name: "Open L2 chat" })).toHaveAttribute("href", "/projects/altitude/tasks/add-badge?question=q-badge&revision=1");
    expect(screen.queryByRole("region", { name: "altitude" })).toBeNull();

    const stoppedCard = within(list).getByRole("article", { name: "Fix the audio" });
    expect(within(stoppedCard).getByText("Stopped mid-task").closest(".decision-kind")).toHaveAttribute("data-tone", "danger");
    expect(within(stoppedCard).getByText("yesterday")).toBeInTheDocument();
    expect(within(stoppedCard).getAllByText("the recording upload fails at 10 minutes")).toHaveLength(1);
    expect(within(stoppedCard).queryByRole("button")).toBeNull();
    expect(screen.getByText(/That is everything\. Running work stays in each project\./)).toHaveClass("calm");
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
    expect(router.state.location.pathname).toBe("/projects/tutor/tasks/fix-audio");
    expect(router.state.location.state).toEqual({ from: "needs", tab: "needs" });
    expect(localStorage.getItem("altitude.project")).toBe("tutor");
  });
});
