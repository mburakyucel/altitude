import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";
import { TaskMessageSchema } from "../data/api";

const OPERATOR = TaskMessageSchema.shape.role.options.find((role) => role !== "l2" && role !== "l3") ?? "";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const asks = {
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
  recommendation: { option: "A", why: "Accent matches the boards; amber matches the old build." },
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
  options: [
    { key: "resume", label: "Resume" },
    { key: "reject", label: "Reject" },
  ],
  recommendation: { option: "resume", why: "" },
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

function mockFetch(queue: unknown[], decideStatus = 200) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview(queue));
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
      return decideStatus === 200
        ? jsonResponse({ ok: true })
        : jsonResponse({ error: "only blocked tasks need a user decision" }, decideStatus);
    }
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Needs you", () => {
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
  it("lists the cards with a project chip, the kind row, the why, and the recommended option primary", async () => {
    mockFetch([asks, stopped]);
    renderApp({ route: "/" });

    expect(await screen.findByText("2 things wait on you across 2 projects. Everything else runs on its own.")).toBeInTheDocument();
    const list = screen.getByLabelText("Decisions");
    const card = within(list).getByRole("article", { name: "Add the badge" });
    expect(within(card).getByText("L3 asks")).toHaveClass("kind-label");
    expect(within(card).getByText("altitude")).toHaveClass("chip");
    expect(within(card).getByText("Which badge colour should the count use?")).toHaveClass("decision-question");
    expect(within(card).getByText("Accent matches the boards; amber matches the old build.")).toHaveClass("decision-why");
    expect(within(card).getByText("4 min ago")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Accent" })).toHaveClass("btn-primary");
    expect(within(card).getByRole("button", { name: "Amber" })).not.toHaveClass("btn-primary");
    expect(within(card).getByRole("link", { name: "More context" })).toHaveAttribute("href", "/projects/altitude/decisions/add-badge");
    expect(screen.queryByRole("region", { name: "altitude" })).toBeNull();

    const stoppedCard = within(list).getByRole("article", { name: "Fix the audio" });
    expect(within(stoppedCard).getByText("Stopped mid-task").closest(".decision-kind")).toHaveAttribute("data-tone", "danger");
    expect(within(stoppedCard).getByText("yesterday")).toBeInTheDocument();
    expect(within(stoppedCard).getAllByText("the recording upload fails at 10 minutes")).toHaveLength(1);
    expect(within(stoppedCard).getByRole("button", { name: "Resume" })).toHaveClass("btn-primary");
    expect(screen.getByText(/That is everything\. Running work stays in each project\./)).toHaveClass("calm");
  });

  // §3.8 Follow-up sent / Answer arrived: the chat rows and task messages that carry the decision mirror on the card.
  it("mirrors follow-ups and answers from the project's chat and the task's messages", async () => {
    mockFetch([asks, l2Asks]);
    renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    const thread = await within(card).findByLabelText("Follow-ups");
    expect(thread).toHaveTextContent("You asked: Why not amber?");
    expect(thread).toHaveTextContent("L3: Amber is the old build's colour; the boards moved on.");
    expect(thread).toHaveTextContent("You asked: And on the phone? · queued for L3");
    expect(thread).not.toHaveTextContent("unrelated");

    const l2Card = screen.getByRole("article", { name: "Score pronunciation per phoneme" });
    expect(within(l2Card).getByText("The L2 asks")).toBeInTheDocument();
    const l2Thread = await within(l2Card).findByLabelText("Follow-ups");
    expect(l2Thread).toHaveTextContent("You asked: Is the old scorer still wired?");
    expect(l2Thread).toHaveTextContent("The L2: Yes, behind the flag.");
  });

  it("records a decision with the option's key and collapses the card", async () => {
    const fetchMock = mockFetch([asks]);
    const { user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Amber" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/decide"));
      expect(JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))).toEqual({
        project: "altitude",
        slug: "add-badge",
        option: "B",
      });
    });
    await waitFor(() => expect(screen.queryByRole("article", { name: "Add the badge" })).toBeNull());
  });

  it("keeps the card with one line and a Retry when the decision fails", async () => {
    const fetchMock = mockFetch([asks], 409);
    const { user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Add the badge" });
    await user.click(within(card).getByRole("button", { name: "Amber" }));

    await within(card).findByText(/Could not record the decision\./);
    expect(within(card).getByRole("button", { name: "Accent" })).toBeEnabled();
    const decides = () => fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/decide")).length;
    expect(decides()).toBe(1);
    await user.click(within(card).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(decides()).toBe(2));
  });

  it("selects the card's project when More context opens the decision page", async () => {
    mockFetch([stopped]);
    const { router, user } = renderApp({ route: "/" });

    const card = await screen.findByRole("article", { name: "Fix the audio" });
    await user.click(within(card).getByRole("link", { name: "More context" }));
    expect(router.state.location.pathname).toBe("/projects/tutor/decisions/fix-audio");
    expect(router.state.location.state).toEqual({ from: "needs", tab: "needs" });
    expect(localStorage.getItem("altitude.project")).toBe("tutor");
  });
});
