import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { hostMicrophone, hostVoiceServer, installVoiceBrowser, speak } from "../components/voiceTest";
import type { TaskView } from "../data/api";

// The xterm screen needs a real canvas; here it stands in with the real screen's frame.
vi.mock("../components/TerminalScreen", () => ({
  default: ({ id }: { id: string }) => <>
    <div className="terminal-frame"><div className="terminal-screen" data-testid="terminal-screen">{id}</div></div>
    <div className="terminal-keys" role="toolbar" aria-label="Terminal keys" />
  </>,
}));

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [] as unknown[],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] as unknown[] },
  quota: { known: false },
  engines: [{ engine: "claude", label: "Claude", known: true }],
  now: new Date().toISOString(),
};

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const running = {
  slug: "fix-timer",
  state: "running",
  title: "Fix the timer",
  attempt: 1,
  session_id: "0123456789abcdef",
  l2_engine: "claude",
  engine_model: "opus",
  dispatched: ago(7),
  updated: ago(1),
  live: { state: "running", context_percent: 34 },
  files: {},
  events: [],
  report_json: null,
  messages: [
    { id: "m-1", at: "2026-08-29T12:01:00Z", role: "operator", text: "Keep the change focused." },
    { id: "m-2", at: "2026-08-29T12:02:00Z", role: "l2", text: "I will use one focused PR." },
    { id: "m-3", at: "2026-08-30T09:00:00Z", role: "l3", text: "Answered from the brief." },
  ],
};

const queued = { ...running, state: "queued", live: null, session_id: "", dispatched: null };

const held = {
  ...running,
  state: "blocked",
  live: null,
  blocked_reason: "usage limit: the window resets at 02:00",
  resume_after: "2026-08-30T02:00",
};

const stuck = {
  ...running,
  state: "blocked",
  live: null,
  blocked_reason: "Should the timer keep the old default?",
  resume_after: null,
};

const decision = {
  id: "q-timer", revision: 1, anchor_id: "m-2", status: "open", audience: "operator",
  project: "altitude",
  slug: "fix-timer",
  kind: "asks",
  asked_by: "l2",
  title: "Fix the timer",
  question: "Should the timer keep the old default?",
  asked: ago(3),
  since: ago(3),
  recommendation: { text: "Keep the default.", label: "Keep it", why: "" },
};

const group = { id: "q-timer", revision: 1, anchor_id: "m-2", questions: [decision] };

const askingL3 = { ...stuck, waiting_on: "l3", blocked_reason: "which suite covers the timer" };

const faulted = {
  ...stuck,
  waiting_on: "l3",
  fault: "sandbox",
  blocked_reason: "The sandbox refused the network socket. Tried twice with the bundled browser.",
};

const done = {
  ...running,
  state: "done",
  live: null,
  prs: [202],
  report_json: { landed: { prs: [{ number: 202, merged: true }], main_runs: [{ conclusion: "success" }] } },
  messages: [],
};

const rejected = { ...done, state: "rejected", prs: [], report_json: null };

const transcript = {
  project: "altitude",
  slug: "fix-timer",
  engine: "claude",
  session_id: "0123456789abcdef",
  attempt: 1, cursor: "fixture:2", lower: "", next: "", more: false, reset: false,
  has_earlier: false, has_engine_records: true, deleted: [],
  events: [
    { id: "boundary", order: "01", version: 1, source: "platform", kind: "boundary", type: "state", at: "2026-08-29T12:00:00", text: "queued → running" },
    { id: "reading", order: "02", version: 2, source: "claude", kind: "message", type: "assistant", role: "assistant", at: null, text: "Reading the timer code" },
  ],
  redaction: "credential-shaped keys and values are redacted",
};

interface StubOptions {
  repository?: string | null;
  overview?: typeof overview;
  message?: () => Response | Promise<Response>;
  action?: () => Response | Promise<Response>;
  task?: () => Response;
  sendNow?: () => Response | Promise<Response>;
}

const terminalRunning = { state: "running", id: "t1", enabled: true, folder: "/fixture/worktree", offset: 0, exit_code: null, reason: null, busy: null };

describe("queued L2 Send now", () => {
  it("keeps the queued message through interruption until canonical delivery and sends its identity once", async () => {
    let release!: (response: Response) => void;
    const message = { id: "steer-now", role: "operator", text: "Check this first", delivery: { state: "queued", at: null, removable: true, send_now: true } };
    let record = { ...running, messages: [message] };
    const fetchMock = stub(record, { task: () => jsonResponse(record), sendNow: () => new Promise((resolve) => { release = resolve; }) });
    const { user } = renderApp({ route: "/projects/altitude/tasks/fix-timer" });
    await user.click(await screen.findByRole("button", { name: "Send now" }));
    const pending = screen.getByRole("button", { name: "Sending now" });
    expect(pending).toHaveAttribute("aria-busy", "true");
    expect(pending).toHaveAttribute("aria-disabled", "true");
    expect(pending).toHaveAccessibleDescription("Joins the current turn without stopping its work.");
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
    expect(screen.getByText("Joins the current turn without stopping its work.")).toHaveClass("sr-only");
    expect(screen.getByText("Queued")).toHaveClass("sr-only");
    record = { ...record, messages: [{ ...message, delivery: { ...message.delivery, state: "delivered", removable: false, send_now: false } }] };
    await act(async () => release(jsonResponse({ ok: true })));
    await waitFor(() => expect(screen.getByText("Check this first").closest(".bubble")).not.toHaveAttribute("data-state"));
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
    expect(screen.getAllByText("Check this first")).toHaveLength(1);
    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/send-now"));
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", id: "steer-now" });
  });

  it("groups queued messages under one Send now, each with its own remove, and drops a removed one", async () => {
    const queued = (id: string, text: string) => ({ id, role: "operator", text, delivery: { state: "queued", at: null, removable: true, send_now: true } });
    let record = { ...running, messages: [queued("first", "First steer"), queued("second", "Second steer"), queued("third", "Third steer")] };
    const fetchMock = stub(record, { task: () => jsonResponse(record) });
    const { user } = renderApp({ route: "/projects/altitude/tasks/fix-timer" });
    const row = (text: string) => screen.getByText(text).closest(".msg-row") as HTMLElement;
    await screen.findByText("Third steer");
    const removalStatus = document.querySelector('p.sr-only[role="status"]');
    expect(removalStatus).toBeEmptyDOMElement();
    expect(["First steer", "Second steer", "Third steer"].map((text) => within(row(text)).getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? button.textContent)))
      .toEqual([["Remove"], ["Remove"], ["Remove", "Send now"]]);
    expect(screen.getAllByText("Queued")).toHaveLength(3);
    record = { ...record, messages: record.messages.map((message) => message.id === "second" ? { ...message, delivery: { ...message.delivery, state: "removed", removable: false, send_now: false } } : message) };
    await user.click(within(row("Second steer")).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByText("Second steer")).toBeNull());
    expect(screen.getByText("Message removed")).toHaveAttribute("role", "status");
    expect(screen.getByText("Message removed")).toBe(removalStatus);
    expect(screen.getAllByRole("button", { name: "Send now" })).toHaveLength(1);
    await user.click(within(row("Third steer")).getByRole("button", { name: "Send now" }));
    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/send-now"));
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", id: "third" });
  });

  it.each([[403, "You do not have permission to send this message now."], [409, "Answer the open question first"], [500, "Send now unconfirmed. Check this message’s status before trying again."]])("refreshes and explains failed delivery (%s)", async (status, expected) => {
    const record = { ...running, messages: [{ id: "steer-now", role: "operator", text: "Keep evidence", delivery: { state: "queued", at: null, removable: true, send_now: true } }] };
    const read = vi.fn(() => jsonResponse(record));
    stub(record, { task: read, sendNow: () => jsonResponse({ error: "Answer the open question first" }, status) });
    const { user } = renderApp({ route: "/projects/altitude/tasks/fix-timer" });
    await user.click(await screen.findByRole("button", { name: "Send now" }));
    await screen.findByText(expected);
    expect(read.mock.calls.length).toBeGreaterThan(1);
    expect(screen.getByText("Keep evidence")).toBeVisible();
  });
});

/** This computer's speech service for the current test: its last words are "spoken detail". */
let voice = hostVoiceServer({ final: "spoken detail" });

