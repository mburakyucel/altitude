import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { ChatView } from "../data/api";
import { HostCapture } from "../components/hostCapture";
import { hostMicrophone, hostVoiceServer, installVoiceBrowser, speak } from "../components/voiceTest";

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
  config: { approval: "ask" },
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

type Fixtures = { chat?: unknown; chatFn?: () => Response | Promise<Response>; post?: (body: unknown) => Response | Promise<Response>; sendNow?: () => Response | Promise<Response> };

let defaults: Record<string, unknown> = {};
const auto = {
  l3_engine: null, l2_engine: null, l3_choice: null, l2_preference: null, l3_unavailable: null, routing: null, roles: [],
  engines: [{ value: "alpha", label: "Alpha", efforts: [], routed: true }],
  models: [{ engine: "alpha", model: "swift", label: "Swift" }, { engine: "alpha", model: "deep", label: "Deep" }],
  efforts: { alpha: [{ value: "low", label: "Low" }, { value: "high", label: "High" }] },
};

function mockFetch(fixtures: Fixtures = {}) {
  defaults = auto;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/project/altitude")) return jsonResponse(project);
    if (url.includes("/api/chat/remove")) return jsonResponse({ ok: true });
    if (url.includes("/api/chat/send-now")) return fixtures.sendNow ? fixtures.sendNow() : jsonResponse({ ok: true });
    if (url.includes("/api/chat/")) return fixtures.chatFn ? fixtures.chatFn() : jsonResponse(fixtures.chat ?? chatView);
    if (url.endsWith("/api/chat")) {
      const body = JSON.parse(String(init?.body)) as unknown;
      return fixtures.post ? fixtures.post(body) : streamResponse(['{"t":"Sure."}', '{"done":{"turn_id":"c3"}}']);
    }
    if (url.includes("/api/task/altitude/persist-paths")) return jsonResponse({ slug: "persist-paths", title: "Persist paths", state: "done", files: { digest: "Shipped." }, messages: [] });
    if (url.endsWith("/api/defaults/altitude")) return jsonResponse(defaults);
    if (url.endsWith("/api/defaults") && init?.method === "POST") {
      defaults = { ...defaults, l3_choice: (JSON.parse(String(init.body)) as { value: null }).value };
      return jsonResponse(defaults);
    }
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
afterEach(() => vi.useRealTimers());

describe("queued L3 Send now", () => {
  it.each([390, 1440])("places incoming before its receiving turn and shows receipt warnings beside successful answers at %i", async (width) => {
    setViewport(width);
    mockFetch({ chat: { ...chatView, history: [
      { role: "user", text: "Ordinary triage request", trigger: "chat", turn_id: "receive" },
      { role: "system", text: "Fictional diagnostic", trigger: "project-message", turn_id: "incoming",
        project_message: { sender: "lab", recipient: "altitude", exchange_id: "exchange", message_id: "incoming",
          summary: "Incoming probe", direction: "incoming", status: "supplied", supplied_turn_id: "receive" } },
      { role: "system", text: "Coordinator message receipt could not be saved; delivery may repeat on the next ordinary turn.",
        trigger: "project-message-error", turn_id: "warning" },
      { role: "assistant", text: "Ordinary triage answer", trigger: "chat", turn_id: "receive" },
    ], queued: [] } });
    renderApp({ route: "/projects/altitude" });
    const incoming = await screen.findByText("lab → altitude · Incoming probe · Incoming");
    const answer = screen.getByText("Ordinary triage answer");
    expect(incoming.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/Coordinator message receipt could not be saved/)).toBeVisible();
    expect(answer).toBeVisible();
    expect(screen.queryByText(/L3 could not answer this turn/)).toBeNull();
  });
  it.each([390, 1440])("does not count information as a runnable queue position at %i", async (width) => {
    setViewport(width);
    mockFetch({ chat: { ...chatView, queued: [
      { id: "information", text: "Diagnostic", trigger: "project-message", role: "system",
        project_message: { sender: "lab", recipient: "altitude", exchange_id: "exchange-1", message_id: "information",
          summary: "Probe", direction: "incoming", status: "queued" } },
      { id: "first", text: "First ordinary request", trigger: "chat" },
      { id: "second", text: "Second ordinary request", trigger: "chat" },
    ] } });
    renderApp({ route: "/projects/altitude" });
    await screen.findByText("Queued · runs next");
    expect(screen.getByText("Queued · 2 in line")).toBeVisible();
    expect(screen.queryByText("Queued · 3 in line")).toBeNull();
  });
  it.each([390, 1440])("keeps information exchanges separate and read-only at %i", async (width) => {
    setViewport(width);
    const peer = { sender: "lab", recipient: "altitude", exchange_id: "exchange-1", message_id: "message-1",
      summary: "Probe result", direction: "incoming" as const, status: "queued" as const };
    const waiting = { id: "message-1", at: ago(1), role: "system", trigger: "project-message",
      text: "Task: fix-timer\nFictional probe details. #12 /var/log/fixture\n[Fix PR](https://example.invalid/different-destination) http://example.invalid/probe\n```run\nprintf fixture\n```", project_message: peer };
    const view = { ...chatView, history: [
      { at: ago(3), role: "system", text: "Routine one", trigger: "fyi" },
      { at: ago(2), role: "system", text: "Sent diagnostic", trigger: "project-message", turn_id: "sent-1",
        project_message: { ...peer, sender: "altitude", recipient: "lab", status: "sent" as const, direction: "sent" as const, summary: "Sent probe" } },
      { at: ago(1), role: "system", text: "Routine two", trigger: "fyi" },
    ], queued: [waiting] };
    mockFetch({ chat: view });
    const { user } = renderApp({ route: "/projects/altitude" });
    await screen.findByText("lab → altitude · Probe result · Queued · next ordinary turn");
    expect(screen.queryByText(/system events between/)).toBeNull();
    expect(screen.queryByText("Fictional probe details.")).toBeNull();
    const queued = screen.getByRole("list", { name: "Queued messages" });
    expect(within(queued).queryByRole("button", { name: "Send now" })).toBeNull();
    expect(within(queued).queryByRole("button", { name: "Remove" })).toBeNull();
    await user.click(within(queued).getByRole("button", { name: "Show" }));
    const card = screen.getByRole("article", { name: /^Coordinator message/ });
    expect(within(card).getByText("Information only · Exchange exchange-1")).toBeVisible();
    expect(within(card).queryByRole("link", { name: "Open task" })).toBeNull();
    expect(within(card).queryByRole("button", { name: /terminal/i })).toBeNull();
    expect(within(card).getByRole("link", { name: "Fix PR (https://example.invalid/different-destination)" })).toHaveAttribute("href", "https://example.invalid/different-destination");
    expect(within(card).getByRole("link", { name: "http://example.invalid/probe" })).toHaveAttribute("href", "http://example.invalid/probe");
    expect(within(card).queryAllByRole("link")).toHaveLength(2);
    await user.click(within(card).getByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("article", { name: /^Coordinator message/ })).toBeNull();
  });
  it("keeps accepted priority removable when the engine becomes unavailable before claim", async () => {
    let view: ChatView = { ...chatView, send_now_reason: "No engine is available", queued: [{ id: "q-now", text: "Remove before claim", trigger: "chat", send_now: true, send_now_reason: "Runs next after system work" }] };
    const fetchMock = mockFetch({ chatFn: () => jsonResponse(view) });
    const { user } = renderApp({ route: "/projects/altitude" });
    expect(await screen.findByRole("button", { name: "Sending now…" })).toBeDisabled();
    expect(screen.getByText("No engine is available")).toBeVisible();
    const remove = screen.getByRole("button", { name: "Remove" });
    expect(remove).toBeEnabled();
    view = { ...view, queued: [] };
    await user.click(remove);
    await waitFor(() => expect(screen.queryByText("Remove before claim")).toBeNull());
    expect(posted(fetchMock, "/api/chat/remove")).toEqual({ project: "altitude", id: "q-now" });
  });

  it("waits for the canonical receipt and prevents removal or repeated requests while sending", async () => {
    let release!: (response: Response) => void;
    let view: ChatView = { ...chatView, queued: [{ id: "q-now", text: "Do this first", trigger: "chat" }] };
    const fetchMock = mockFetch({ chatFn: () => jsonResponse(view), sendNow: () => new Promise((resolve) => { release = resolve; }) });
    const { user } = renderApp({ route: "/projects/altitude" });
    await user.click(await screen.findByRole("button", { name: "Send now" }));
    expect(screen.getByRole("button", { name: "Sending now…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
    expect(screen.getByText("Do this first").closest(".queued-row")).not.toBeNull();
    view = { ...view, queued: [], history: [...history, { role: "user", text: "Do this first", trigger: "chat", turn_id: "next" }] };
    await act(async () => release(jsonResponse({ ok: true })));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Sending now…" })).toBeNull());
    expect(screen.getAllByText("Do this first")).toHaveLength(1);
    expect(posted(fetchMock, "/api/chat/send-now")).toEqual({ project: "altitude", id: "q-now" });
  });

  it.each([390, 1440])("keeps a message sent during an engine hold under its bubble, then attaches the reply at %i", async (width) => {
    setViewport(width);
    const kept = { id: "held", turn_id: "held", at: ago(0), text: "Are you there?", trigger: "chat", role: "user" };
    const sent = { at: ago(0), role: "user", text: "Are you there?", trigger: "chat", turn_id: "held" };
    let view: ChatView = chatView;
    mockFetch({
      chatFn: () => jsonResponse(view),
      post: () => {
        view = { ...chatView, send_now_reason: "No engine is available. The message stays queued.", history: [...history, sent],
          queued: [kept, { id: "later", at: ago(0), text: "System work", trigger: "restart" }] };
        return streamResponse([JSON.stringify({ queued: kept })]);
      },
    });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    await user.type(await screen.findByRole("textbox", { name: "Message L3 about altitude" }), "Are you there?{Enter}");

    const turn = await waitFor(() => {
      const node = document.querySelector('[data-turn="held"]');
      expect(node).not.toBeNull();
      return node as HTMLElement;
    });
    await waitFor(() => expect(screen.getAllByText("Are you there?")).toHaveLength(1));
    expect(within(turn).getByText("Queued · runs next")).toBeVisible();
    expect(within(turn).getByRole("button", { name: "Send now" })).toBeDisabled();
    expect(within(turn).getByText("No engine is available. The message stays queued.")).toBeVisible();
    expect(within(turn).queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.queryByText(/could not answer/)).toBeNull();
    expect(screen.queryByText(/engine hold|unavailable:/)).toBeNull();
    expect(within(screen.getByRole("list", { name: "Queued messages" })).queryByText("Are you there?")).toBeNull();

    view = { ...chatView, history: [...history, sent, { at: ago(0), role: "assistant", text: "Here now.", trigger: "chat", engine: "alpha", turn_id: "held" }] };
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["chat", "altitude"] }); });
    await waitFor(() => expect(within(document.querySelector('[data-turn="held"]') as HTMLElement).getByText("Here now.")).toBeVisible());
    expect(screen.queryByText("Queued · runs next")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
  });

  it("lists a kept message whose bubble is older than the loaded history, without Remove", async () => {
    mockFetch({ chat: { ...chatView, send_now_reason: "No engine is available. The message stays queued.", queued: [
      { id: "old-held", turn_id: "old-held", at: ago(90), text: "Earlier kept question", trigger: "chat", role: "user" },
    ] } });
    renderApp({ route: "/projects/altitude" });
    const list = await screen.findByRole("list", { name: "Queued messages" });
    expect(within(list).getByText("Earlier kept question")).toBeVisible();
    expect(within(list).getByText("Queued · runs next")).toBeVisible();
    expect(within(list).getByRole("button", { name: "Send now" })).toBeDisabled();
    expect(within(list).queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("explains unavailable delivery without offering system rows an action", async () => {
    mockFetch({ chat: { ...chatView, send_now_reason: "No engine is available", queued: [
      { id: "chat", text: "Wait for capacity", trigger: "chat" }, { id: "system", text: "System work", trigger: "restart" },
    ] } });
    renderApp({ route: "/projects/altitude" });
    expect(await screen.findByRole("button", { name: "Send now" })).toBeDisabled();
    expect(screen.getByText("No engine is available")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Send now" })).toHaveLength(1);
  });
});

/** Separate server snapshots and delayed network responses exercise the real route and stream reader. */
function projectChats(post: (body: { project: string; text: string }) => Response | Promise<Response>) {
  const names = ["alpha-project", "beta-project"];
  const chats = Object.fromEntries(names.map((name) => [name, {
    ...chatView,
    history: [{ role: "assistant", text: `${name} history`, trigger: "chat", turn_id: `${name}-saved`, at: ago(5) }],
  }])) as Record<string, ChatView>;
  const voice = hostVoiceServer({ words: "spoken words", final: "spoken words" });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/voice/live")) return voice.fetch(input, init);
    if (url === "/api/overview") return jsonResponse({ ...overview, projects: names.map((name) => ({ name, managed: true })) });
    if (url.startsWith("/api/project/")) return jsonResponse({ ...project, name: url.split("/").pop(), tasks: [], archive: [] });
    if (url.startsWith("/api/chat/")) return jsonResponse(chats[url.split("?")[0]!.split("/").pop()!]);
    if (url === "/api/chat") return post(JSON.parse(String(init?.body)));
    return jsonResponse({ error: "unexpected request" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { chats, fetchMock, voice };
}

function liveReply() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let closed = false;
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }));
  return {
    response,
    frame(value: unknown) { controller.enqueue(new TextEncoder().encode(`${JSON.stringify(value)}\n`)); },
    close() { if (!closed) { closed = true; controller.close(); } },
    fail() { closed = true; controller.error(new TypeError("Connection interrupted")); },
  };
}

describe.each([390, 1440])("project switching at %ipx", (width) => {
  const field = (name: string) => screen.getByRole("textbox", { name: `Message L3 about ${name}-project` });

  it("canonical completion replaces a stalled stream and ignores its late chunks", async () => {
    const reply = liveReply();
    const { chats } = projectChats(() => reply.response);
    setViewport(width);
    const { user, queryClient } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    try {
      await user.type(field("alpha"), "Inspect sample");
      await user.click(screen.getByRole("button", { name: "Send" }));
      await act(async () => {
        reply.frame({ turn: { id: "stalled", started_at: ago(0), trigger: "chat" } });
        reply.frame({ t: "Partial answer" });
      });
      await screen.findByText("Partial answer");
      await user.type(field("alpha"), "Next draft");
      chats["alpha-project"]!.history.push(
        { role: "user", text: "Inspect sample", trigger: "chat", turn_id: "stalled" },
        { role: "assistant", text: "Canonical complete answer", trigger: "chat", turn_id: "stalled" },
      );
      await act(async () => { await queryClient.invalidateQueries({ queryKey: ["chat", "alpha-project"] }); });
      expect(await screen.findByText("Canonical complete answer")).toBeInTheDocument();
      expect(screen.queryByText("Partial answer")).toBeNull();
      await act(async () => reply.frame({ t: " obsolete fragment" }));
      expect(screen.queryByText(/obsolete fragment/)).toBeNull();
      expect(field("alpha")).toHaveValue("Next draft");
    } finally { await act(async () => reply.close()); }
  });

  it("a queue receipt keeps an accepted preview through a failed refresh and read Retry never resends", async () => {
    let failReads = false;
    const { chats, fetchMock } = projectChats(() => {
      failReads = true;
      return jsonResponse({ queued: { id: "accepted-queue", text: "Accepted sample", at: ago(0), trigger: "chat" } });
    });
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => failReads && String(input).startsWith("/api/chat/alpha-project")
      ? Promise.resolve(jsonResponse({ error: "Read unavailable" }, 503)) : original(input, init));
    setViewport(width);
    const { user, queryClient } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    await user.type(field("alpha"), "Accepted sample");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Could not load the conversation.", { exact: false });
    await user.type(field("alpha"), "Newer draft");
    expect(screen.getByText("Accepted sample")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Queued messages" })).toBeNull();
    expect(screen.queryByText(/Could not confirm delivery/)).toBeNull();
    expect(sessionStorage.getItem("altitude.submitted:project/alpha-project")).toBeNull();
    // Engine selection/rollback can update the cache without learning anything about delivery.
    await act(async () => {
      queryClient.setQueryData<ChatView>(["chat", "alpha-project"], (cached) => cached && { ...cached, engine: "alpha" });
      await queryClient.invalidateQueries({ queryKey: ["chat", "alpha-project"] });
    });
    expect(screen.getByText("Accepted sample")).toBeInTheDocument();
    // The real queue may have been removed by another view; an empty fresh snapshot owns that fact.
    chats["alpha-project"]!.queued = [];
    failReads = false;
    await user.click(screen.getByRole("button", { name: /^Retry$/ }));
    await waitFor(() => expect(screen.queryByText("Accepted sample")).toBeNull());
    expect(field("alpha")).toHaveValue("Newer draft");
    expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/chat" && init?.method === "POST")).toHaveLength(1);
  });

  it("overlapping accepted sends reconstruct after navigation and failed reads without restoring drafts", async () => {
    let failReads = false;
    const { chats, fetchMock } = projectChats(({ project, text }) => {
      const row = { id: `receipt-${text}`, text, trigger: "chat", at: ago(0) };
      chats[project]!.queued = [...(chats[project]!.queued ?? []), row];
      failReads = true;
      return jsonResponse({ queued: row });
    });
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => failReads && String(input).startsWith("/api/chat/alpha-project")
      ? Promise.resolve(jsonResponse({ error: "Read unavailable" }, 503)) : original(input, init));
    setViewport(width);
    const { user, router } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    for (const text of ["First accepted", "Second accepted"]) {
      await user.type(field("alpha"), text);
      await user.click(screen.getByRole("button", { name: "Send" }));
      await screen.findByText("Could not load the conversation.", { exact: false });
      await waitFor(() => expect(sessionStorage.getItem("altitude.submitted:project/alpha-project")).toBeNull());
    }
    await user.type(field("alpha"), "Newer source draft");
    await act(() => router.navigate("/projects/beta-project"));
    await user.type(field("beta"), "Independent draft");
    expect(screen.queryByRole("alert")).toBeNull();
    await act(() => router.navigate("/projects/alpha-project"));
    await screen.findByText("Could not load the conversation.", { exact: false });
    expect(field("alpha")).toHaveValue("Newer source draft");
    expect(screen.queryByText(/Could not confirm delivery/)).toBeNull();
    failReads = false;
    await user.click(screen.getByRole("button", { name: /^Retry$/ }));
    const queue = await screen.findByRole("list", { name: "Queued messages" });
    expect(within(queue).getAllByRole("listitem").map((row) => row.textContent)).toEqual([
      "First acceptedQueued · runs nextSend nowRemove", "Second acceptedQueued · 2 in lineSend nowRemove",
    ]);
    expect(field("alpha")).toHaveValue("Newer source draft");
    expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/chat" && init?.method === "POST")).toHaveLength(2);
  });

  it("an accepted image replay stops saying Sending while its history refresh fails", async () => {
    let failReads = false;
    const { chats, fetchMock } = projectChats(() => {
      failReads = true;
      return jsonResponse({ accepted: true, queued: { id: "image-replay", text: "Image sample", at: ago(0), trigger: "chat" } });
    });
    chats["alpha-project"]!.history.push(
      { role: "user", text: "Image sample", turn_id: "failed-image", trigger: "chat", images: [
        { id: "sample-image", name: "sample.png", mime_type: "image/png", size: 10, width: 1, height: 1, source_message_id: "original-image" },
      ] },
      { role: "error", text: "Fixture answer failed", turn_id: "failed-image", trigger: "chat" },
    );
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => failReads && String(input).startsWith("/api/chat/alpha-project")
      ? Promise.resolve(jsonResponse({ error: "Read unavailable" }, 503)) : original(input, init));
    setViewport(width);
    const { user } = renderApp({ route: "/projects/alpha-project" });
    const failed = await screen.findByText(/^L3 could not answer this turn\./);
    await user.click(within(failed).getByRole("button", { name: "Retry" }));
    await screen.findByText("Could not load the conversation.", { exact: false });
    expect(screen.queryByText("Sending images…")).toBeNull();
    expect(screen.queryByText("Could not confirm send.", { exact: false })).toBeNull();
    expect(field("alpha")).toHaveValue("");
    expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/chat" && init?.method === "POST")).toHaveLength(1);
  });

  it("prevents an earlier read replacing a late queue receipt while its conversation is unmounted", async () => {
    let receipt!: (response: Response) => void;
    let stale!: (response: Response) => void;
    const pendingReceipt = new Promise<Response>((resolve) => { receipt = resolve; });
    const pendingRead = new Promise<Response>((resolve) => { stale = resolve; });
    const { chats, fetchMock } = projectChats(() => pendingReceipt);
    const original = fetchMock.getMockImplementation()!;
    // Hold the read this test starts, whichever of the conversation's polls came before it.
    let holdNextRead = false;
    fetchMock.mockImplementation((input, init) => {
      if (holdNextRead && String(input).startsWith("/api/chat/alpha-project")) {
        holdNextRead = false;
        return pendingRead;
      }
      return original(input, init);
    });
    setViewport(width);
    const { router, user, queryClient } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    await user.type(field("alpha"), "Queued sample request");
    await user.click(screen.getByRole("button", { name: "Send" }));
    holdNextRead = true;
    void queryClient.invalidateQueries({ queryKey: ["chat", "alpha-project"] });
    await waitFor(() => expect(holdNextRead).toBe(false));
    const old = jsonResponse(chats["alpha-project"]);
    await act(() => router.navigate("/projects/beta-project"));
    await screen.findByText("beta-project history");
    const row = { id: "late-queue", at: ago(0), text: "Queued sample request", trigger: "chat" };
    chats["alpha-project"]!.queued = [row];
    await act(async () => receipt(jsonResponse({ queued: row })));
    await act(async () => stale(old));
    expect(queryClient.getQueryData<ChatView>(["chat", "alpha-project"])?.queued).toEqual([row]);
    await act(() => router.navigate("/projects/alpha-project"));
    await screen.findByText("Queued sample request");
    expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/chat" && init?.method === "POST")).toHaveLength(1);
  });

  it("recovers an unconfirmed submitted message in its source conversation after navigation", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise<Response>((_, fail) => { reject = fail; });
    const { fetchMock } = projectChats(() => pending);
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    await user.type(field("alpha"), "Please inspect the sample project");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field("alpha"), "Alpha next draft");
    await act(() => router.navigate("/projects/beta-project"));
    await screen.findByText("beta-project history");
    await user.type(field("beta"), "Keep this new draft");
    await act(async () => { reject(new TypeError("Controlled lost receipt")); });
    expect(field("beta")).toHaveValue("Keep this new draft");
    expect(screen.queryByRole("alert")).toBeNull();
    await act(() => router.navigate("/projects/alpha-project"));
    await screen.findByText("alpha-project history");
    expect(field("alpha")).toHaveValue("Please inspect the sample project\nAlpha next draft");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/chat" && init?.method === "POST")).toHaveLength(1);
    await act(() => router.navigate("/projects/beta-project"));
    expect(field("beta")).toHaveValue("Keep this new draft");
    await act(() => router.navigate("/projects/alpha-project"));
    expect(field("alpha")).toHaveValue("Please inspect the sample project\nAlpha next draft");
  });

  it("polls accepted text after returning before the original send receives its turn receipt", async () => {
    const reply = liveReply();
    const { chats } = projectChats(() => reply.response);
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    try {
      await user.type(field("alpha"), "Please inspect the sample project");
      await user.click(screen.getByRole("button", { name: "Send" }));
      await act(() => router.navigate("/projects/beta-project"));
      await screen.findByText("beta-project history");
      chats["alpha-project"]!.busy = true;
      await act(() => router.navigate("/projects/alpha-project"));
      await screen.findByText("alpha-project history");
      await screen.findByRole("button", { name: "Queue" });
      const turn = { id: "accepted-after-return", started_at: ago(0), trigger: "chat" };
      chats["alpha-project"]!.active = turn;
      chats["alpha-project"]!.history.push({ role: "user", text: "Please inspect the sample project", turn_id: turn.id });
      await act(async () => { reply.frame({ turn }); });
      // The response still belongs to the departed component. The mounted conversation must read
      // its server state while that stream remains open, including after background throttling.
      await waitFor(() => expect(screen.getByText("Please inspect the sample project")).toBeInTheDocument());
    } finally {
      await act(async () => reply.close());
    }
  });

  it("releases the source microphone on switching without sending or transcribing its recording", async () => {
    const { fetchMock, voice } = projectChats(() => { throw new Error("No recording should be submitted"); });
    const { track } = installVoiceBrowser();
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
    speak();
    await screen.findByText("Listening… Stop to add text, or Send.");
    await waitFor(() => expect(field("alpha")).toHaveValue("spoken words"));
    expect(track.stop).not.toHaveBeenCalled();
    await act(() => router.navigate("/projects/beta-project"));
    await screen.findByText("beta-project history");
    expect(HostCapture.retained.size).toBe(0);
    expect(track.stop).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Stop voice input" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start voice input" })).toBeEnabled();
    expect(field("beta")).toHaveValue("");
    // The host drops the recording; no last words are asked for and nothing is sent.
    await waitFor(() => expect(voice.calls.at(-1)?.path).toBe("/api/voice/live/rec-1/cancel"));
    expect(voice.audio.some((call) => call.final)).toBe(false);
    expect(fetchMock.mock.calls.filter(([url, init]) => init?.method === "POST" && !String(url).startsWith("/api/voice/live"))).toEqual([]);
  });

  it("retains independent drafts across project and route remounts, including manual clearing", async () => {
    projectChats(() => { throw new Error("No draft should be submitted"); });
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    await user.type(field("alpha"), "Alpha private draft");
    await act(() => router.navigate("/projects/beta-project"));
    await screen.findByText("beta-project history");
    expect(field("beta")).toHaveValue("");
    expect(screen.queryByText("alpha-project history")).toBeNull();
    await user.type(field("beta"), "Beta private draft");
    await act(() => router.navigate("/projects/alpha-project"));
    await screen.findByText("alpha-project history");
    expect(field("alpha")).toHaveValue("Alpha private draft");
    expect(screen.queryByText("beta-project history")).toBeNull();
    await user.clear(field("alpha"));
    await act(() => router.navigate("/projects/beta-project"));
    expect(field("beta")).toHaveValue("Beta private draft");
    await act(() => router.navigate("/monitor"));
    await act(() => router.navigate("/projects/beta-project"));
    expect(field("beta")).toHaveValue("Beta private draft");
    await act(() => router.navigate("/projects/alpha-project"));
    expect(field("alpha")).toHaveValue("");
  });

  it("removes a pending send on switching and keeps a late refusal out of the destination draft", async () => {
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    const { fetchMock } = projectChats(() => pending);
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    try {
      await user.type(field("alpha"), "Alpha pending request");
      await user.click(screen.getByRole("button", { name: "Send" }));
      expect(screen.getByText("Alpha pending request").closest(".msg-row")).toHaveAttribute("data-pending");
      await act(() => router.navigate("/projects/beta-project"));
      await screen.findByText("beta-project history");
      expect(screen.queryByText("Alpha pending request")).toBeNull();
      await user.type(field("beta"), "Beta draft survives");
      await act(async () => { release(jsonResponse({ error: "Alpha refused" }, 503)); });
      expect(field("beta")).toHaveValue("Beta draft survives");
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(posted(fetchMock, "/api/chat")).toEqual({ project: "alpha-project", text: "Alpha pending request" });
    } finally {
      await act(async () => { release(jsonResponse({ error: "refused" }, 503)); });
    }
  });

  it.each(["alpha", "beta"])("isolates concurrent streams when %s finishes first, and restores source history on return", async (first) => {
    const replies = { "alpha-project": liveReply(), "beta-project": liveReply() };
    const { chats, fetchMock } = projectChats((body) => {
      const turn = { id: `${body.project}-turn`, started_at: ago(0), trigger: "chat" };
      chats[body.project]!.active = turn;
      chats[body.project]!.history.push({ role: "user", text: body.text, turn_id: turn.id, trigger: "chat", at: ago(0) });
      const reply = replies[body.project as keyof typeof replies];
      reply.frame({ turn });
      reply.frame({ t: `${body.project} partial` });
      return reply.response;
    });
    const finish = async (name: string) => {
      const owner = `${name}-project` as keyof typeof replies;
      chats[owner]!.history.push({ role: "assistant", text: `${owner} complete`, turn_id: `${owner}-turn`, trigger: "chat", at: ago(0) });
      chats[owner]!.active = null;
      await act(async () => {
        replies[owner].frame({ t: " complete" });
        replies[owner].frame({ done: { turn_id: `${owner}-turn` } });
        replies[owner].close();
      });
    };
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    try {
      await user.type(field("alpha"), "Alpha prompt");
      await user.click(screen.getByRole("button", { name: "Send" }));
      await screen.findByText("alpha-project partial");
      await act(() => router.navigate("/projects/beta-project"));
      await screen.findByText("beta-project history");
      expect(screen.queryByText("Alpha prompt")).toBeNull();
      expect(screen.queryByText("alpha-project partial")).toBeNull();
      expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
      await user.type(field("beta"), "Beta prompt");
      await user.click(screen.getByRole("button", { name: "Send" }));
      await screen.findByText("beta-project partial");
      await finish(first);
      await finish(first === "alpha" ? "beta" : "alpha");
      await screen.findByText("beta-project complete");
      expect(screen.queryByText("alpha-project complete")).toBeNull();
      expect(posted(fetchMock, "/api/chat", 0)).toEqual({ project: "alpha-project", text: "Alpha prompt" });
      expect(posted(fetchMock, "/api/chat", 1)).toEqual({ project: "beta-project", text: "Beta prompt" });
      await act(() => router.navigate("/projects/alpha-project"));
      await screen.findByText("alpha-project complete");
      expect(screen.getAllByText("Alpha prompt")).toHaveLength(1);
      expect(screen.queryByText("Beta prompt")).toBeNull();
      expect(screen.queryByText("beta-project complete")).toBeNull();
    } finally {
      await act(async () => { Object.values(replies).forEach((reply) => reply.close()); });
    }
  });

  it("keeps a late failed turn and its Retry in Alpha, including a switch back while it is active", async () => {
    const reply = liveReply();
    const { chats, fetchMock } = projectChats((body) => {
      if (body.text === "Alpha fails" && !chats[body.project]!.history.some((row) => row.role === "error")) {
        const turn = { id: "alpha-failed", started_at: ago(0), trigger: "chat" };
        chats[body.project]!.active = turn;
        chats[body.project]!.history.push({ role: "user", text: body.text, turn_id: turn.id, trigger: "chat", at: ago(0) });
        reply.frame({ turn });
        return reply.response;
      }
      return streamResponse(['{"t":"Retry accepted in Alpha"}', '{"done":{"turn_id":"alpha-retried"}}']);
    });
    setViewport(width);
    const { router, user } = renderApp({ route: "/projects/alpha-project" });
    await screen.findByText("alpha-project history");
    try {
      await user.type(field("alpha"), "Alpha fails");
      await user.click(screen.getByRole("button", { name: "Send" }));
      await screen.findByRole("status", { name: "L3 is answering" });
      await act(() => router.navigate("/projects/beta-project"));
      await screen.findByText("beta-project history");
      await act(() => router.navigate("/projects/alpha-project"));
      await screen.findByText("Alpha fails");
      expect(screen.getByRole("status", { name: "L3 is answering" })).toBeInTheDocument();
      await act(() => router.navigate("/projects/beta-project"));
      await screen.findByText("beta-project history");
      await user.type(field("beta"), "Beta keeps typing");
      chats["alpha-project"]!.history.push({ role: "error", text: "provider failed", trigger: "chat", turn_id: "alpha-failed", at: ago(0) });
      chats["alpha-project"]!.active = null;
      await act(async () => { reply.frame({ done: { turn_id: "alpha-failed", error: "provider failed" } }); reply.close(); });
      expect(field("beta")).toHaveValue("Beta keeps typing");
      expect(screen.queryByText(/L3 could not answer/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      await act(() => router.navigate("/projects/alpha-project"));
      await user.click(await screen.findByRole("button", { name: "Retry" }));
      await waitFor(() => expect(posted(fetchMock, "/api/chat", 1)).toEqual({ project: "alpha-project", text: "Alpha fails" }));
    } finally {
      await act(async () => { reply.close(); });
    }
  });
});

