import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import { installVoiceBrowser } from "../components/voiceTest";
import type { TaskView } from "../data/api";

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
    { id: "m-1", at: "2026-08-29T12:01:00Z", role: "burak", text: "Keep the change focused." },
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
  cursor: 2,
  events: [
    { seq: 0, source: "platform", kind: "boundary", type: "state", at: "2026-08-29T12:00:00", text: "queued → running" },
    { seq: 1, source: "claude", kind: "message", type: "assistant", role: "assistant", at: null, text: "Reading the timer code" },
  ],
  redaction: "credential-shaped keys and values are redacted",
};

interface StubOptions {
  repository?: string | null;
  overview?: typeof overview;
  message?: () => Response | Promise<Response>;
  action?: () => Response | Promise<Response>;
  task?: () => Response;
}

function stub(task: unknown, options: StubOptions = {}) {
  // The server keeps an accepted message on the task: the refetch after a send returns it.
  const sent: unknown[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(options.overview ?? overview);
    if (url.includes("/api/transcribe")) return jsonResponse({ text: "spoken detail" });
    if (url.includes("/api/task/action")) return options.action ? options.action() : jsonResponse({ ok: true });
    if (url.includes("/api/transcript/")) return jsonResponse(transcript);
    if (url.includes("/api/task/")) {
      if (options.task) return options.task();
      const record = task as { messages?: unknown[] };
      return jsonResponse({ ...record, messages: [...(record.messages ?? []), ...sent] });
    }
    if (url.includes("/api/l2/message")) {
      if (options.message) return options.message();
      const body = JSON.parse(String(init?.body)) as { text: string; request_id: string };
      const message = { id: body.request_id, at: new Date().toISOString(), role: running.messages[0]?.role, text: body.text };
      sent.push(message);
      return jsonResponse({ ok: true, message });
    }
    if (url.includes("/api/project/")) return jsonResponse({ name: "altitude", tasks: [], repository: options.repository ?? null });
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
    let reads = 0;
    fetchMock.mockImplementation((input, init) => {
      if (String(input).includes("/api/l2/message")) return pendingReceipt;
      if (String(input).includes("/api/task/altitude/fix-timer") && ++reads === 2) return pendingRead;
      return original(input, init);
    });
    const { user, router, queryClient } = renderApp({ route });
    await screen.findByText("Keep the change focused.");
    await user.type(screen.getByRole("textbox", { name: "Message the L2" }), "Keep the queued sample");
    await user.click(screen.getByRole("button", { name: "Send" }));
    void queryClient.invalidateQueries({ queryKey: ["task", "altitude", "fix-timer"] });
    await waitFor(() => expect(reads).toBe(2));
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
    const opener = await screen.findByRole("button", { name: "Task details" });
    expect(screen.getByText("Waits for L3")).toBeInTheDocument();
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
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Opus on Claude")).toBeInTheDocument();
    expect(screen.queryByText("attempt 1 · started 7 min ago · 34% of its context used")).toBeNull();

    const convo = screen.getByRole("region", { name: "Task conversation" });
    const mine = convo.querySelector("[data-mine]");
    expect(mine).toHaveTextContent("Keep the change focused.");
    expect(mine?.querySelector(".bubble")).toBeInTheDocument();
    const reply = convo.querySelector('[data-role="l2"]');
    expect(reply).toHaveTextContent("I will use one focused PR.");
    expect(reply?.querySelector(".bubble")).toBeNull();
    expect(convo.querySelector('[data-role="l3"]')).toHaveTextContent("L3Answered from the brief.");
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
      "/api/transcript/altitude/fix-timer?engine=claude&session_id=0123456789abcdef&raw=0",
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
    const group = screen.getByRole("group", { name: "Reject this task?" });
    expect(group).toHaveTextContent("Reject this task? Its worker ends and the task is archived.");
    await user.type(within(group).getByLabelText("Reason (optional)"), "waiting on the API");
    await user.click(within(group).getByRole("button", { name: "Reject" }));
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
    expect(within(conversation).getByText("Waits for the index migration to land")).toBeInTheDocument();
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
    expect(screen.queryByText("Waits for the index migration to land")).toBeNull();
    expect(field).toBeEnabled();
    expect(field).toHaveValue("Preserve this draft");
    expect(within(conversation).getByText("Keep pagination compatible.")).toBeInTheDocument();

    task.state = "running";
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["task", "altitude", task.slug] }); });
    await screen.findByText("Running", { exact: true });
    expect(field).toHaveValue("Preserve this draft");
    expect(within(conversation).getByRole("button", { name: /^Stop/ })).toBeInTheDocument();
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
    expect(within(panel).getByText("Waits for resume · usage limit: the window resets at 02:00")).toBeInTheDocument();
    expect(screen.getByText("Delivered when Altitude resumes the L2.")).toBeInTheDocument();
  });

  it("puts the question at its durable conversation anchor with preceding context", async () => {
    stub({ ...stuck, question: decision, questions: [decision] }, { overview: { ...overview, queue: [decision] } });
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Needs your answer")).toBeInTheDocument();
    const convo = screen.getByRole("region", { name: "Task conversation" });
    const card = convo.querySelector("[data-question-id=\"q-timer\"]")!;
    expect(card).toHaveTextContent("Should the timer keep the old default?");
    expect(convo.firstElementChild?.firstElementChild).toHaveClass("convo-col");
    expect(card.closest(".conversation-question")?.previousElementSibling).toHaveTextContent("Keep the change focused.");
    expect(document.querySelector(".task-line")).toBeNull();
    expect(screen.getByLabelText("Message the L2")).toBeInTheDocument();
  });

  it("discloses the full L3 block reason without a permanent paragraph", async () => {
    stub(askingL3);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    expect(screen.getByText("Waits for L3")).toBeInTheDocument();
    expect(screen.queryByText("which suite covers the timer")).toBeNull();
    expect(document.querySelector(".task-line")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Task details" }));
    expect(within(screen.getByRole("dialog", { name: "Task details" })).getByText("which suite covers the timer")).toBeInTheDocument();
  });

  it("shows a fault as one red sentence and says L3 has been told", async () => {
    stub(faulted);
    renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const line = document.querySelector(".task-line");
    expect(line).toHaveTextContent("The sandbox refused the network socket. L3 has been told.");
    expect(line).not.toHaveTextContent("Tried twice");
    expect(line).toHaveClass("text-danger");
  });

  it("keeps an unpunctuated fault concise and discloses its complete reason", async () => {
    const reason = `The verification browser could not start ${"while checking the configured environment ".repeat(12)}`.trim();
    stub({ ...faulted, blocked_reason: reason });
    const { user } = renderApp({ route });
    const opener = await screen.findByRole("button", { name: "Task details" });
    const notice = document.querySelector(".task-fault");
    expect(notice).toHaveTextContent("The verification browser could not start");
    expect(notice).toHaveTextContent("… L3 has been told.");
    expect(notice!.textContent!.length).toBeLessThanOrEqual(120);
    await user.click(opener);
    expect(within(screen.getByRole("dialog", { name: "Task details" })).getByText(reason)).toBeInTheDocument();
  });

  it.each([390, 1440])("links the PR in a new tab at %i pixels when the repository is known", async (width) => {
    setViewport(width);
    stub(done, { repository: "https://github.com/example/project" });
    const { user } = renderApp({ route });

    if (width === 390) await user.click(await screen.findByRole("button", { name: "Task details" }));
    const link = await screen.findByRole("link", { name: "PR #202 merged · main checks passed" });
    expect(link).toHaveAttribute("href", "https://github.com/example/project/pull/202");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([390, 1440])("keeps the PR as text at %i pixels when the repository is null", async (width) => {
    setViewport(width);
    stub(done, { repository: null });
    const { user } = renderApp({ route });

    if (width === 390) await user.click(await screen.findByRole("button", { name: "Task details" }));
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
    await user.click(screen.getByRole("button", { name: "Task details" }));
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

  it("shows the bubble at once and keeps the stored row once the server accepts it", async () => {
    const fetchMock = stub(running);
    const { user } = renderApp({ route });

    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    await user.type(screen.getByLabelText("Message the L2"), "prefer the smaller diff");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/l2/message"))).toBe(true));
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/l2/message"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", text: "prefer the smaller diff", request_id: expect.stringMatching(/^[0-9a-f]{32}$/) });
    const row = await screen.findByText("prefer the smaller diff");
    await waitFor(() => expect(row.closest(".msg-row")).not.toHaveAttribute("data-pending"));
    expect(screen.getByLabelText("Message the L2")).toHaveValue("");
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
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice input" }));
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
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/transcribe")) await gate;
      return originalFetch(input, init);
    }));
    const { user, router } = renderApp({ route: `${route}?question=q-timer&revision=1` });
    await user.type(await screen.findByLabelText("Message the L2"), "Original question reply");
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(screen.getByRole("button", { name: "Send" }));
    await act(() => router.navigate("/monitor"));
    task.question = { ...decision, id: "q-other", revision: 2, question: "A newer unrelated question?" };
    await act(() => router.navigate(`${route}?question=q-other&revision=2`));
    await screen.findByText("A newer unrelated question?");
    expect(screen.getByLabelText("Message the L2")).toHaveValue("Original question reply");
    await act(async () => release());
    await waitFor(() => expect(originalFetch.mock.calls.some(([url]) => String(url).includes("/api/l2/message"))).toBe(true));
    const request = originalFetch.mock.calls.find(([url]) => String(url).includes("/api/l2/message"));
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ project: "altitude", slug: "fix-timer", text: "Original question reply spoken detail", question_id: "q-timer", revision: 1 });
  });

  it("resets the composer when navigating between cached tasks", async () => {
    installVoiceBrowser();
    const other = { ...running, slug: "other-task", title: "Other task", messages: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/overview")) return jsonResponse(overview);
        if (url.includes("/api/transcribe")) return jsonResponse({ text: "fix-timer only" });
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
    await user.click(screen.getByRole("button", { name: "Start voice input" }));
    await user.click(await screen.findByRole("button", { name: "Stop voice input" }));
    await waitFor(() => expect(field).toHaveValue("Fix-only draft fix-timer only"));

    await router.navigate("/projects/altitude/tasks/other-task");
    await screen.findByRole("heading", { level: 1, name: "Other task" });
    expect(screen.getByLabelText("Message the L2")).toHaveValue("");
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
    expect(document.querySelector(".task-state-line")).toHaveTextContent("L2 · Running");
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
    expect(screen.queryByLabelText("Message the L2")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps Stop directly beside the composer on phone", async () => {
    setViewport(390);
    const fetchMock = stub(running);
    const { user } = renderApp({ route });
    await screen.findByRole("heading", { level: 1, name: "Fix the timer" });
    const stop = screen.getByRole("button", { name: "Stop" });
    expect(stop.closest(".convo-dock")).toBeInTheDocument();
    await user.click(stop);
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
  });

  it.each([409, 500, 200])("keeps send outcome %i and newer typing across a Live session switch", async (status) => {
    setViewport(390);
    let finish!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { finish = resolve; });
    let record = running;
    stub(running, { message: () => response, task: () => jsonResponse(record) });
    const { user } = renderApp({ route });
    const field = await screen.findByLabelText("Message the L2");
    await user.type(field, "Original message");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(field, "New thought");
    await user.click(screen.getByRole("button", { name: "View live session" }));
    expect(screen.queryByLabelText("Message the L2")).toBeNull();
    const message = { id: "after-view-switch", at: new Date().toISOString(), role: running.messages[0]!.role, text: "Original message" };
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
    await user.click(screen.getByRole("button", { name: "View live session" }));
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
    expect(document.querySelector(".task-line")).toHaveTextContent("The sandbox refused the network socket. L3 has been told.");
  });
});

