import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  engines: [{ engine: "claude", label: "Claude", known: true }],
  now: new Date().toISOString(),
};

const task = {
  slug: "fix-timer",
  state: "running",
  title: "Fix the timer",
  attempt: 1,
  session_id: "0123456789abcdef",
  l2_engine: "claude",
  files: {},
  events: [],
  messages: [],
  activity: { generation: "w1", state: "available", commentary: null, observation: { at: new Date().toISOString(), label: "Recorded output changed" } },
};

const at = (second: number) => `2026-08-29T12:00:${String(second).padStart(2, "0")}+00:00`;

// A prompt, a reply with a code block, a command with its output, a file read still waiting, an engine
// bookkeeping record, and one Codex-style command that carries its own output.
const transcript = {
  project: "altitude",
  slug: "fix-timer",
  engine: "claude",
  session_id: "0123456789abcdef",
  attempt: 1, cursor: "epoch:8", lower: "", next: "", more: false, reset: false,
  has_earlier: false, has_engine_records: true, deleted: [],
  redaction: "credential-shaped keys and values are redacted; model reasoning is never shown",
  events: [
    { seq: 0, source: "platform", kind: "boundary", type: "state", role: "system", at: at(0), text: "queued → running · altd" },
    { seq: 1, source: "claude", kind: "message", type: "user", role: "user", at: at(1), text: "# Brief\nFix the **timer** in `alt`." },
    { seq: 2, source: "claude", kind: "message", type: "assistant", role: "assistant", at: at(2), text: "Reading the timer code first.\n\n```\nmake test\n```" },
    { seq: 3, source: "claude", kind: "command", type: "tool_use", role: "assistant", at: at(3), text: "git status", tool: "Bash", summary: "git status", tool_use_id: "toolu_1" },
    { seq: 4, source: "claude", kind: "result", type: "tool_result", role: "tool", at: at(4), text: "On branch fix-timer\nnothing to commit", tool_use_id: "toolu_1", error: false },
    { seq: 5, source: "claude", kind: "file", type: "tool_use", role: "assistant", at: at(5), text: "", tool: "Read", summary: "altitude/timer.py", tool_use_id: "toolu_2" },
    { seq: 6, source: "claude", kind: "engine", type: "attachment", role: "system", at: at(6), text: "" },
    { seq: 7, source: "codex", kind: "command", type: "item.completed", role: "assistant", at: null, text: "make test", tool: "command", summary: "make test", tool_use_id: "item_1", status: "completed", output: "ok", error: false },
  ],
};

const rawEvents = transcript.events.map((event) => ({ ...event, id: `record-${event.seq}`, order: String(event.seq).padStart(8, "0"), version: event.seq + 1 }));
const normalEvents = rawEvents.filter((event) => !["engine", "result"].includes(event.kind)).map((event) => event.seq === 3
  ? { ...event, status: "completed", output: "On branch fix-timer\nnothing to commit" } : event);
const normalTranscript = { ...transcript, events: normalEvents };
const boundariesOnly = { ...normalTranscript, has_engine_records: false, events: [normalEvents[0]] };