describe("Conversation", () => {
  it.each([390, 1440])("keeps a selected heads-up between routine groups at %ipx with chat, active turns and evidence intact", async (width) => {
    setViewport(width);
    const quiet = [
      { role: "system", trigger: "fyi", text: "Automatic fault details", by: "altd", heads_up: false },
      { role: "system", trigger: "fyi", text: "Historical ambiguous author", by: "l3" },
      { role: "system", trigger: "fyi", text: "Unattributed historical FYI" },
      { role: "system", trigger: "fyi", text: "Owner progress", by: "l2", heads_up: false },
    ];
    mockFetch({ chat: {
      ...chatView,
      history: [
        ...history.slice(0, 6),
        { role: "system", trigger: "fyi", text: "The build is blocked.\n\nAn owner is investigating.", by: "l3", heads_up: true, slug: "persist-paths" },
        ...quiet,
        ...history.slice(6),
        { role: "user", trigger: "restart", text: "Inspect the restart", turn_id: "active" },
      ],
      active: { id: "active", started_at: ago(1), trigger: "restart" }, busy: true,
    } });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const headsUp = () => within(region).getByText(/The build is blocked\.\s+An owner is investigating\./);
    expect(headsUp()).toBeVisible();
    expect(headsUp().closest(".sys-group")).toBeNull();
    expect(within(region).getByText("What is left this week?")).toHaveClass("bubble");
    expect(within(region).getByText("Created one task for it.")).toBeVisible();
    expect(within(region).getByRole("link", { name: /Fix the timer/ })).toBeVisible();
    const active = within(region).getByText("L3 is handling the restart").parentElement!;
    expect(active.closest(".sys-line")?.querySelector(".sys-time")).toHaveTextContent("time unavailable");
    expect(within(active).queryByRole("button", { name: "Show" })).toBeNull();
    for (const row of quiet) expect(within(region).queryByText(row.text)).toBeNull();
    const before = within(region).getByText("L3 handled 2 system events between your messages");
    const after = within(region).getByText("L3 handled 4 system events between your messages");
    expect(before.compareDocumentPosition(headsUp()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(headsUp().compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(within(after.parentElement!).getByRole("button", { name: "Show" }));
    const group = within(region).getByRole("group", { name: "4 system events" });
    for (const row of quiet) expect(within(group).getByText(row.text)).toBeVisible();
    expect(headsUp()).toBeVisible();
    await user.click(within(group).getByRole("button", { name: "Hide" }));
    expect(within(region).queryByRole("group")).toBeNull();
    await user.click(within(headsUp().closest(".sys-line")!).getByRole("button", { name: "Show" }));
    const card = within(region).getByRole("article", { name: "FYI · Persist paths" });
    expect(within(card).getByText("The build is blocked.")).toBeVisible();
    expect(within(card).getByText("An owner is investigating.")).toBeVisible();
    expect(within(card).getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/projects/altitude/tasks/persist-paths");
    expect(within(card).queryByText("What altd sent L3")).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Hide" }));
    expect(headsUp()).toBeVisible();
    expect(within(region).queryByRole("article")).toBeNull();
  });

  it.each([0, 12])("renders bubbles, prose, day dividers, and the task a turn created at hour %i", async (hour) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 11, hour, 3));
    const datedHistory = history.map((row, index) => ({
      ...row, at: new Date(2026, 8, index < 2 ? 10 : 11, 0, 0, index).toISOString(),
    }));
    mockFetch({ chat: { ...chatView, history: datedHistory } });
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
    // Each system event carries its recorded time, readable in the line and exact on hover.
    const times = [...list.querySelectorAll(".sys-line[data-turn] .sys-time")];
    expect(times).toHaveLength(2);
    for (const time of times) {
      expect(time.tagName).toBe("TIME");
      expect(time.getAttribute("title")).not.toBe("");
    }
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

  it("appends the bubble at once with a sending cue and settles the same bubble in place when the stream accepts it", async () => {
    const reply = liveReply();
    const stored = [...history];
    mockFetch({ chatFn: () => jsonResponse({ ...chatView, history: stored }), post: () => reply.response });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    await user.type(screen.getByLabelText("Message L3 about altitude"), "Hold the line");
    await user.click(screen.getByRole("button", { name: "Send" }));
    const row = within(region).getByText("Hold the line").closest<HTMLElement>(".msg-row")!;
    expect(row).toHaveAttribute("data-pending");
    expect(within(row).getByRole("status", { name: "Sending" })).toBeInTheDocument();
    expect(region.querySelectorAll("[data-pending]")).toHaveLength(1);

    await act(async () => { reply.frame({ turn: { id: "c9", started_at: ago(0), trigger: "chat" } }); });
    await waitFor(() => expect(row).not.toHaveAttribute("data-pending"));
    expect(within(region).getByText("Hold the line").closest<HTMLElement>(".msg-row")).toBe(row);
    expect(within(row).queryByRole("status", { name: "Sending" })).toBeNull();
    expect(within(region).getByRole("status", { name: "L3 is answering" })).toBeInTheDocument();

    await act(async () => { reply.frame({ t: "Held." }); reply.frame({ done: { turn_id: "c9" } }); reply.close(); });
    await within(region).findByText("Held.");
    stored.push(
      { at: ago(0), role: "user", text: "Hold the line", trigger: "chat", turn_id: "c9" },
      { at: ago(0), role: "assistant", text: "Held.", trigger: "chat", engine: "alpha", turn_id: "c9" },
    );
    await act(() => queryClient.invalidateQueries({ queryKey: ["chat", "altitude"] }));
    await waitFor(() => expect(region.querySelector("[data-local]")).toBeNull());
    expect(within(region).getAllByText("Hold the line")).toHaveLength(1);
    expect(within(region).getAllByText("Held.")).toHaveLength(1);
    expect(region.querySelector("[data-pending]")).toBeNull();
  });

  it("refused: the bubble leaves, the draft returns, and the hint reads Not sent. Retry", async () => {
    mockFetch({ post: () => jsonResponse({ error: "no L3 for this project" }, 409) });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "Ship it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not sent. Retry");
    expect(field).toHaveValue("Ship it");
    expect([...region.querySelectorAll(".bubble")].map((el) => el.textContent)).not.toContain("Ship it");
    expect(region.querySelector("[data-pending]")).toBeNull();
  });

  it.each(["", "My next draft"])("keeps an accepted message sent after stream failure with draft '%s' and failed refresh", async (draft) => {
    const reply = liveReply();
    let failRefresh = false;
    const turn = { id: "accepted-turn", started_at: ago(0), trigger: "chat" };
    const stored = [...history, { at: ago(0), role: "user", text: "Ship it", trigger: "chat", turn_id: turn.id }];
    const fetchMock = mockFetch({
      chatFn: () => failRefresh ? jsonResponse({ error: "Read unavailable" }, 503) : jsonResponse({ ...chatView, history: stored, active: turn, busy: true }),
      post: () => reply.response,
    });
    const { user, queryClient } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    await within(region).findByText("Ship it");
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "Ship it");
    await user.click(screen.getByRole("button", { name: "Queue" }));
    await act(async () => { reply.frame({ turn }); });
    if (draft) await user.type(field, draft);
    failRefresh = true;
    await act(async () => { reply.fail(); });
    await within(region).findByText("Could not load the conversation.", { exact: false });
    expect(field).toHaveValue(draft);
    expect(region.querySelector(".composer-hint[role=alert]")).toBeNull();
    expect(region.querySelector(".turn-failed")).toBeNull();
    expect(within(region).getAllByText("Ship it")).toHaveLength(1);
    failRefresh = false;
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["chat", "altitude"] }); });
    expect(field).toHaveValue(draft);
    expect(within(region).getAllByText("Ship it")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/chat")).toHaveLength(1);
  });

  it("keeps an earlier stream callback out of a later pending queue request", async () => {
    const reply = liveReply();
    let release!: (response: Response) => void;
    const waiting = new Promise<Response>((resolve) => { release = resolve; });
    let posts = 0;
    const fetchMock = mockFetch({ post: () => ++posts === 1 ? reply.response : waiting });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "First request");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => { reply.frame({ turn: { id: "first-turn", started_at: ago(0), trigger: "chat" } }); });
    await user.type(field, "Queued request");
    await user.click(screen.getByRole("button", { name: "Queue" }));
    await act(async () => { reply.frame({ t: "Earlier answer" }); });
    const pending = region.querySelector("[data-local]")!;
    expect(pending).toHaveTextContent("Queued request");
    expect(pending).not.toHaveTextContent("Earlier answer");
    expect(pending.querySelector("[data-pending]")).not.toBeNull();
    await act(async () => { reply.fail(); });
    expect(pending).toBeInTheDocument();
    expect(pending.querySelector("[data-pending]")).not.toBeNull();
    await act(async () => { release(jsonResponse({ queued: { id: "queue-receipt", at: ago(0), text: "Queued request", role: "user", trigger: "chat", position: 1 } })); });
    expect(field).toHaveValue("");
    expect(region.querySelector(".composer-hint[role=alert]")).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/chat")).toHaveLength(2);
  });

  it("an ambiguous server failure restores both drafts and asks for a read before another send", async () => {
    let release!: (response: Response) => void;
    const waiting = new Promise<Response>((resolve) => { release = resolve; });
    const fetchMock = mockFetch({ post: () => waiting });
    const { user } = renderApp({ route: "/projects/altitude" });
    const region = await conversation();
    const field = screen.getByLabelText("Message L3 about altitude");
    await user.type(field, "Original message");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "New thought");
    await act(async () => { release(jsonResponse({ error: "Acceptance could not be confirmed" }, 500)); });
    expect(field).toHaveValue("Original message\nNew thought");
    expect(region.querySelector(".composer-hint[role=alert]")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
    expect(within(region).queryByRole("button", { name: "Retry" })).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/chat")).toHaveLength(1);
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

  it("opens Models on L3 from the message box and saves a choice for this project", async () => {
    const fetchMock = mockFetch();
    const { user } = renderApp({ route: "/projects/altitude" });
    await conversation();
    const button = await screen.findByRole("button", { name: "L3 model: Auto" });
    await user.click(button);
    const dialog = screen.getByRole("dialog", { name: "Models · L3 · altitude only" });
    expect(within(dialog).getByRole("radio", { name: /^Auto/ })).toHaveFocus();
    await user.click(within(dialog).getByRole("radio", { name: /^Deep/ }));
    await user.click(within(dialog).getByRole("radio", { name: "High" }));
    await user.click(within(dialog).getByRole("button", { name: "Use for L3 in altitude" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetchMock, "/api/defaults")).toEqual({ project: "altitude", setting: "l3_choice", value: { engine: "alpha", model: "deep", effort: "high" }, expected: null });
    expect(await screen.findByRole("button", { name: "L3 model: Deep · High" })).toHaveFocus();
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