/** Start host voice and say half a second, so Stop and Send act on a listening capture. */
async function listen(user: { click: (element: Element) => Promise<void> }) {
  await user.click(screen.getByRole("button", { name: "Start voice input" }));
  await waitFor(() => expect(hostMicrophone.deliver).not.toBeNull());
  speak();
  await screen.findByText("Listening… Stop to add text, or Send.");
}

function stub(task: unknown, options: StubOptions = {}) {
  // The server keeps an accepted message on the task: the refetch after a send returns it.
  const sent: unknown[] = [];
  voice = hostVoiceServer({ final: "spoken detail" });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(options.overview ?? overview);
    if (url.startsWith("/api/voice/live")) return voice.fetch(input, init);
    if (url.includes("/api/task/action")) return options.action ? options.action() : jsonResponse({ ok: true });
    if (url.includes("/api/transcript/")) return jsonResponse(transcript);
    if (url.includes("/api/l2/send-now")) return options.sendNow ? options.sendNow() : jsonResponse({ ok: true });
    if (url.includes("/api/l2/remove")) return jsonResponse({ ok: true });
    if (url.startsWith("/api/terminal/")) return jsonResponse(terminalRunning);
    if (url.includes("/api/task/")) {
      if (options.task) return options.task();
      const record = task as { messages?: unknown[] };
      return jsonResponse({ ...record, repository: options.repository ?? null, messages: [...(record.messages ?? []), ...sent] });
    }
    if (url.includes("/api/l2/message")) {
      if (options.message) return options.message();
      const body = JSON.parse(String(init?.body)) as { text: string; request_id: string };
      const message = { id: body.request_id, at: new Date().toISOString(), role: running.messages[0]?.role, text: body.text };
      sent.push(message);
      return jsonResponse({ ok: true, message });
    }
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const actionCall = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls.find(([u]) => String(u).includes("/api/task/action"));

const route = "/projects/altitude/tasks/fix-timer";

afterEach(() => setViewport(1024));

describe("Task on desktop", () => {
  it("folds a long L3 coordination message to its summary line and opens its original in place", async () => {
    setViewport(1440);
    const text = "Grant recorded for the fictional sandbox run. Keep it through landing.\n\nSee [the guide](https://example.com/guide).";
    stub({ ...running, messages: [...running.messages.slice(0, 2),
      { id: "m-3", at: "2026-08-30T09:00:00Z", role: "l3", text, summary: "Sandbox grant recorded", images: [{ id: "img-1", name: "shot.png", mime_type: "image/png", size: 10, width: 4, height: 4, source_message_id: "m-0" }] }] });
    renderApp({ route });
    const convo = await screen.findByRole("region", { name: "Task conversation" });
    const row = convo.querySelector<HTMLElement>('[data-role="l3"]')!;
    expect(row).toHaveTextContent("L3 · Sandbox grant recorded · 1 imageShow");
    expect(within(row).queryByText(/Grant recorded/)).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Show" }));
    expect(within(row).getByRole("button", { name: "Hide" })).toHaveAttribute("aria-expanded", "true");
    expect(within(row).getByText(/Grant recorded for the fictional sandbox run/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "the guide" })).toHaveAttribute("href", "https://example.com/guide");
    fireEvent.click(within(row).getByRole("button", { name: "Hide" }));
    expect(within(row).queryByLabelText("Message images")).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Show" }));
    expect(within(row).getByLabelText("Message images")).toBeInTheDocument();
  });

  it("links an L2 reply's validation captures in a new tab, worded by how many there are", async () => {
    const capture = (title: string) => ({ name: `${"a".repeat(64)}.gif`, title, bytes: 306_000, width: 390, height: 600, frames: 11, seconds: 9.8 });
    stub({ ...running, messages: [running.messages[1],
      { id: "m-4", at: "2026-08-30T10:00:00Z", role: "l2", text: "One phone capture.", captures: [capture("Phone send")], capture_run: 7 },
      { id: "m-5", at: "2026-08-30T11:00:00Z", role: "l2", text: "Two captures.", captures: [capture("Phone send"), capture("Desktop send")], capture_run: 8 }] });
    renderApp({ route });
    const convo = await screen.findByRole("region", { name: "Task conversation" });
    const one = within(convo).getByRole("link", { name: "Watch capture · Phone send" });
    expect(one).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer/captures/m-4");
    expect(one).toHaveAttribute("target", "_blank");
    expect(one).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(convo).getByRole("link", { name: "Watch 2 captures" })).toHaveAttribute("href", "/projects/altitude/tasks/fix-timer/captures/m-5");
    expect(within(convo).getAllByRole("link", { name: /^Watch / })).toHaveLength(2);
    expect(within(convo.querySelector<HTMLElement>('[data-role="l2"]')!).queryByRole("link")).toBeNull();
  });

  it.each([200, 500])("reconciles by identity before response %i without hiding repeated text or changing recovery", async (status) => {
    let finish!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { finish = resolve; });
    let record = running;
    const fetchMock = stub(running, { message: () => response, task: () => jsonResponse(record) });
    const { user, queryClient } = renderApp({ route });
    const field = await screen.findByRole("textbox", { name: "Message the L2" });
    const text = running.messages[0]!.text;
    await user.type(field, text);
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getAllByText(text, { exact: true })).toHaveLength(2);
    expect(document.querySelectorAll("[data-pending]")).toHaveLength(1);
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    const input = JSON.parse(String(call?.[1]?.body));
    const row = { ...running.messages[0]!, id: input.request_id };
    record = { ...running, messages: [...running.messages, row] };
    await act(() => queryClient.invalidateQueries({ queryKey: ["task", "altitude", "fix-timer"] }));
    await waitFor(() => expect(document.querySelector("[data-pending]")).toBeNull());
    expect(screen.getAllByText(text, { exact: true })).toHaveLength(2);
    await user.type(field, "New draft");
    await act(async () => finish(jsonResponse(status === 200 ? { message: row } : { error: "Response lost" }, status)));
    expect(screen.getAllByText(text, { exact: true })).toHaveLength(2);
    expect(field).toHaveValue(status === 200 ? "New draft" : `${text}\nNew draft`);
    if (status === 500) expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery.");
    else expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("keeps the reported open-PR owner reachable without releasing its merge hold", async () => {
    stub({ ...running, state: "reported", can_continue: true, hold_merge: "Operator review required", prs: [202] });
    const { user } = renderApp({ route });
    const field = await screen.findByRole("textbox", { name: "Message the L2" });
    expect(screen.getByText("Merge held", { exact: true })).toBeInTheDocument();
    await user.type(field, "Resolve the conflicts.");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Resolve the conflicts.", { exact: true });
    expect(field).toHaveValue("");
    expect(screen.getByText("Merge held", { exact: true })).toBeInTheDocument();
  });

  it("keeps a reported task without open-PR ownership evidence read-only", async () => {
    stub({ ...running, state: "reported", can_continue: false });
    renderApp({ route });
    await screen.findByRole("heading", { name: "Fix the timer" });
    expect(screen.queryByRole("textbox", { name: "Message the L2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("keeps a late saved receipt when an older task read completes after navigation", async () => {
    let receipt!: (response: Response) => void;
    let stale!: (response: Response) => void;
    const pendingReceipt = new Promise<Response>((resolve) => { receipt = resolve; });
    const pendingRead = new Promise<Response>((resolve) => { stale = resolve; });
    const fetchMock = stub(running);
    const original = fetchMock.getMockImplementation()!;
    // Hold the read this test starts, whichever of the running task's two-second polls came before it.
    let holdNextRead = false;
    fetchMock.mockImplementation((input, init) => {
      if (String(input).includes("/api/l2/message")) return pendingReceipt;
      if (holdNextRead && String(input).includes("/api/task/altitude/fix-timer")) {
        holdNextRead = false;
        return pendingRead;
      }
      return original(input, init);
    });
    const { user, router, queryClient } = renderApp({ route });
    await screen.findByText("Keep the change focused.");
    await user.type(screen.getByRole("textbox", { name: "Message the L2" }), "Keep the queued sample");
    await user.click(screen.getByRole("button", { name: "Send" }));
    holdNextRead = true;
    void queryClient.invalidateQueries({ queryKey: ["task", "altitude", "fix-timer"] });
    await waitFor(() => expect(holdNextRead).toBe(false));
    await act(() => router.navigate("/projects/altitude"));
    const row = { id: "late-message", role: running.messages[0]!.role, text: "Keep the queued sample", at: ago(0) };
    await act(async () => receipt(jsonResponse({ message: row })));
    await act(async () => stale(jsonResponse(running)));
    expect(queryClient.getQueryData<TaskView>(["task", "altitude", "fix-timer"])?.messages).toContainEqual(row);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/message"))).toHaveLength(1);
  });

  it.each([390, 1440])("keeps independent block and merge reasons discoverable at %i pixels", async (width) => {
    setViewport(width);
    const holdReason = "Wait for the operator to review the complete phone and desktop evidence before merging this pull request.";
    stub({ ...askingL3, hold_merge: holdReason, question: { ...decision, audience: "l3" }, questions: [{ ...decision, audience: "l3" }] });
    const { user } = renderApp({ route });
    const opener = await screen.findByRole("button", { name: /Task details$/ });
    expect(screen.getByText("Waiting for coordinator")).toBeInTheDocument();
    expect(screen.queryByText(holdReason)).toBeNull();
    expect(screen.queryByText(askingL3.blocked_reason)).toBeNull();
    const field = screen.getByRole("textbox", { name: "Message the L2" });
    await user.type(field, "Preserve this draft.");
    await user.click(opener);
    const details = screen.getByRole("dialog", { name: "Task details" });
    expect(within(details).getByText(holdReason)).toBeInTheDocument();
    expect(within(details).getByText(askingL3.blocked_reason)).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "View question" })).toHaveAttribute("href", `${route}?question=q-timer&revision=1`);
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Task details" })).toBeNull();
    expect(opener).toHaveFocus();
    expect(field).toHaveValue("Preserve this draft.");
  });

  it("shows the header rows, the conversation, and the live session panel for a running task", async () => {
    setViewport(1440);
    const fetchMock = stub(running);
    renderApp({ route });

    expect(screen.getByLabelText("Loading")).toBeInTheDocument();
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByRole("button", { name: "Back" })).toHaveTextContent("‹ altitude");
    expect(screen.getByText("L2 working")).toBeInTheDocument();
    expect(screen.getByText("Opus 5 on Claude")).toBeInTheDocument();
    expect(screen.queryByText("attempt 1 · started 7 min ago · 34% of its context used")).toBeNull();

    const convo = screen.getByRole("region", { name: "Task conversation" });
    const mine = convo.querySelector("[data-mine]");
    expect(mine).toHaveTextContent("Keep the change focused.");
    expect(mine?.querySelector(".bubble")).toBeInTheDocument();
    const reply = convo.querySelector('[data-role="l2"]');
    expect(reply).toHaveTextContent("I will use one focused PR.");
    expect(reply?.querySelector(".bubble")).toBeNull();
    const coordination = convo.querySelector<HTMLElement>('[data-role="l3"]')!;
    expect(coordination).toHaveTextContent("L3 messaged the L2Show");
    expect(within(coordination).queryByText("Answered from the brief.")).toBeNull();
    fireEvent.click(within(coordination).getByRole("button", { name: "Show" }));
    expect(within(coordination).getByText("Answered from the brief.")).toBeInTheDocument();
    fireEvent.click(within(coordination).getByRole("button", { name: "Hide" }));
    expect(within(coordination).queryByText("Answered from the brief.")).toBeNull();
    const days = within(convo).getAllByRole("separator").map((n) => n.textContent);
    expect(days).toHaveLength(2);
    expect(days[0]).toMatch(/Aug 29$/);
    expect(days[1]).toMatch(/Aug 30$/);
    expect(screen.getByLabelText("Message the L2")).toHaveAttribute("placeholder", "Message the L2");
    expect(screen.getByText("Reaches the L2 at its next checkpoint.")).toBeInTheDocument();

    const panel = screen.getByRole("region", { name: "Live session" });
    expect(await within(panel).findByText("Reading the timer code")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Raw events" })).toHaveAttribute("aria-pressed", "false");
    expect(within(panel).getByText("Following live · new steps appear at the bottom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Live session" })).toHaveAttribute("aria-pressed", "true");
    expect(String(fetchMock.mock.calls.find(([u]) => String(u).includes("/api/transcript/"))?.[0])).toContain(
      "/api/transcript/altitude/fix-timer?engine=claude&session_id=0123456789abcdef&attempt=1&raw=0",
    );
    expect(screen.getAllByRole("button", { name: "Stop" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("opens the live session as an overlay below the inline width and closes it again", async () => {
    stub(running);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.queryByRole("region", { name: "Live session" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Live session" }));
    const dialog = screen.getByRole("dialog", { name: "Live session" });
    expect(await within(dialog).findByText("Reading the timer code")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Live session" })).toBeNull();
  });

  it("opens the panel when the route ends in /live", async () => {
    stub(running);
    renderApp({ route: `${route}/live` });
    expect(await screen.findByRole("dialog", { name: "Live session" })).toBeInTheDocument();
  });

  it("keeps Stop reachable in a narrow desktop live overlay without a session transcript", async () => {
    setViewport(1024);
    const fetchMock = stub({ ...running, session_id: "" });
    const { user } = renderApp({ route: `${route}/live` });
    const dialog = await screen.findByRole("dialog", { name: "Live session" });
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", action: "stop", generation: null });
    expect(within(dialog).getByRole("button", { name: "Stopping…" })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/transcript/"))).toBe(false);
  });

  it("requests Stop once without confirmation and keeps the draft editable until evidence arrives", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const fetchMock = stub(running);
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Keep this draft");
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", action: "stop", generation: null });
    expect(screen.queryByRole("group", { name: "Stop this task?" })).toBeNull();
    expect(screen.getByText("Stopping…")).toBeInTheDocument();
    expect(field).toHaveValue("Keep this draft");
    expect(field).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(field, " and edit it");
    expect(field).toHaveValue("Keep this draft and edit it");
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("rejects with an optional reason", async () => {
    const fetchMock = stub(queued);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    let group = screen.getByRole("group", { name: "Reject this task?" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Reject task"]);
    expect(within(group).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Reject this task?" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    group = screen.getByRole("group", { name: "Reject this task?" });
    expect(group).toHaveTextContent("Reject this task? Its worker ends and the task is archived.");
    await user.type(within(group).getByLabelText("Reason (optional)"), "waiting on the API");
    await user.click(within(group).getByRole("button", { name: "Reject task" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body))).toEqual({
      project: "altitude",
      slug: "fix-timer",
      action: "reject",
      reason: "waiting on the API",
    });
  });

  it("shows an unconfirmed Stop with status recheck when its request fails", async () => {
    stub(running, { action: () => jsonResponse({ error: "altd is restarting" }, 503) });
    const { user } = renderApp({ route });
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The worker may still be running.");
    expect(screen.getByRole("button", { name: "Check status" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("says what a queued task waits for where the session would be", async () => {
    const fetchMock = stub(queued, {
      overview: { ...overview, wip: { ...overview.wip, waiting: [{ project: "altitude", slug: "fix-timer", why: "dispatch" }] } },
    });
    setViewport(1440);
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Queued")).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "Live session" });
    expect(await within(panel).findByText("Waits for dispatch")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Raw events" })).toBeNull();
    expect(screen.getByLabelText("Message the L2")).toBeEnabled();
    expect(screen.getByText("Delivered when Altitude starts the L2.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/transcript/"))).toBe(false);
  });

  it.each([390, 1440])("keeps planned messages and drafts through release and launch at %ipx", async (width) => {
    setViewport(width);
    const task = { ...queued, messages: [], planned_wait: { reason: "the index migration to land", after: null } as { reason: string; after: null } | null };
    const fetchMock = stub(task);
    const { user, queryClient } = renderApp({ route });
    await screen.findByText("Planned", { exact: true });
    const conversation = screen.getByRole("region", { name: "Task conversation" });
    expect(screen.getAllByText("Waiting for the index migration to land.")[0]).toBeInTheDocument();
    expect(within(conversation).getByText("No messages yet.")).toBeInTheDocument();
    const field = screen.getByRole("textbox", { name: "Message the L2" });
    await user.type(field, "Keep pagination compatible.");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(field).toHaveValue(""));
    expect(within(conversation).getByText("Keep pagination compatible.")).toBeInTheDocument();
    expect(screen.getByText("Planned", { exact: true })).toBeInTheDocument();
    expect(actionCall(fetchMock)).toBeUndefined();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/transcript/"))).toBe(false);
    await user.type(field, "Preserve this draft");

    task.planned_wait = null;
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["task", "altitude", task.slug] }); });
    await screen.findByText("Queued", { exact: true });
    expect(screen.queryByText("Waiting for the index migration to land.")).toBeNull();
    expect(field).toBeEnabled();
    expect(field).toHaveValue("Preserve this draft");
    expect(within(conversation).getByText("Keep pagination compatible.")).toBeInTheDocument();

    task.state = "running";
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["task", "altitude", task.slug] }); });
    await screen.findByText("L2 working", { exact: true });
    expect(field).toHaveValue("Preserve this draft");
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByText("Planned", { exact: true })).toBeNull();
  });

  it("reads a held task as queued, waiting for resume", async () => {
    setViewport(1440);
    stub(held);
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.queryByText("Blocked")).toBeNull();
    const panel = screen.getByRole("region", { name: "Live session" });
    expect(within(panel).getByText("Waits for resume")).toBeInTheDocument();
    expect(screen.getByText("Delivered when Altitude resumes the L2.")).toBeInTheDocument();
  });

  it("puts the open question at the end of the conversation as the operator's turn", async () => {
    stub({ ...stuck, question: decision, questions: [decision], question_group: group }, { overview: { ...overview, queue: [decision] } });
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const convo = screen.getByRole("region", { name: "Task conversation" });
    const card = convo.querySelector("[data-question-id=\"q-timer\"]")!;
    expect(card).toHaveTextContent("Should the timer keep the old default?");
    expect(card.closest(".convo-col")).toBeInTheDocument();
    const turn = card.closest(".conversation-question")!;
    expect(turn).toHaveAttribute("data-turn", "operator");
    expect(turn.querySelector(".conversation-turn")).toHaveTextContent("Your turn · 1 question");
    // Anchored at m-2, shown after the last message.
    expect(turn.previousElementSibling).toHaveTextContent("L3 messaged the L2");
    expect(convo.querySelectorAll(".conversation-question")).toHaveLength(1);
    expect(screen.getAllByText("Your turn · 1 question")).toHaveLength(2);
    expect(document.querySelector(".task-explanation")).toHaveTextContent("Waiting for your answer to the task’s question.");
    expect(screen.getByLabelText("Message the L2")).toBeInTheDocument();
    expect(screen.getByText("Replying hands the turn back to the L2.")).toBeInTheDocument();
  });

  it("labels a question the L2 asks again after a hand-back", async () => {
    const again = { ...decision, asked_again: true };
    stub({ ...stuck, question: again, questions: [again], question_group: { ...group, questions: [again] } }, { overview: { ...overview, queue: [again] } });
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const convo = screen.getByRole("region", { name: "Task conversation" });
    expect(convo.querySelector(".conversation-turn")).toHaveTextContent("Your turn · 1 question · asked again");
    expect(within(convo).getByRole("button", { name: "Keep it" })).toBeEnabled();
    expect(screen.getByText("Your turn · 1 question")).toBeInTheDocument();
  });

  it.each([["running", "Sent · the L2 has your reply."], ["queued", "Sent · waiting for the L2 to start."]])(
    "turns a handed-back question into a quiet line while the task is %s", async (state, line) => {
      // The reply came after the question was asked; the question record stays open.
      stub({ ...stuck, state, handed_back: new Date().toISOString(), question: decision, questions: [decision], question_group: group });
      renderApp({ route });

      await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
      const convo = screen.getByRole("region", { name: "Task conversation" });
      expect(within(convo).getByText(line)).toHaveAttribute("role", "status");
      expect(convo.querySelector(".conversation-question")).toHaveAttribute("data-turn", "l2");
      expect(convo.querySelector("[data-question-id]")).toBeNull();
      expect(within(convo).queryByRole("button", { name: "Keep it" })).toBeNull();
      expect(screen.queryByText(/Your turn/)).toBeNull();
      expect(screen.queryByText("Replying hands the turn back to the L2.")).toBeNull();
      if (state === "running") expect(screen.getByText("L2 replying to you")).toBeInTheDocument();
    });

  describe("review before merge", () => {
    const hold = "Wait for the operator to review the phone evidence.";
    const review = { project: "altitude", slug: "fix-timer", title: "Fix the timer", kind: "review", pr: 204, asked_by: "l2",
      question: "Review PR #204 before merge", detail: hold, asked: ago(2), since: ago(2) };
    const reported = { ...stuck, state: "reported", blocked_reason: null, hold_merge: hold, delivery: { number: 204 } };
    const approved = () => jsonResponse({ message: { id: "approval", at: new Date().toISOString(), role: running.messages[0]?.role, text: "Approved: merge PR #204." } });
    const reviewTurn = () => within(screen.getByRole("region", { name: "Task conversation" })).getByText("Your turn · review before merge").closest(".conversation-question")! as HTMLElement;

    it("approves the PR with the operator's own message and confirms the receipt", async () => {
      const fetchMock = stub(reported, { repository: "https://github.com/example/altitude", overview: { ...overview, queue: [review] }, message: approved });
      const { user } = renderApp({ route });
      await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
      const turn = await waitFor(reviewTurn);
      expect(turn).toHaveTextContent("Review PR #204 before merge");
      expect(turn).toHaveTextContent(hold);
      expect(within(turn).getByRole("link", { name: "View PR #204" })).toHaveAttribute("href", "https://github.com/example/altitude/pull/204");
      expect(screen.getByText("Your turn · review PR #204")).toBeInTheDocument();
      await user.click(within(turn).getByRole("button", { name: "Approve merge" }));
      await within(turn).findByText("Approval sent · the L2 merges after a final check of the same PR.");
      expect(within(turn).queryByRole("button", { name: "Approve merge" })).toBeNull();
      const sent = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/message"));
      expect(sent).toHaveLength(1);
      expect(JSON.parse(String(sent[0]![1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", text: "Approved: merge PR #204." });
    });

    it("keeps the review open with Not sent. Retry when the send fails", async () => {
      let fail = true;
      const fetchMock = stub(reported, { overview: { ...overview, queue: [review] },
        message: () => fail ? jsonResponse({ error: "unavailable" }, 500) : approved() });
      const { user } = renderApp({ route });
      await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
      const turn = await waitFor(reviewTurn);
      expect(within(turn).queryByRole("link", { name: /View PR/ })).toBeNull();
      await user.click(within(turn).getByRole("button", { name: "Approve merge" }));
      expect(await within(turn).findByRole("alert")).toHaveTextContent("Not sent. Retry");
      expect(within(turn).getByRole("button", { name: "Approve merge" })).toBeEnabled();
      fail = false;
      await user.click(within(turn).getByRole("button", { name: "Retry" }));
      await within(turn).findByText("Approval sent · the L2 merges after a final check of the same PR.");
      expect(within(turn).queryByRole("alert")).toBeNull();
      const sent = fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/message"));
      expect(sent).toHaveLength(2);
      // Retry resends the same message.
      for (const call of sent) expect(JSON.parse(String(call[1]?.body)).text).toBe("Approved: merge PR #204.");
    });

    it("says the operator cannot approve here when access is denied", async () => {
      stub(reported, { overview: { ...overview, queue: [review] }, message: () => jsonResponse({ error: "Access denied" }, 403) });
      const { user } = renderApp({ route });
      await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
      const turn = await waitFor(reviewTurn);
      await user.click(within(turn).getByRole("button", { name: "Approve merge" }));
      expect(await within(turn).findByRole("alert")).toHaveTextContent("You cannot approve here.");
      expect(within(turn).queryByRole("button", { name: "Retry" })).toBeNull();
      expect(within(turn).getByRole("button", { name: "Approve merge" })).toBeDisabled();
    });
  });

  it("discloses the full L3 block reason without a permanent paragraph", async () => {
    stub(askingL3);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Waiting for coordinator")).toBeInTheDocument();
    expect(screen.queryByText("which suite covers the timer")).toBeNull();
    expect(document.querySelector(".task-explanation")).toHaveTextContent("Waiting for the coordinator: which suite covers the timer.");
    await user.click(screen.getByRole("button", { name: /Task details$/ }));
    expect(within(screen.getByRole("dialog", { name: "Task details" })).getByText("which suite covers the timer")).toBeInTheDocument();
  });

  it("explains a fault in red without inferring its cause from diagnostic prose", async () => {
    stub(faulted);
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const line = document.querySelector(".task-line");
    expect(line).toHaveTextContent("A system problem paused work. Waiting for the coordinator to check the blocker.");
    expect(line).not.toHaveTextContent("Tried twice");
    expect(line).toHaveClass("text-danger");
  });

  it("keeps an unpunctuated fault concise and discloses its complete reason", async () => {
    const reason = `The verification browser could not start ${"while checking the configured environment ".repeat(12)}`.trim();
    stub({ ...faulted, blocked_reason: reason });
    const { user } = renderApp({ route });
    const opener = await screen.findByRole("button", { name: /Task details$/ });
    const notice = document.querySelector(".task-fault");
    expect(notice).toHaveTextContent("A system problem paused work.");
    expect(notice).not.toHaveTextContent(reason);
    expect(notice!.textContent!.length).toBeLessThanOrEqual(120);
    await user.click(opener);
    expect(within(screen.getByRole("dialog", { name: "Task details" })).getByText(reason)).toBeInTheDocument();
  });

  it.each([390, 1440])("links the PR in a new tab at %i pixels when the repository is known", async (width) => {
    setViewport(width);
    const fetchMock = stub(done, { repository: "https://github.com/example/project" });
    const { user } = renderApp({ route });

    if (width === 390) await user.click(await screen.findByRole("button", { name: /Task details$/ }));
    const link = await screen.findByRole("link", { name: "PR #202 merged · main checks passed" });
    expect(link).toHaveAttribute("href", "https://github.com/example/project/pull/202");
    // The task view carries the link; the whole project view is not fetched for it.
    expect(fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("/api/project/"))).toEqual([]);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([390, 1440])("keeps the PR as text at %i pixels when the repository is null", async (width) => {
    setViewport(width);
    stub(done, { repository: null });
    const { user } = renderApp({ route });

    if (width === 390) await user.click(await screen.findByRole("button", { name: /Task details$/ }));
    expect(await screen.findByText("PR #202 merged · main checks passed", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /PR #202/ })).toBeNull();
  });

  it("reads a done task read-only with its PR in the header", async () => {
    setViewport(1440);
    stub(done);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("PR #202 merged · main checks passed")).toHaveAttribute("data-tone", "ok");
    expect(screen.queryByLabelText("Message the L2")).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.getByText("No messages on this task.")).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "Live session" });
    expect(await within(panel).findByText("Session ended")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Task details$/ }));
    expect(within(screen.getByRole("dialog", { name: "Task details" })).getByText("attempt 1 · done 1 min ago")).toBeInTheDocument();
  });

  it("reads a rejected task the same way", async () => {
    stub(rejected);
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.queryByLabelText("Message the L2")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("shows the bubble at once with a sending cue and settles the same bubble in place once the server accepts it", async () => {
    let accept!: (response: Response) => void;
    const held = new Promise<Response>((resolve) => { accept = resolve; });
    let record = running;
    const fetchMock = stub(running, { message: () => held, task: () => jsonResponse(record) });
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    await user.type(screen.getByLabelText("Message the L2"), "prefer the smaller diff");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true));
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    const input = JSON.parse(String(call?.[1]?.body));
    expect(input).toEqual({ project: "altitude", slug: "fix-timer", text: "prefer the smaller diff", request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    const row = screen.getByText("prefer the smaller diff").closest<HTMLElement>(".msg-row")!;
    expect(row).toHaveAttribute("data-pending");
    expect(within(row).getByRole("status", { name: "Sending" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message the L2")).toHaveValue("");

    const message = { id: input.request_id, at: new Date().toISOString(), role: running.messages[0]!.role, text: "prefer the smaller diff", delivery: { state: "queued", at: null, removable: true } };
    record = { ...running, messages: [...running.messages, message] };
    await act(async () => accept(jsonResponse({ ok: true, message })));
    await waitFor(() => expect(row).not.toHaveAttribute("data-pending"));
    // The stored row settles the pending bubble in place: the same node, one copy, its receipt swapped in.
    expect(screen.getByText("prefer the smaller diff").closest<HTMLElement>(".msg-row")).toBe(row);
    expect(screen.getAllByText("prefer the smaller diff")).toHaveLength(1);
    expect(within(row).queryByRole("status", { name: "Sending" })).toBeNull();
    expect(within(row).getByText("Queued")).toHaveClass("sr-only");
    expect(within(row).getByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("returns the draft with Not sent. Retry when the server refuses the message", async () => {
    let refuse = true;
    const fetchMock = stub(running, {
      message: () =>
        refuse
          ? jsonResponse({ error: "no active session" }, 409)
          : jsonResponse({ ok: true, message: { id: "m-9", at: new Date().toISOString(), role: "l2", text: "" } }),
    });
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    await user.type(screen.getByLabelText("Message the L2"), "prefer the smaller diff");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not sent. Retry");
    expect(screen.getByLabelText("Message the L2")).toHaveValue("prefer the smaller diff");
    expect(document.querySelector("[data-pending]")).toBeNull();

    refuse = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/l2/message"))).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("says when the attempt has no session file", async () => {
    setViewport(1440);
    const fetchMock = stub({ ...running, session_id: "" });
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const panel = screen.getByRole("region", { name: "Live session" });
    expect(within(panel).getByText("No session file for this attempt")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/transcript/"))).toBe(false);
  });

  it("offers Retry when the task cannot be loaded", async () => {
    let fail = true;
    stub(running, { task: () => (fail ? jsonResponse({ error: "boom" }, 500) : jsonResponse(running)) });
    const { user } = renderApp({ route });

    expect(await screen.findByText(/Could not load the task/)).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Fix the timer" })).toBeInTheDocument();
  });

  it("lands the transcript in the draft and sends it through the same message path", async () => {
    installVoiceBrowser();
    const fetchMock = stub(running);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const field = screen.getByLabelText("Message the L2");
    await user.type(field, "Typed context.");
    await listen(user);
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Typed context. spoken detail"));
    // Landed: the draft is the only place the words appear (issue #195).
    expect(screen.queryByRole("region", { name: /transcript/i })).toBeNull();
    expect(screen.queryByText("spoken detail")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true));
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", text: "Typed context. spoken detail", request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("voice Send retains its task question context when returning to a newer question before transcription finishes", async () => {
    installVoiceBrowser();
    const task = { ...stuck, question: { ...decision } };
    const originalFetch = stub(task);
    const { user, router } = renderApp({ route: `${route}?question=q-timer&revision=1` });
    await user.type(await screen.findByLabelText("Message the L2"), "Original question reply");
    await listen(user);
    voice.connection = "hold";
    await user.click(screen.getByRole("button", { name: "Send" }));
    await act(() => router.navigate("/monitor"));
    task.question = { ...decision, id: "q-other", revision: 2, question: "A newer unrelated question?" };
    await act(() => router.navigate(`${route}?question=q-other&revision=2`));
    await screen.findByText("A newer unrelated question?");
    expect(screen.getByLabelText("Message the L2")).toHaveValue("Original question reply");
    await act(async () => voice.reconnect());
    await waitFor(() => expect(originalFetch.mock.calls.some(([url]) => String(url).includes("/api/l2/message"))).toBe(true));
    const request = originalFetch.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ project: "altitude", slug: "fix-timer", text: "Original question reply spoken detail", question_id: "q-timer", revision: 1 });
  });

  it("resets the composer when navigating between cached tasks", async () => {
    installVoiceBrowser();
    const other = { ...running, slug: "other-task", title: "Other task", messages: [] };
    voice = hostVoiceServer({ final: "fix-timer only" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.startsWith("/api/voice/live")) return voice.fetch(input, init);
        if (url.includes("/api/task/altitude/other-task")) return jsonResponse(other);
        if (url.includes("/api/task/altitude/fix-timer")) return jsonResponse(running);
        return jsonResponse({ error: "not found" }, 404);
      }),
    );

    const { router, user } = renderApp({ route });
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    await router.navigate("/projects/altitude/tasks/other-task");
    await screen.findByRole("heading", { level: 1, name: "Other task" });
    await router.navigate(route);
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });

    const field = screen.getByLabelText("Message the L2");
    await user.type(field, "Fix-only draft");
    await listen(user);
    await user.click(screen.getByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Fix-only draft fix-timer only"));

    await router.navigate("/projects/altitude/tasks/other-task");
    await screen.findByRole("heading", { level: 1, name: "Other task" });
    expect(screen.getByLabelText("Message the L2")).toHaveValue("");
  });
});

describe("Phone swipe lifecycle", () => {
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", x: number, on = ".convo-scroll") => {
    const scroller = document.querySelector(on)!;
    const point = [{ clientX: x, clientY: 300 }];
    fireEvent[type](scroller, type === "touchEnd" ? { touches: [], changedTouches: point } : { touches: point, cancelable: true });
  };
  const track = () => document.querySelector<HTMLElement>(".task-track")!;
  const live = () => screen.queryByRole("region", { name: "Live session" });
  const transcriptReads = (fetchMock: ReturnType<typeof stub>) => fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/transcript/")).length;
  let width: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    setViewport(390);
    width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(390);
  });
  afterEach(() => width.mockRestore());

  it("reveals Live session with its transcript while the finger drags, and a short slow release springs back", async () => {
    const fetchMock = stub(running);
    const { router } = renderApp({ route });
    await screen.findByRole("region", { name: "Task conversation" });
    expect(live()).toBeNull();
    expect(transcriptReads(fetchMock)).toBe(0);
    touch("touchStart", 200);
    touch("touchMove", 150);
    expect(await screen.findByRole("region", { name: "Live session" })).toBeVisible();
    expect(track().style.transform).toBe("translateX(-50px)");
    await waitFor(() => expect(transcriptReads(fetchMock)).toBe(1));
    expect(screen.getByRole("region", { name: "Task conversation" })).toBeVisible();
    await new Promise((resolve) => setTimeout(resolve, 150));
    touch("touchEnd", 150);
    expect(track().style.transform).toBe("translateX(0px)");
    await waitFor(() => expect(live()).toBeNull());
    expect(track().style.transform).toBe("");
    expect(router.state.location.pathname).toBe(route);
  });

  it("completes the switch after the settle when the drag passes half the width", async () => {
    stub(running);
    const { router } = renderApp({ route });
    await screen.findByRole("region", { name: "Task conversation" });
    touch("touchStart", 300);
    touch("touchMove", 100);
    expect(track().style.transform).toBe("translateX(-200px)");
    touch("touchEnd", 100);
    expect(track().style.transform).toBe("translateX(-390px)");
    expect(router.state.location.pathname).toBe(route);
    await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/live`));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Task conversation" })).toBeNull());
    expect(track().style.marginLeft).toBe("-100%");
    expect(track().style.transform).toBe("");
    expect(live()).toBeVisible();
    expect(screen.getByLabelText("Message the L2")).not.toBeVisible();
  });

  it("completes a short quick flick from its release speed", async () => {
    let now = 0;
    const clock = vi.spyOn(Event.prototype, "timeStamp", "get").mockImplementation(() => now);
    try {
      stub(running);
      const { router } = renderApp({ route });
      await screen.findByRole("region", { name: "Task conversation" });
      touch("touchStart", 300);
      now = 20;
      touch("touchMove", 260);
      now = 40;
      touch("touchEnd", 220);
      expect(track().style.transform).toBe("translateX(-390px)");
      await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/live`));
    } finally { clock.mockRestore(); }
  });

  it("springs back when a second finger lands during the drag", async () => {
    stub(running);
    const { router } = renderApp({ route });
    await screen.findByRole("region", { name: "Task conversation" });
    touch("touchStart", 300);
    touch("touchMove", 100);
    expect(live()).toBeVisible();
    fireEvent.touchStart(document.querySelector(".convo-scroll")!, { touches: [{ clientX: 100, clientY: 300 }, { clientX: 200, clientY: 500 }] });
    expect(track().style.transform).toBe("translateX(0px)");
    touch("touchMove", 60);
    expect(track().style.transform).toBe("translateX(0px)");
    touch("touchEnd", 60);
    await waitFor(() => expect(live()).toBeNull());
    expect(track().style.transform).toBe("");
    expect(router.state.location.pathname).toBe(route);
  });

  it("gives resistance past the end and never switches", async () => {
    stub(running);
    const { router } = renderApp({ route });
    await screen.findByRole("region", { name: "Task conversation" });
    touch("touchStart", 100);
    touch("touchMove", 400);
    const offset = parseFloat(track().style.transform.replace(/[^\d.-]/g, ""));
    expect(offset).toBeGreaterThan(0);
    expect(offset).toBeLessThan(100);
    touch("touchEnd", 400);
    await waitFor(() => expect(track().style.transform).toBe(""));
    expect(router.state.location.pathname).toBe(route);
    expect(live()).toBeNull();
  });

  it("switches at once on release under reduced motion, with nothing revealed mid-drag", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
    stub(running);
    const { router } = renderApp({ route });
    await screen.findByRole("region", { name: "Task conversation" });
    touch("touchStart", 300);
    touch("touchMove", 100);
    expect(track().style.transform).toBe("");
    expect(live()).toBeNull();
    touch("touchEnd", 100);
    await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/live`));
    expect(track().style.transform).toBe("");
    expect(live()).toBeVisible();
  });

  describe("with a terminal", () => {
    const withTerminal = { ...running, worktree: "/fixture/worktree" };
    const terminalReads = (fetchMock: ReturnType<typeof stub>) => fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/terminal/")).length;
    const terminal = () => screen.queryByRole("region", { name: "Terminal" });
    const swipeOn = async (on: string, from: number, to: number) => {
      touch("touchStart", from, on);
      touch("touchMove", to, on);
      touch("touchEnd", to, on);
    };

    it("swipes left from Live session to Terminal, opening it only once the switch completes, and right back", async () => {
      const fetchMock = stub(withTerminal);
      const { router } = renderApp({ route: `${route}/live` });
      await screen.findByRole("region", { name: "Live session" });
      touch("touchStart", 300, ".live-body");
      touch("touchMove", 100, ".live-body");
      expect(track().style.transform).toBe("translateX(-200px)");
      expect(within(terminal()!).getByRole("status", { name: "Starting the terminal" })).toBeVisible();
      expect(terminalReads(fetchMock)).toBe(0);
      touch("touchEnd", 100, ".live-body");
      expect(track().style.transform).toBe("translateX(-390px)");
      await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/terminal`));
      expect(await screen.findByTestId("terminal-screen")).toBeVisible();
      expect(track().style.marginLeft).toBe("-200%");
      expect(track().style.transform).toBe("");
      expect(live()).toBeNull();
      const tabs = screen.getByRole("navigation", { name: "Task views" });
      expect(within(tabs).getByRole("link", { name: "Terminal" })).toHaveAttribute("aria-current", "page");
      expect(within(tabs).getByRole("button", { name: "Close terminal" })).toBeVisible();
      expect(screen.getAllByRole("navigation", { name: "Task views" })).toHaveLength(1);
      await swipeOn(".terminal-note", 100, 300);
      await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/live`));
      await waitFor(() => expect(screen.queryByTestId("terminal-screen")).toBeNull());
      expect(live()).toBeVisible();
      expect(within(screen.getByRole("navigation", { name: "Task views" })).queryByRole("button", { name: "Close terminal" })).toBeNull();
    });

    it("leaves a swipe on the terminal screen or its key row to the terminal and resists past the last view", async () => {
      stub(withTerminal);
      const { router } = renderApp({ route: `${route}/terminal` });
      await screen.findByTestId("terminal-screen");
      await swipeOn("[data-testid=terminal-screen]", 100, 300);
      expect(track().style.transform).toBe("");
      await swipeOn(".terminal-keys", 100, 300);
      expect(track().style.transform).toBe("");
      touch("touchStart", 300, ".terminal-note");
      touch("touchMove", 100, ".terminal-note");
      const offset = parseFloat(track().style.transform.replace(/[^\d.-]/g, ""));
      expect(offset).toBeLessThan(0);
      expect(offset).toBeGreaterThan(-100);
      touch("touchEnd", 100, ".terminal-note");
      await waitFor(() => expect(track().style.transform).toBe(""));
      expect(router.state.location.pathname).toBe(`${route}/terminal`);
      expect(screen.getByTestId("terminal-screen")).toBeVisible();
    });

    it("moves one view per swipe from Conversation, never skipping Live session", async () => {
      stub(withTerminal);
      const { router } = renderApp({ route });
      await screen.findByRole("region", { name: "Task conversation" });
      await swipeOn(".convo-scroll", 300, 100);
      await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/live`));
      await swipeOn(".live-body", 300, 100);
      await waitFor(() => expect(router.state.location.pathname).toBe(`${route}/terminal`));
      expect(await screen.findByTestId("terminal-screen")).toBeVisible();
    });

    it("keeps two views when the task has no terminal: Live session is the last", async () => {
      stub(running);
      const { router } = renderApp({ route: `${route}/live` });
      await screen.findByRole("region", { name: "Live session" });
      expect(terminal()).toBeNull();
      touch("touchStart", 300, ".live-body");
      touch("touchMove", 100, ".live-body");
      expect(parseFloat(track().style.transform.replace(/[^\d.-]/g, ""))).toBeGreaterThan(-100);
      touch("touchEnd", 100, ".live-body");
      await waitFor(() => expect(track().style.transform).toBe(""));
      expect(router.state.location.pathname).toBe(`${route}/live`);
    });
  });
});