describe("L2 activity and steering", () => {
  const activity = {
    generation: "worker-1", state: "available", commentary: { id: "words-1", text: "I am checking the saved retry behavior.", at: new Date(Date.now() - 20_000).toISOString(), time_kind: "source" },
    observation: { label: "Tool output observed", at: new Date(Date.now() - 5000).toISOString() },
  };
  const steering = { state: "running", stop_id: null as string | null, generation: "worker-1", error: null };
  const active = { ...running, steering, activity };
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
    await user.click(within(convo).getByRole("button", { name: "View live session" }));
    const live = screen.getByRole("region", { name: "Live session" });
    expect(within(live).getByRole("button", { name: "Stop" })).toBeInTheDocument();
    if (width === 390) {
      await user.click(screen.getByRole("link", { name: "Conversation" }));
      field = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(field.selectionStart).toBe(2); expect(field.selectionEnd).toBe(7);
    }
    const controls = within(screen.getByRole("region", { name: "Task conversation" }));
    await user.click(controls.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body)).generation).toBe("worker-1");
    expect(field).toHaveValue("Keep this correction");
    expect(controls.getByRole("button", { name: "Send" })).toBeDisabled();
    await update({ ...active, state: "blocked", steering: { ...steering, state: "stopping" } });
    expect(controls.queryByRole("button", { name: "Continue session" })).toBeNull();
    await update({ ...active, state: "blocked", steering: { ...steering, state: "stopped", stop_id: "stop-1" } });
    expect(controls.getByRole("button", { name: "Continue session" })).toBeInTheDocument();
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
    await user.click(screen.getByRole("button", { name: "Continue session" }));
    await waitFor(() => expect(actionCall(fetchMock)).toBeDefined());
    expect(JSON.parse(String(actionCall(fetchMock)?.[1]?.body))).toEqual({ project: "altitude", slug: "fix-timer", action: "resume", stop_id: "stop-1" });
    expect(screen.getByText("Waiting to resume")).toBeInTheDocument();
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
    await act(async () => { accept(jsonResponse({ ok: true, message: {
      id: "saved-correction", role: running.messages[0]?.role, at: new Date().toISOString(), text: "Saved correction",
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
    await screen.findByText("Queued · waiting for a checkpoint");
    const update = (next: unknown) => act(async () => { queryClient.setQueryData(["task", "altitude", "fix-timer"], next); await new Promise((resolve) => setTimeout(resolve, 0)); });
    await update({ ...active, messages: [{ ...message, delivery: { state: "delivered", at: ago(1) } }] });
    expect(screen.getByText("Delivered to session")).toBeInTheDocument();
    expect(screen.queryByText("Queued · waiting for a checkpoint")).toBeNull();
    expect(screen.getAllByText(message.text)).toHaveLength(1);
    await update({ ...active, state: "blocked", steering: { ...steering, state: "idle" }, question: decision, messages: running.messages });
    expect(screen.queryByRole("region", { name: "L2 activity" })).toBeNull();
    expect(screen.getByText(decision.question)).toBeInTheDocument();
    await update({ ...done, steering: { ...steering, state: "idle" } });
    expect(screen.queryByRole("textbox")).toBeNull(); expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
