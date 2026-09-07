import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

/*
 * The project conversation (SPEC.md §3.3, §3.4, §4.1, §4.2): rows, system lines and groups, the states
 * of the conversation and its composer, and the one-conversation rule the poll enforces.
 */

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
const yesterday = (minutes: number) => new Date(Date.now() - 86_400_000 - minutes * 60_000).toISOString();

const overview = {
  projects: [{ name: "altitude", managed: true, counts: { running: 1 } }],
  queue: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  engines: [{ engine: "alpha", label: "Alpha", week: 52, known: true, stale: false, at: ago(1) }],
  roots: ["~/Projects"],
  operator: "Ada",
};

const project = {
  name: "altitude",
  config: { approval: "ask", wip: 2 },
  l3: { session_id: "abcdef1234567890", turns: 12 },
  busy: false,
  tasks: [{ slug: "fix-timer", state: "running", title: "Fix the timer", updated: ago(2) }],
  archive: [{ slug: "persist-paths", state: "done", title: "Persist paths", updated: yesterday(0) }],
  decisions: [],
  incidents: [],
  hold: null,
};

const reportPrompt = [
  "Report landed for persist-paths.",
  "Task: persist-paths",
  "Verdict: done",
  "Problems: none",
  "Post-mortem signals: none",
  "PRs: #12 merged",
  "Spend: 3 turns",
  "",
  "Read the full report with `alt task report persist-paths`. Handle the report.",
].join("\n");

const history = [
  { at: yesterday(30), role: "user", text: "What is left this week?", trigger: "chat", turn_id: "c1" },
  { at: yesterday(29), role: "assistant", text: "Two tasks. **Persist paths** lands today.\n\n- one\n- two", trigger: "chat", engine: "alpha", turn_id: "c1" },
  { at: ago(50), role: "user", text: reportPrompt, trigger: "report-landed", turn_id: "s1" },
  { at: ago(49), role: "assistant", text: "Checked the PR and the digest.\n\nClosed Persist paths as done.", trigger: "report-landed", engine: "alpha", turn_id: "s1" },
  { at: ago(40), role: "user", text: "Altitude restarted with the code now on main. Its active tasks: fix-timer", trigger: "restart", turn_id: "s2" },
  { at: ago(39), role: "assistant", text: "Resumed nothing; the running task continues.", trigger: "restart", engine: "alpha", turn_id: "s2" },
  { at: ago(10), role: "user", text: "Fix the timer, please", trigger: "chat", turn_id: "c2" },
  { at: ago(9), role: "assistant", text: "Created one task for it.", trigger: "chat", engine: "alpha", turn_id: "c2", tasks: ["fix-timer"] },
];

const chatView = { history, active: null, busy: false, queued: [], l3: { session_id: "abcdef1234567890" }, engine: null };

type Fixtures = { chat?: unknown; chatFn?: () => Response | Promise<Response>; post?: (body: unknown) => Response | Promise<Response> };

