import { screen, waitFor, within } from "@testing-library/react";
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
  cursor: 8,
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

const boundariesOnly = { ...transcript, cursor: 1, events: [transcript.events[0]] };

function stub(options: { task?: unknown; transcript?: () => Response } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/transcript/")) return options.transcript ? options.transcript() : jsonResponse(transcript);
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
    expect(within(panel).getByRole("status")).toHaveTextContent(/^Recorded output changed · \d+ sec ago$/);
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
    stub({ transcript: () => (fail ? jsonResponse({ error: "boom" }, 500) : jsonResponse(transcript)) });
    const { user } = renderApp({ route });
    const panel = await openPanel();
    expect(await within(panel).findByText(/Could not read the session/)).toBeInTheDocument();
    fail = false;
    await user.click(within(panel).getByRole("button", { name: "Retry" }));
    expect(await within(panel).findByText("Reading the timer code first.")).toBeInTheDocument();
  });
});