function stub(options: { task?: unknown; transcript?: (url: string) => Response | Promise<Response> } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/transcript/")) return options.transcript ? options.transcript(url) : jsonResponse({ ...normalTranscript, events: url.includes("raw=1") ? rawEvents : normalEvents });
    if (url.includes("/api/task/")) return jsonResponse(options.task ?? task);
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const route = "/projects/altitude/tasks/fix-timer/live";

afterEach(() => setViewport(1024));

async function openPanel() {
  setViewport(1440);
  return screen.findByRole("region", { name: "Live session" });
}

describe("LiveSession", () => {
  it("reads the session as tinted prompts, the worker's prose, and folded tool rows", async () => {
    stub();
    renderApp({ route });

    const panel = await openPanel();
    const view = await within(panel).findByRole("region", { name: "Live transcript" });
    const separators = within(view).getAllByRole("separator");
    expect(separators[0]).toHaveTextContent("queued → running · altd");

    const prompt = view.querySelector('[data-role="user"]');
    expect(prompt).toHaveClass("session-prompt");
    expect(prompt).toHaveTextContent("Prompt");
    expect(prompt).toHaveTextContent("Fix the timer in alt.");
    expect(prompt?.querySelector("strong")).toHaveTextContent("timer");
    expect(prompt?.querySelector("code")).toHaveTextContent("alt");
    expect(prompt?.querySelector("time")).toHaveAttribute("datetime", at(1));

    const reply = view.querySelector('[data-role="assistant"]');
    expect(reply).toHaveTextContent("Claude");
    expect(reply).toHaveTextContent("Reading the timer code first.");
    expect(reply?.querySelector("pre")).toHaveTextContent("make test");

    const command = within(view).getByText("git status", { selector: "code" }).closest("details");
    expect(command).not.toHaveAttribute("open");
    expect(command?.querySelector(".session-tool-name")).toHaveTextContent("$");
    expect(command).toHaveTextContent("2 lines");
    expect(command?.querySelector("[data-output]")).toHaveTextContent("nothing to commit");

    const read = within(view).getByText("altitude/timer.py", { selector: "code" }).closest("details");
    expect(read?.querySelector(".session-tool-name")).toHaveTextContent("Read");
    expect(read?.querySelector(".session-hint")).toHaveTextContent(/^running · \d+ d$/);

    const codex = within(view).getByText("make test", { selector: "code" }).closest("details");
    expect(codex?.querySelector("[data-output]")).toHaveTextContent("ok");
    expect(codex?.querySelector(".session-time")).toHaveTextContent("time unavailable");

    expect(view.textContent).not.toContain("attachment");
    expect(within(panel).getByRole("button", { name: "Raw events" })).toHaveAttribute("title", transcript.redaction);
    expect(within(panel).getByText("Following live · new steps appear at the bottom").closest("[role=separator]")).toHaveAttribute("data-tone", "live");
    expect(panel.querySelector(".live-pulse")).toHaveAttribute("data-tone", "live");
    expect(within(panel).getByRole("status")).toHaveTextContent(/^Working · output \d+ sec ago$/);
    expect(within(panel).getByRole("status").querySelector(".dot")).toHaveAttribute("data-pulse", "true");
  });

  it("stops the pulse and names the quiet time when a running worker records no output", async () => {
    const quiet = new Date(Date.now() - 4 * 60_000).toISOString();
    stub({ task: { ...task, activity: { ...task.activity, observation: { at: quiet, label: "Recorded output changed" } } } });
    renderApp({ route });
    const panel = await openPanel();
    const status = await within(panel).findByRole("status");
    expect(status).toHaveTextContent("No new activity for 4 min");
    expect(status.querySelector(".dot")).not.toHaveAttribute("data-pulse");
    expect(panel.querySelector(".live-pulse")).toHaveAttribute("data-tone", "muted");
  });

  it("claims no activity when the activity record is unavailable", async () => {
    stub({ task: { ...task, activity: { ...task.activity, state: "unavailable" } } });
    renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByRole("status")).toHaveTextContent("Activity unavailable");
    expect(panel.querySelector(".live-pulse")).toHaveAttribute("data-tone", "muted");
  });

  it("keeps Raw events behind its toggle and pauses following", async () => {
    const fetchMock = stub();
    const { user } = renderApp({ route });

    const panel = await openPanel();
    await within(panel).findByRole("region", { name: "Live transcript" });
    await user.click(within(panel).getByRole("button", { name: "Raw events" }));
    const raw = await within(panel).findByRole("region", { name: "Raw events" });
    expect(within(raw).getByText(/claude · engine · attachment · system/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Raw events" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("raw=1"))).toBe(true));
    await user.click(within(panel).getByRole("button", { name: "Raw events" }));
    expect(await within(panel).findByRole("region", { name: "Live transcript" })).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Pause" }));
    expect(within(panel).getByText("Paused · Follow to catch up")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Follow" }));
    expect(within(panel).getByText("Following live · new steps appear at the bottom")).toBeInTheDocument();
  });

  it("shows the connecting skeleton, then only boundaries while the worker has not written yet", async () => {
    let release: (() => void) | undefined;
    stub({
      transcript: () => {
        return new Promise<Response>((resolve) => {
          release = () => resolve(jsonResponse(boundariesOnly));
        }) as unknown as Response;
      },
    });
    renderApp({ route });

    const panel = await openPanel();
    expect(await within(panel).findByLabelText("Connecting")).toBeInTheDocument();
    expect(panel.querySelector(".live-pulse")).toHaveAttribute("data-tone", "muted");
    release?.();
    await waitFor(() => expect(within(panel).queryByLabelText("Connecting")).toBeNull());
    expect(within(panel).getByRole("separator")).toHaveTextContent("queued → running · altd");
    expect(within(panel).getByText("Connecting to the session…")).toBeInTheDocument();
  });

  it("says the session ended, or paused, once the task is no longer running", async () => {
    stub({ task: { ...task, state: "done" } });
    renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText("Session ended")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Pause" })).toBeNull();
    expect(panel.querySelector(".live-pulse")).toHaveAttribute("data-tone", "off");
  });

  it("says the session is paused while the task is blocked", async () => {
    stub({ task: { ...task, state: "blocked", blocked_reason: "which suite", waiting_on: "l3" } });
    renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText("Session paused until the task resumes")).toBeInTheDocument();
  });

  it("says there is no session file when the transcript is gone", async () => {
    stub({ transcript: () => jsonResponse({ error: "transcript unavailable for this task generation" }, 404) });
    renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText("No session file for this attempt")).toBeInTheDocument();
  });

  it("offers Retry when the session cannot be read", async () => {
    let fail = true;
    stub({ transcript: () => (fail ? jsonResponse({ error: "boom" }, 500) : jsonResponse(normalTranscript)) });
    const { user } = renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText(/Could not read the session/)).toBeInTheDocument();
    fail = false;
    await user.click(within(panel).getByRole("button", { name: "Retry" }));
    expect(await within(panel).findByText("Reading the timer code first.")).toBeInTheDocument();
  });

  it("uses whole-session facts even when the recent window contains only a platform boundary", async () => {
    stub({ task: { ...task, state: "done" }, transcript: () => jsonResponse({ ...normalTranscript, events: [normalEvents[0]], has_earlier: true }) });
    renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText("Session ended")).toBeInTheDocument();
    expect(within(panel).queryByText("No session file for this attempt")).toBeNull();
  });

  it("keeps following when content shrinks under it, and pauses when the reader scrolls up", async () => {
    stub();
    renderApp({ route });
    const panel = await openPanel();
    await within(panel).findByRole("region", { name: "Live transcript" });
    const body = within(panel).getByLabelText("Session activity");
    // jsdom has no layout: give the body a browser's geometry, which clamps scrollTop to the content.
    let height = 1000;
    let top = 0;
    Object.defineProperty(body, "clientHeight", { configurable: true, get: () => 400 });
    Object.defineProperty(body, "scrollHeight", { configurable: true, get: () => height });
    Object.defineProperty(body, "scrollTop", { configurable: true, get: () => top, set: (value: number) => { top = Math.max(0, Math.min(value, height - 400)); } });
    body.scrollTop = height;
    fireEvent.scroll(body);
    height = 800;
    body.scrollTop = top;
    fireEvent.scroll(body);
    expect(within(panel).getByRole("button", { name: "Pause" })).toBeInTheDocument();
    body.scrollTop = 100;
    fireEvent.scroll(body);
    expect(within(panel).getByRole("button", { name: "Follow" })).toBeInTheDocument();
  });

  it("loads one earlier page only on upward intent, including a short initial viewport, and retries history in place", async () => {
    let historyReads = 0;
    let fail = true;
    const earlier = { ...normalEvents[2], id: "earlier", order: "00000000", text: "An earlier step" };
    stub({ transcript: (url) => {
      if (url.includes("mode=history")) {
        historyReads += 1;
        return fail ? jsonResponse({ error: "unavailable" }, 503)
          : jsonResponse({ ...normalTranscript, events: [earlier], has_earlier: true, lower: "00000000" });
      }
      return jsonResponse({ ...normalTranscript, has_earlier: true, lower: "00000001" });
    } });
    const { user } = renderApp({ route });
    const panel = await openPanel();
    await within(panel).findByRole("region", { name: "Live transcript" });
    expect(historyReads).toBe(0);
    const historySlot = panel.querySelector(".live-history-status");
    expect(historySlot).toBeEmptyDOMElement();
    const body = within(panel).getByLabelText("Session activity");
    fireEvent.wheel(body, { deltaY: -60 });
    await within(panel).findByText("Could not load earlier activity.");
    expect(panel.querySelector(".live-history-status")).toBe(historySlot);
    expect(within(panel).getByText("Reading the timer code first.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Follow" })).toBeInTheDocument();
    fail = false;
    await user.click(within(panel).getByRole("button", { name: "Retry" }));
    await within(panel).findByText("An earlier step");
    expect(historySlot).toBeEmptyDOMElement();
    expect(historyReads).toBe(2);
    expect(within(panel).queryByText("Beginning of session")).toBeNull();
    fireEvent.keyDown(body, { key: "ArrowUp" });
    await waitFor(() => expect(historyReads).toBe(3));
    expect(panel.querySelectorAll('[data-transcript-id="earlier"]')).toHaveLength(1);
  });

  it("keeps expanded tool rows and read-only text when updates fail, then resumes updates without duplicate rows", async () => {
    let fail = false;
    stub({ transcript: (url) => url.includes("mode=delta") && fail
      ? jsonResponse({ error: "offline" }, 503) : jsonResponse(normalTranscript) });
    const { user } = renderApp({ route });
    const panel = await openPanel();
    const command = (await within(panel).findByText("git status", { selector: "code" })).closest("details")!;
    await user.click(command.querySelector("summary")!);
    expect(command).toHaveAttribute("open");
    fail = true;
    fireEvent.focus(window);
    await within(panel).findByText("Could not update the session.");
    expect(command).toHaveAttribute("open");
    expect(within(panel).queryByText("Following live · new steps appear at the bottom")).toBeNull();
    fail = false;
    await user.click(within(panel).getByRole("button", { name: "Retry" }));
    await within(panel).findByText("Following live · new steps appear at the bottom");
    expect(panel.querySelectorAll('[data-transcript-id="record-3"]')).toHaveLength(1);
    expect(command).toHaveAttribute("open");
  });

  it("fetches raw detail only by disclosure and each additional chunk only by Show more", async () => {
    const detailOffsets: string[] = [];
    stub({ transcript: (url) => {
      const query = new URL(url, "http://localhost").searchParams;
      if (query.get("mode") === "record") {
        detailOffsets.push(query.get("offset")!);
        return jsonResponse(query.get("offset") === "0" ? { text: "First redacted chunk", next_offset: 4000 }
          : { text: " and the remaining record", next_offset: null });
      }
      return jsonResponse({ ...normalTranscript, events: query.get("raw") === "1" ? [rawEvents[3]] : normalEvents });
    } });
    const { user } = renderApp({ route });
    const panel = await openPanel();
    await within(panel).findByRole("region", { name: "Live transcript" });
    await user.click(within(panel).getByRole("button", { name: "Raw events" }));
    const raw = await within(panel).findByRole("region", { name: "Raw events" });
    expect(detailOffsets).toEqual([]);
    await user.click(within(raw).getByRole("button", { name: "Full record" }));
    await within(raw).findByText("First redacted chunk");
    expect(detailOffsets).toEqual(["0"]);
    await user.click(within(raw).getByRole("button", { name: "Show more" }));
    await within(raw).findByText("First redacted chunk and the remaining record");
    expect(detailOffsets).toEqual(["0", "4000"]);
    expect(within(raw).queryByRole("button", { name: "Show more" })).toBeNull();
  });
});