function mockFetch(fixtures: Fixtures = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/project/altitude")) return jsonResponse(project);
    if (url.includes("/api/chat/remove")) return jsonResponse({ ok: true });
    if (url.includes("/api/chat/")) return fixtures.chatFn ? fixtures.chatFn() : jsonResponse(fixtures.chat ?? chatView);
    if (url.endsWith("/api/chat")) {
      const body = JSON.parse(String(init?.body)) as unknown;
      return fixtures.post ? fixtures.post(body) : streamResponse(['{"t":"Sure."}', '{"done":{"turn_id":"c3"}}']);
    }
    if (url.includes("/api/task/altitude/persist-paths")) return jsonResponse({ slug: "persist-paths", title: "Persist paths", state: "done", files: { digest: "Shipped." }, messages: [] });
    if (url.includes("/api/l3/engine")) return jsonResponse({ ok: true });
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posted(fetchMock: ReturnType<typeof vi.fn>, path: string, nth = 0) {
  const calls = fetchMock.mock.calls.filter(([u, init]) => String(u).endsWith(path) && (init as RequestInit | undefined)?.method === "POST");
  const call = calls[nth];
  return call ? JSON.parse(String((call[1] as RequestInit | undefined)?.body)) : null;
}

const conversation = () => screen.findByRole("region", { name: "Conversation" });

describe("Conversation", () => {
  it("renders bubbles, prose, day dividers, and the task a turn created", async () => {
    mockFetch();
    renderApp({ route: "/projects/altitude" });
    const region = await conversation();

    const bubble = within(region).getByText("What is left this week?");
    expect(bubble).toHaveClass("bubble");
    expect(bubble.closest(".msg-row")).toHaveAttribute("data-mine", "true");
    const reply = within(region).getByText("Persist paths", { selector: "strong" }).closest(".reply");
    expect(reply).not.toBeNull();
    expect(within(reply as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
    expect(within(region).getAllByRole("separator").map((el) => el.textContent)).toEqual(["Yesterday", "Today"]);
    expect(within(region).getByRole("link", { name: /Fix the timer/ })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer");
    expect(within(region).queryByRole("region", { name: "Transcript" })).toBeNull();
  });

  it("folds a run of system turns into one line and expands it to the list, each with its own Show", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();

    const group = within(region).getByText("L3 handled 2 system events between your messages");
    expect(within(region).queryByText("Closed Persist paths as done.")).toBeNull();
    await user.click(within(group.parentElement as HTMLElement).getByRole("button", { name: "Show" }));

    const list = within(region).getByRole("group", { name: "2 system events" });
    expect(within(list).getByText(/Closed Persist paths as done\.$/)).toBeInTheDocument();
    expect(within(list).getByText(/Resumed nothing; the running task continues\.$/)).toBeInTheDocument();
    expect(within(list).getAllByRole("button", { name: "Show" })).toHaveLength(2);
  });

  it("expands one turn to the card: label/value rows, the reply, and the links", async () => {
    mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    await user.click(within(region).getByRole("button", { name: "Show" }));
    const list = within(region).getByRole("group", { name: "2 system events" });
    await user.click(within(list).getAllByRole("button", { name: "Show" })[0]!);

    const card = await within(region).findByRole("article", { name: "Report landed · Persist paths" });
    expect(within(card).getByText(/^Report landed · Persist paths · /)).toBeInTheDocument();
    expect(within(card).getByText("What altd sent L3")).toBeInTheDocument();
    expect([...card.querySelectorAll("dt")].map((el) => el.textContent)).toEqual(["Verdict", "Problems", "Post-mortem signals", "PRs", "Spend"]);
    expect(within(card).getByText("#12 merged")).toBeInTheDocument();
    expect(within(card).queryByText("persist-paths", { exact: true })).toBeNull();
    expect(within(card).getByText("L3 replied")).toBeInTheDocument();
    expect(within(card).getByText("Checked the PR and the digest.")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/projects/altitude/tasks/persist-paths");
    expect(within(card).getByRole("link", { name: "Full report" })).toHaveAttribute("href", "/projects/altitude/tasks/persist-paths/report");
    expect(await within(card).findByRole("link", { name: "Digest" })).toHaveAttribute("href", "/projects/altitude/tasks/persist-paths/report#digest");
    await user.click(within(card).getByRole("button", { name: "Hide" }));
    expect(within(region).queryByRole("article")).toBeNull();
  });

  it("shows an older, unstructured prompt as preformatted text", async () => {
    mockFetch({
      chat: {
        ...chatView,
        history: [
          { at: ago(5), role: "user", text: "Report landed for `persist-paths`: verdict **done** {\"landed\": {}}", trigger: "report-landed" },
          { at: ago(4), role: "assistant", text: "Done.", trigger: "report-landed" },
        ],
      },
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    await user.click(within(region).getByRole("button", { name: "Show" }));
    const card = within(region).getByRole("article", { name: "Report landed · Persist paths" });
    expect(card.querySelector("pre")).toHaveTextContent("verdict **done**");
    expect(card.querySelector("dl")).toBeNull();
  });

  it("marks a fault with the danger dot and reads a failed turn as L3 could not handle it, with the error behind Show", async () => {
    mockFetch({
      chat: {
        ...chatView,
        history: [
          { at: ago(5), role: "user", text: "Recover the session for altitude/fix-timer", trigger: "system-recovery", turn_id: "r1" },
          { at: ago(4), role: "error", text: "L3 turn failed: engine timed out", trigger: "system-recovery", turn_id: "r1" },
        ],
      },
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const line = within(region).getByText("L3 could not handle a recovery on Fix the timer");
    expect(line.parentElement?.querySelector(".sys-dot")).toHaveAttribute("data-tone", "danger");
    await user.click(within(region).getByRole("button", { name: "Show" }));
    expect(within(region).getByText("Recover the session for altitude/fix-timer")).toBeInTheDocument();
    expect(within(region).getByText("L3 turn failed: engine timed out")).toHaveClass("text-danger");
  });

  it("shows a system turn in progress without Show, and an FYI as a line", async () => {
    mockFetch({
      chat: {
        ...chatView,
        history: [
          { at: ago(3), role: "system", text: "The nightly build is green again.", trigger: "fyi", slug: "fix-timer" },
          { at: ago(1), role: "user", text: reportPrompt, trigger: "report-landed", turn_id: "s9" },
        ],
        active: { id: "s9", started_at: ago(1), trigger: "report-landed" },
        busy: true,
      },
    });
    renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const line = within(region).getByText("L3 is handling a landed report for Persist paths");
    expect(within(line.parentElement as HTMLElement).queryByRole("button", { name: "Show" })).toBeNull();
    expect(within(region).getByText("The nightly build is green again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue" })).toBeInTheDocument();
  });

  it("shows three skeleton rows while loading, then the rows", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    mockFetch({ chatFn: async () => (await gate, jsonResponse(chatView)) });
    renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    expect(within(region).getByLabelText("Loading").querySelectorAll(".skeleton")).toHaveLength(3);
    release();
    await within(region).findByText("What is left this week?");
    expect(within(region).queryByLabelText("Loading")).toBeNull();
  });

  it("says Could not load the conversation. with Retry, and keeps the cached rows on a later failure", async () => {
    let fail = true;
    mockFetch({ chatFn: () => (fail ? jsonResponse({ error: "boom" }, 500) : jsonResponse(chatView)) });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const error = await within(region).findByText(/^Could not load the conversation\./);
    expect(error).toHaveClass("text-danger");
    fail = false;
    await user.click(within(region).getByRole("button", { name: "Retry" }));
    await within(region).findByText("What is left this week?");
    expect(within(region).queryByText(/^Could not load the conversation\./)).toBeNull();

    fail = true;
    await queryClient.invalidateQueries({ queryKey: ["chat", "altitude"] });
    await within(region).findByText(/^Could not load the conversation\./);
    expect(within(region).getByText("What is left this week?")).toBeInTheDocument();
  });

  it("reads the empty copy, and the first-run copy when L3 never ran", async () => {
    mockFetch({ chat: { ...chatView, history: [] } });
    renderApp({ route: "/projects/altitude" });
    expect(await screen.findByText("Say what you want done. L3 answers or creates one task.")).toBeInTheDocument();
  });

  it("streams a reply under the bubble at once, then defers to the server's rows for the turn", async () => {
    const stored = [...history];
    const fetchMock = mockFetch({
      chatFn: () => jsonResponse({ ...chatView, history: stored }),
      post: () => {
        stored.push(
          { at: ago(0), role: "user", text: "Ship it", trigger: "chat", turn_id: "c3" },
          { at: ago(0), role: "assistant", text: "Sure.", trigger: "chat", engine: "alpha", turn_id: "c3" },
        );
        return streamResponse(['{"turn":{"id":"c3","started_at":"2026-09-07T09:14:00+00:00","trigger":"chat"}}', '{"t":"Sure."}', '{"done":{"turn_id":"c3"}}']);
      },
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "Ship it");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await within(region).findByText("Ship it")).toHaveClass("bubble");
    expect(field).toHaveValue("");
    await within(region).findByText("Sure.");
    expect(posted(fetchMock, "/api/chat")).toEqual({ project: "altitude", text: "Ship it" });
    // The poll after the stream carries the turn's rows: one bubble and one reply, never two.
    await waitFor(() => expect(within(region).getAllByText("Ship it")).toHaveLength(1));
    expect(within(region).getAllByText("Sure.")).toHaveLength(1);
    expect(within(region).queryByRole("status", { name: "L3 is answering" })).toBeNull();
  });

  it("refused: the bubble leaves, the draft returns, and the hint reads Not sent. Retry", async () => {
    mockFetch({ post: () => jsonResponse({ error: "no L3 for this project" }, 500) });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "Ship it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not sent. Retry");
    expect(field).toHaveValue("Ship it");
    expect([...region.querySelectorAll(".bubble")].map((el) => el.textContent)).not.toContain("Ship it");
  });

  it("queues while L3 is mid-turn: Queue appends a queued row with Remove, and Remove posts the id", async () => {
    const queuedRow = { id: "q1", at: ago(0), trigger: "chat", role: "user", text: "After that, the badge", position: 1 };
    // The server keeps the queue: a poll after Queue lists the row, a poll after Remove no longer does.
    const queue: (typeof queuedRow)[] = [];
    const fetchMock = mockFetch({
      chatFn: () => jsonResponse({ ...chatView, active: { id: "c2", started_at: ago(9), trigger: "chat" }, busy: true, history: history.slice(0, 7), queued: queue }),
      post: () => {
        queue.push(queuedRow);
        return jsonResponse({ queued: queuedRow });
      },
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    expect(within(region).getByRole("status", { name: "L3 is answering" })).toBeInTheDocument();
    expect(screen.getByText("L3 is mid-turn · runs next")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Message L3 about altitude"), "After that, the badge");
    await user.click(screen.getByRole("button", { name: "Queue" }));

    const list = await within(region).findByRole("list", { name: "Queued messages" });
    expect(within(list).getByText("After that, the badge")).toBeInTheDocument();
    queue.length = 0;
    await user.click(within(list).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(posted(fetchMock, "/api/chat/remove")).toEqual({ project: "altitude", id: "q1" }));
    await waitFor(() => expect(within(region).queryByRole("list", { name: "Queued messages" })).toBeNull());
  });

  it("offers Retry under a failed reply and resends the same prompt", async () => {
    const fetchMock = mockFetch({
      chat: {
        ...chatView,
        history: [
          { at: ago(5), role: "user", text: "Ship it", trigger: "chat", turn_id: "c5" },
          { at: ago(4), role: "error", text: "L3 turn failed", trigger: "chat", turn_id: "c5" },
        ],
      },
    });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const failed = within(region).getByText(/^L3 could not answer this turn\./);
    expect(failed).toHaveClass("text-muted");
    await user.click(within(failed).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(posted(fetchMock, "/api/chat")).toEqual({ project: "altitude", text: "Ship it" }));
  });

  it("pins the engine from the composer's pill with names from the API", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });
    await conversation();
    const pill = screen.getByRole("combobox", { name: "L3 engine" });
    expect(within(pill).getAllByRole("option").map((o) => o.textContent)).toEqual(["Auto", "Alpha"]);
    await user.selectOptions(pill, "alpha");
    await waitFor(() => expect(posted(fetchMock, "/api/l3/engine")).toEqual({ project: "altitude", engine: "alpha" }));
    await user.selectOptions(pill, "");
    await waitFor(() => expect(posted(fetchMock, "/api/l3/engine", 1)).toEqual({ project: "altitude", engine: null }));
  });

  it("shows the project name once on the phone, above the conversation", async () => {
    mockFetch();
    setViewport(390);
    renderApp({ route: "/projects/altitude" });
    await conversation();
    expect(screen.getAllByText("altitude", { selector: "h1 *, h1" })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "altitude" })).toHaveClass("phone-title");
  });
});