describe("Task on the phone", () => {
  it("keeps repeated view switches in one history entry and preserves the originating tab", async () => {
    setViewport(390);
    stub(running);
    const { user, router } = renderApp({ route: "/projects/altitude?tab=work" });
    await act(() => router.navigate(route, { state: { tab: "needs" } }));
    await screen.findByRole("navigation", { name: "Task views" });
    for (const name of ["Live session", "Conversation", "Live session", "Conversation", "Live session"]) {
      await user.click(within(screen.getByRole("navigation", { name: "Task views" })).getByRole("link", { name }));
    }
    await act(() => router.navigate(-1));
    expect(router.state.location.pathname + router.state.location.search).toBe("/projects/altitude?tab=work");
    await act(() => router.navigate(1));
    expect(router.state.location.pathname).toBe(`${route}/live`);
    expect(router.state.location.state).toEqual({ tab: "needs" });
    expect(await screen.findByRole("region", { name: "Live session" })).toBeInTheDocument();
  });

  it("shows the state line, the two tabs, and the composer above the tab bar", async () => {
    setViewport(390);
    stub(running);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(document.querySelector(".task-state-line")).toHaveTextContent("L2 working");
    expect(screen.queryByText("Opus on Claude")).toBeNull();
    const tabs = screen.getByRole("navigation", { name: "Task views" });
    expect(within(tabs).getByRole("link", { name: "Conversation" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Live session" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("region", { name: "Task conversation" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message the L2")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Live session" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Stop" }).length).toBeGreaterThan(0);

    await user.click(within(tabs).getByRole("link", { name: "Live session" }));
    expect(within(tabs).getByRole("link", { name: "Live session" })).toHaveAttribute("aria-current", "page");
    const panel = await screen.findByRole("region", { name: "Live session" });
    expect(await within(panel).findByText("Reading the timer code")).toBeInTheDocument();
    expect(screen.getByLabelText("Message the L2")).not.toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps a single Stop in the phone header across both views", async () => {
    setViewport(390);
    const fetchMock = stub(running);
    const { user } = renderApp({ route });
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const stop = screen.getByRole("button", { name: "Stop" });
    expect(stop.closest("header")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Stop" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "View live session" })).toBeNull();
    await user.click(screen.getByRole("link", { name: "Live session" }));
    expect(screen.getByRole("button", { name: "Stop" })).toBe(stop);
    await user.click(stop);
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
  });

  it.each([409, 500, 200])("keeps send outcome %i and newer typing across a Live session switch", async (status) => {
    setViewport(390);
    let finish!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { finish = resolve; });
    let record = running;
    const fetchMock = stub(running, { message: () => response, task: () => jsonResponse(record) });
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Original message");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "New thought");
    await user.click(screen.getByRole("link", { name: "Live session" }));
    expect(screen.getByLabelText("Message the L2")).not.toBeVisible();
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    const message = { id: JSON.parse(String(call?.[1]?.body)).request_id, at: new Date().toISOString(), role: running.messages[0]!.role, text: "Original message" };
    if (status === 200) record = { ...running, messages: [...running.messages, message] };
    await act(async () => { finish(jsonResponse(status === 200 ? { ok: true, message } : { error: "Send failed" }, status)); });
    await user.click(screen.getByRole("link", { name: "Conversation" }));
    expect(screen.getByLabelText("Message the L2")).toHaveValue(status === 200 ? "New thought" : "Original message\nNew thought");
    if (status === 409) expect(screen.getByRole("alert")).toHaveTextContent("Not sent. Retry");
    else if (status === 500) {
      expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    } else {
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getAllByText("Original message")).toHaveLength(1);
    }
  });

  it.each([true, false])("keeps mixed send failures unconfirmed across views, refusal first: %s", async (refusalFirst) => {
    setViewport(390);
    const finishes: Array<(response: Response) => void> = [];
    stub(running, { message: () => new Promise<Response>((resolve) => finishes.push(resolve)) });
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    for (const text of ["Refused draft", "Unconfirmed draft"]) {
      await user.type(field, text);
      await user.click(screen.getByRole("button", { name: "Send" }));
    }
    await user.type(field, "New draft");
    await user.click(screen.getByRole("link", { name: "Live session" }));
    for (const index of refusalFirst ? [0, 1] : [1, 0]) {
      await act(async () => finishes[index]!(jsonResponse({ error: "Send failed" }, index === 0 ? 409 : 500)));
    }
    await user.click(screen.getByRole("link", { name: "Conversation" }));
    const restored = screen.getByLabelText("Message the L2");
    expect(restored).toHaveValue(refusalFirst ? "Unconfirmed draft\nRefused draft\nNew draft" : "Refused draft\nUnconfirmed draft\nNew draft");
    await user.type(restored, " edited");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm delivery. Check the conversation before sending again.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => finishes[2]!(jsonResponse({ ok: true, message: { id: "confirmed", at: ago(0), role: running.messages[0]!.role, text: "Confirmed correction" } })));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(restored).toHaveValue("");
    expect(finishes).toHaveLength(3);
  });

  it("does not restore an earlier task's failed send into the destination task", async () => {
    setViewport(390);
    let finish!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { finish = resolve; });
    const fetchMock = stub(running, { message: () => response });
    const originalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).includes("/api/task/altitude/other-task")
      ? jsonResponse({ ...running, slug: "other-task", title: "Other task", messages: [] }) : originalFetch(input, init));
    const { user, router } = renderApp({ route });
    await user.type(await screen.findByLabelText("Message the L2"), "Old task message");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await act(() => router.navigate("/projects/altitude/tasks/other-task"));
    await screen.findByRole("heading", { name: "Other task" });
    await user.type(screen.getByLabelText("Message the L2"), "Destination draft");
    await act(async () => { finish(jsonResponse({ error: "Old request failed" }, 500)); });
    expect(screen.getByLabelText("Message the L2")).toHaveValue("Destination draft");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the fault line and the decision card on the phone too", async () => {
    setViewport(390);
    stub(faulted);
    renderApp({ route });
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(document.querySelector(".task-line")).toHaveTextContent("A system problem paused work. Waiting for the coordinator to check the blocker.");
  });
});

