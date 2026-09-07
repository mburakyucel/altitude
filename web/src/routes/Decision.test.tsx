import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { TaskMessageSchema } from "../data/api";

const OPERATOR = TaskMessageSchema.shape.role.options.find((role) => role !== "l2" && role !== "l3") ?? "";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function streamResponse(lines: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(`${line}\n`));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const decision = {
  project: "altitude",
  slug: "fix-timer",
  title: "Fix the timer",
  kind: "asks",
  asked_by: "l2",
  question: "Keep the portrait rule everywhere or allow landscape on tablets?",
  detail: "",
  asked: ago(9),
  since: ago(10),
  options: [
    { key: "A", label: "Keep the portrait rule", text: "Keep the portrait rule everywhere." },
    { key: "B", label: "Allow landscape", text: "Allow landscape on tablets only." },
  ],
  recommendation: { option: "A", why: "Portrait first keeps the boards honest, and nothing on tablets needs landscape yet." },
};

const task = {
  slug: "fix-timer",
  state: "blocked",
  title: "Fix the timer",
  session_id: "sess-1",
  engine: "alpha",
  model: "opus",
  prs: [140],
  files: { conversation: "/x/conversation.jsonl" },
  messages: [],
  events: [
    { at: ago(60), kind: "new", by: "l3" },
    { at: ago(50), kind: "state", from: "queued", to: "running", by: "altd" },
    { at: ago(10), kind: "state", from: "running", to: "blocked", by: "l2", reason: "Which orientation rule should stand? A: Keep the portrait rule. B: Allow landscape." },
    { at: ago(9), kind: "escalated", by: "l3", question: "Keep the portrait rule everywhere or allow landscape on tablets? A is what the boards say." },
  ],
};

const project = {
  name: "altitude",
  tasks: [{ ...task, updated: ago(9) }],
  archive: [],
  repository: "https://github.com/ada/altitude",
};

const overview = {
  projects: [{ name: "altitude", managed: true, counts: { running: 0 } }],
  queue: [decision],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: 10, known: true, stale: false, at: ago(1) }],
  roots: ["~/Projects"],
};

type Fixtures = { overview?: unknown; task?: unknown; chat?: unknown; decideStatus?: number };