describe("L2 activity and steering", () => {
  const activity = {
    generation: "worker-1", state: "available", commentary: { id: "words-1", text: "I am checking the saved retry behavior.", at: new Date(Date.now() - 20_000).toISOString(), time_kind: "source" },
    observation: { label: "Tool output observed", at: new Date(Date.now() - 5000).toISOString() },
  };
  const steering = { state: "running", stop_id: null as string | null, generation: "worker-1", error: null };
  const active = { ...running, steering, activity };

  beforeEach(() => {
    activity.commentary.at = new Date(Date.now() - 20_000).toISOString();
    activity.observation.at = new Date(Date.now() - 5000).toISOString();
  });

  it.each([390, 1440])("keeps header actions disabled after a denied send across views at %i", async (width) => {
    setViewport(width);
    const fetchMock = stub(active, { message: () => jsonResponse({ error: "Access denied" }, 403) });
    const { user } = renderApp({ route });
    const field = await screen.findByRole("textbox", { name: "Message the L2" });
    await user.type(field, "Keep this correction");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("You cannot send messages or answers here.", { exact: false });
    expect(field).toBeDisabled();
    expect(field).toHaveValue("Keep this correction");
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    if (width === 390) {
      await user.click(screen.getByRole("link", { name: "Live session" }));
      expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
      await user.click(screen.getByRole("link", { name: "Conversation" }));
    } else {
      fireEvent.keyDown(document.body, { key: "Escape" });
    }
    expect(actionCall(fetchMock)).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled());
    expect(field).toBeEnabled();
    expect(field).toHaveValue("Keep this correction");
  });

  it.each([390, 1440])("replaces fresh public words without adding replies and hides stale or missing evidence at %i", async (width) => {
    setViewport(width);
    stub(active);
    const { queryClient, user } = renderApp({ route });
    const preview = await screen.findByRole("region", { name: "L2 activity" });
    const update = async (next: unknown) => act(async () => { queryClient.setQueryData(["task", "altitude", "fix-timer"], next); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(within(preview).getByText(activity.commentary.text)).toBeInTheDocument();
    const replies = document.querySelectorAll('[data-role="l2"]').length;
    await user.click(within(preview).getByRole("button", { name: "Expand" }));
    expect(preview).toHaveAttribute("data-expanded");
    const next = { ...activity, commentary: { ...activity.commentary, id: "words-2", text: "Now checking the message race." } };
    await update({ ...active, activity: next });
    expect(within(preview).queryByText(activity.commentary.text)).toBeNull();
    expect(within(preview).getByText("Now checking the message race.")).toBeInTheDocument();
    expect(document.querySelectorAll('[data-role="l2"]').length).toBe(replies);
    for (const hidden of [
      undefined,
      { ...next, observation: { ...next.observation, at: ago(5) } },
      { ...next, commentary: { ...next.commentary, at: ago(5) } },
      { ...next, commentary: { ...next.commentary, at: null, time_kind: "unknown" } },
      { ...next, commentary: { ...next.commentary, text: " " } },
      { ...next, state: "unavailable" },
      { ...next, observation: null },
      { ...next, generation: "worker-2", state: "empty", commentary: null, observation: null },
    ]) {
      await update({ ...active, activity: hidden });
      expect(screen.queryByRole("region", { name: "L2 activity" })).toBeNull();
    }
    await update({ ...active, activity: { ...next, generation: "worker-2" } });
    const resumed = screen.getByRole("region", { name: "L2 activity" });
    expect(within(resumed).getByText(next.commentary.text)).toBeInTheDocument();
    expect(within(resumed).getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });

  it.each([390, 1440])("preserves draft/selection through views and Stop, then sends an explicit correction at %i", async (width) => {
    setViewport(width);
    let record = active;
    const fetchMock = stub(active, { task: () => jsonResponse(record) });
    const { user, queryClient } = renderApp({ route });
    const update = async (next: typeof active) => { record = next; await act(async () => { queryClient.setQueryData(["task", "altitude", "fix-timer"], next); await new Promise((resolve) => setTimeout(resolve, 0)); }); };
    const convo = await screen.findByRole("region", { name: "Task conversation" });
    let field = within(convo).getByRole("textbox") as HTMLTextAreaElement;
    await user.type(field, "Keep this correction");
    field.setSelectionRange(2, 7); fireEvent.select(field);
    if (width === 390) await user.click(screen.getByRole("link", { name: "Live session" }));
    const live = screen.getByRole("region", { name: "Live session" });
    expect(live).toBeVisible();
    expect(screen.getByRole("button", { name: "Stop" }).closest("header")).toBeInTheDocument();
    if (width === 390) {
      await user.click(screen.getByRole("link", { name: "Conversation" }));
      field = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(field.selectionStart).toBe(2); expect(field.selectionEnd).toBe(7);
    }
    const controls = within(screen.getByRole("region", { name: "Task conversation" }));
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body)).generation).toBe("worker-1");
    expect(field).toHaveValue("Keep this correction");
    expect(controls.getByRole("button", { name: "Send" })).toBeDisabled();
    await update({ ...active, state: "blocked", steering: { ...steering, state: "stopping" } });
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
    await update({ ...active, state: "blocked", steering: { ...steering, state: "stopped", stop_id: "stop-1" } });
    expect(screen.getByRole("button", { name: "Continue" }).closest("header")).toBeInTheDocument();
    await user.click(controls.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/l2/message"))).toBe(true));
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", text: "Keep this correction", stop_id: "stop-1", request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    expect(field).toHaveValue("");
  });

  it("Continue retains an unsent draft and waits for authoritative running evidence", async () => {
    const stopped = { ...active, state: "blocked", steering: { ...steering, state: "stopped", stop_id: "stop-1" } };
    const fetchMock = stub(stopped);
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Unsent thought");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", action: "resume", stop_id: "stop-1" });
    expect(screen.getByText("Waiting to resume")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resuming…" })).toBeDisabled();
    expect(field).toHaveValue("Unsent thought");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/l2/message"))).toBe(false);
  });

  it("a replacement generation releases a local Stop and an older response cannot overwrite its controls", async () => {
    let record = active;
    let finishOld!: (response: Response) => void;
    const oldRequest = new Promise<Response>((resolve) => { finishOld = resolve; });
    let calls = 0;
    const fetchMock = stub(active, {
      task: () => jsonResponse(record),
      action: () => ++calls === 1 ? oldRequest : jsonResponse({ ok: true }),
    });
    const { user, queryClient } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Keep the draft");
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByText("Stopping…")).toBeInTheDocument();
    record = { ...active, steering: { ...steering, generation: "worker-2" }, activity: { ...activity, generation: "worker-2" } };
    await act(async () => {
      queryClient.setQueryData(["task", "altitude", "fix-timer"], record);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByText("Stopping…")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => { finishOld(jsonResponse({ error: "Old generation refused" }, 409)); });
    expect(screen.getByText("Stopping…")).toBeInTheDocument();
    expect(screen.queryByText(/Stop unconfirmed/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field).toHaveValue("Keep the draft");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/task/action"))
      .map(([, init]) => JSON.parse(String(init?.body)).generation)).toEqual(["worker-1", "worker-2"]);
  });

  it("accepted L2 sends stay saved when refresh fails and leave a newer draft untouched", async () => {
    let accept!: (response: Response) => void;
    const pendingSend = new Promise<Response>((resolve) => { accept = resolve; });
    let failRead = false;
    const fetchMock = stub(active, {
      message: () => pendingSend,
      task: () => failRead ? jsonResponse({ error: "Read unavailable" }, 503) : jsonResponse(active),
    });
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Saved correction");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "Next draft");
    failRead = true;
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    await act(async () => { accept(jsonResponse({ ok: true, message: {
      id: JSON.parse(String(call?.[1]?.body)).request_id, role: running.messages[0]?.role, at: new Date().toISOString(), text: "Saved correction",
      delivery: { state: "queued", at: null },
    } })); });
    await screen.findByText(/Showing saved conversation/);
    expect(field).toHaveValue("Next draft");
    expect(screen.getAllByText("Saved correction")).toHaveLength(1);
    expect(document.querySelector(".composer-hint[role=alert]")).toBeNull();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/l2/message"))).toHaveLength(1);
  });

  it("Escape respects draft, overlay, recording and composition before stopping the worker", async () => {
    installVoiceBrowser();
    const fetchMock = stub(active);
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.click(field); await user.keyboard("{Escape}");
    expect(actionCall(fetchMock)).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "Live session" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull(); expect(actionCall(fetchMock)).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await screen.findByRole("button", { name: "Stop voice input" });
    fireEvent.keyDown(document.body, { key: "Escape" });
    await screen.findByRole("button", { name: "Start voice input" });
    expect(actionCall(fetchMock)).toBeUndefined();
    await user.click(screen.getByRole("heading", { name: "Fix the timer" }));
    fireEvent.keyDown(document.body, { key: "Escape", isComposing: true });
    expect(actionCall(fetchMock)).toBeUndefined();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
  });

  it("shows receipts on the existing bubble and removes the preview on a question block or completion", async () => {
    const message = { ...running.messages[0]!, delivery: { state: "queued", at: null } };
    stub({ ...active, messages: [message] });
    const { queryClient } = renderApp({ route });
    await screen.findByText("Queued");
    const update = (next: unknown) => act(async () => { queryClient.setQueryData(["task", "altitude", "fix-timer"], next); await new Promise((resolve) => setTimeout(resolve, 0)); });
    await update({ ...active, messages: [{ ...message, delivery: { state: "delivered", at: ago(1) } }] });
    expect(screen.getByText(message.text).closest(".bubble")).not.toHaveAttribute("data-state");
    expect(screen.queryByText("Queued")).toBeNull();
    expect(screen.getAllByText(message.text)).toHaveLength(1);
    await update({ ...active, state: "blocked", steering: { ...steering, state: "idle" }, question: decision, messages: running.messages });
    expect(screen.queryByRole("region", { name: "L2 activity" })).toBeNull();
    expect(screen.getByText(decision.question)).toBeInTheDocument();
    await update({ ...done, steering: { ...steering, state: "idle" } });
    expect(screen.queryByRole("textbox")).toBeNull(); expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