function mockFetch(fixtures: Fixtures = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(fixtures.overview ?? overview);
    if (url.includes("/api/project/altitude")) return jsonResponse(project);
    if (url.includes("/api/task/altitude/fix-timer")) return jsonResponse(fixtures.task ?? task);
    if (url.includes("/api/chat/")) return jsonResponse(fixtures.chat ?? { history: [], queued: [], active: null, busy: false });
    if (url.endsWith("/api/chat") && init?.method === "POST") {
      return streamResponse(['{"turn":{"id":"c9","started_at":"2026-09-07T09:14:00+00:00","trigger":"chat","slug":"fix-timer"}}', '{"t":"Because the boards say so."}', '{"done":{"turn_id":"c9"}}']);
    }
    if (url.includes("/api/l2/message")) return jsonResponse({ ok: true, message: { id: "m9", at: ago(0), role: OPERATOR, text: "x" } });
    if (url.includes("/api/decide")) {
      return (fixtures.decideStatus ?? 200) === 200
        ? jsonResponse({ ok: true })
        : jsonResponse({ error: "only blocked tasks need a user decision" }, fixtures.decideStatus);
    }
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posted(fetchMock: ReturnType<typeof vi.fn>, path: string) {
  const call = fetchMock.mock.calls.find(([u, init]) => String(u).includes(path) && (init as RequestInit | undefined)?.method === "POST");
  return call ? JSON.parse(String((call[1] as RequestInit | undefined)?.body)) : null;
}

const route = "/projects/altitude/decisions/fix-timer";

/** The page's own column; the work panel beside it repeats the card's buttons and labels. */
function page() {
  const col = document.querySelector(".decision-col");
  if (!col) throw new Error("the decision page has no column");
  return within(col as HTMLElement);
}

describe("Decision page", () => {
  it("shows the chips, question, options, why, timeline, and evidence with the panel card selected", async () => {
    mockFetch();
    setViewport(1440);
    renderApp({ route });

    expect(await screen.findByRole("heading", { level: 1, name: decision.question })).toHaveClass("decision-title");
    expect(screen.getByRole("link", { name: "‹ altitude" })).toHaveAttribute("href", "/projects/altitude?tab=work");
    expect(screen.getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer");
    const chips = page().getByText("The L2 asks").closest(".decision-chips");
    expect(chips).toHaveTextContent("altitude");
    expect(chips).toHaveTextContent("Fix the timer");
    expect(chips).toHaveTextContent("9 min ago");

    expect(page().getByRole("button", { name: "Keep the portrait rule" })).toHaveClass("btn-primary");
    expect(page().getByRole("button", { name: "Allow landscape" })).not.toHaveClass("btn-primary");
    expect(page().getByPlaceholderText("Add a note for the L2 (optional)")).toBeInTheDocument();
    const why = page().getByRole("region", { name: "Why L3 recommends" });
    expect(within(why).getByRole("heading", { name: "Why L3 recommends Keep the portrait rule" })).toBeInTheDocument();
    expect(why).toHaveTextContent("Portrait first keeps the boards honest");
    expect(why).toHaveTextContent("Allow landscape: Allow landscape on tablets only.");

    const timeline = page().getByRole("list", { name: "Where this came from" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("The L2 (Opus on Alpha) asked L3");
    expect(items[0]).toHaveTextContent("Which orientation rule should stand?");
    expect(items[1]).toHaveTextContent("L3 escalated to you: Keep the portrait rule everywhere or allow landscape on tablets?");
    expect(items[1]).not.toHaveTextContent("A is what the boards say.");
    expect(items[2]).toHaveTextContent("now");
    expect(items[2]).toHaveTextContent("The task is blocked until you choose.");
    expect(items).toHaveLength(3);

    const evidence = page().getByRole("region", { name: "Evidence" });
    expect(within(evidence).getByRole("link", { name: "Task conversation" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer");
    expect(within(evidence).getByRole("link", { name: "Live session at the failing step" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer/live");
    expect(within(evidence).getByRole("link", { name: "PR #140" })).toHaveAttribute("href", "https://github.com/ada/altitude/pull/140");

    expect(screen.getByText("Your question and the answer appear here and on the card. The L2 stays blocked until you choose.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Recipient" })).toHaveValue("l2");

    const panel = screen.getByRole("region", { name: "Work" });
    expect(within(panel).getByRole("article", { name: "Fix the timer" })).toHaveAttribute("data-selected");
  });

  it("records the option with the note and then reads as decided", async () => {
    const fixtures: Fixtures = {};
    const fetchMock = mockFetch(fixtures);
    setViewport(1440);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: decision.question });
    await user.type(page().getByLabelText("Note for the L2"), "Tablets only.");
    fixtures.overview = { ...overview, queue: [] };
    fixtures.task = {
      ...task,
      state: "running",
      decision: { key: "B", option: "Allow landscape", note: "Tablets only.", at: ago(0), by: "ada" },
      events: [...task.events, { at: ago(0), kind: "decided", key: "B", option: "Allow landscape", note: "Tablets only.", by: "ada" }],
    };
    await user.click(page().getByRole("button", { name: "Allow landscape" }));

    await waitFor(() =>
      expect(posted(fetchMock, "/api/decide")).toEqual({ project: "altitude", slug: "fix-timer", option: "B", note: "Tablets only." }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Decided just now: Allow landscape · Tablets only.");
    expect(page().queryByRole("button", { name: "Allow landscape" })).toBeNull();
    expect(screen.queryByLabelText("Ask a follow-up")).toBeNull();
    expect(page().getByRole("heading", { level: 1, name: decision.question })).toBeInTheDocument();
    const timeline = page().getByRole("list", { name: "Where this came from" });
    expect(timeline).toHaveTextContent("You chose Allow landscape");
    expect(timeline).not.toHaveTextContent("The task is blocked until you choose.");
  });

  it("keeps the options with one line and a Retry when the decision fails", async () => {
    mockFetch({ decideStatus: 409 });
    setViewport(1440);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: decision.question });
    await user.click(page().getByRole("button", { name: "Keep the portrait rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not record the decision.");
    await user.click(page().getByRole("button", { name: "Retry" }));
    expect(page().queryByRole("alert")).toBeNull();
    expect(page().getByRole("button", { name: "Keep the portrait rule" })).toBeEnabled();
  });

  // §5.2 note 6 / §4.3: a follow-up to L3 carries the slug; the answer lands in the timeline.
  it("sends a follow-up to L3 with the slug and lands the answer in the timeline", async () => {
    const fixtures: Fixtures = {};
    const fetchMock = mockFetch(fixtures);
    setViewport(1440);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: decision.question });
    await user.selectOptions(screen.getByRole("combobox", { name: "Recipient" }), "l3");
    fixtures.chat = {
      history: [
        { at: ago(0), role: "user", text: "Why not both?", trigger: "chat", turn_id: "c9", slug: "fix-timer" },
        { at: ago(0), role: "assistant", text: "Because the boards say so.", trigger: "chat", turn_id: "c9", slug: "fix-timer" },
      ],
      queued: [],
      active: null,
      busy: false,
    };
    await user.type(screen.getByLabelText("Ask a follow-up"), "Why not both?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(posted(fetchMock, "/api/chat")).toEqual({ project: "altitude", text: "Why not both?", slug: "fix-timer" }));
    const timeline = page().getByRole("list", { name: "Where this came from" });
    await waitFor(() => expect(timeline).toHaveTextContent("You asked L3"));
    expect(timeline).toHaveTextContent("Why not both?");
    expect(timeline).toHaveTextContent("L3 answered");
    expect(timeline).toHaveTextContent("Because the boards say so.");
    expect(screen.queryByText("L3 is answering…")).toBeNull();
    expect(page().getByRole("button", { name: "Keep the portrait rule" })).toBeEnabled();
  });

  it("sends a follow-up to the L2 through the task conversation when the L2 asked", async () => {
    const fetchMock = mockFetch();
    setViewport(1440);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: decision.question });
    await user.type(screen.getByLabelText("Ask a follow-up"), "Is the old scorer still wired?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(posted(fetchMock, "/api/l2/message")).toEqual({ project: "altitude", slug: "fix-timer", text: "Is the old scorer still wired?" }),
    );
  });

  it("says the task was archived and links to it", async () => {
    mockFetch({ overview: { ...overview, queue: [] }, task: { ...task, state: "done", events: [...task.events, { at: ago(1), kind: "state", from: "blocked", to: "done", by: "altd" }] } });
    setViewport(1440);
    renderApp({ route });

    expect(await screen.findByRole("status")).toHaveTextContent("This task was done.");
    expect(page().getByRole("link", { name: "Open the archived task" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer");
    expect(page().queryByRole("button", { name: "Keep the portrait rule" })).toBeNull();
    expect(screen.queryByLabelText("Ask a follow-up")).toBeNull();
  });

  it("shows one line and a Retry when the task cannot be read", async () => {
    mockFetch({ overview: { ...overview, queue: [] } });
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse({ ...overview, queue: [] });
      if (url.includes("/api/task/")) return jsonResponse({ error: "state file unreadable" }, 500);
      if (url.includes("/api/project/")) return jsonResponse(project);
      if (url.includes("/api/chat/")) return jsonResponse({ history: [], queued: [], active: null, busy: false });
      return jsonResponse({ error: "not found" }, 404);
    });
    setViewport(1440);
    renderApp({ route });

    await screen.findByText(/Could not read the decision\./);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("pushes over Needs you on the phone with Back, the crumb, and Open task", async () => {
    mockFetch();
    setViewport(390);
    const { router, user } = renderApp({ route: "/" });
    await screen.findByRole("article", { name: "Fix the timer" });
    await router.navigate(route, { state: { from: "needs", tab: "needs" } });

    await screen.findByRole("heading", { level: 1, name: decision.question });
    expect(screen.getByRole("heading", { level: 1, name: "Needs you" })).toHaveClass("phone-title");
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer");
    const bar = screen.getByRole("navigation", { name: "Primary" });
    expect(within(bar).getByRole("link", { name: /Needs you$/ })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("region", { name: "Work" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });
});
