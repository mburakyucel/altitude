import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const overview = {
  projects: [{ name: "altitude", managed: true }],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
  now: new Date().toISOString(),
};

const task = {
  slug: "fix-timer",
  state: "running",
  title: "Fix the timer",
  attempt: 1,
  session_id: "0123456789abcdef",
  agent_id: "a-9",
  l2_engine: "claude",
  worktree: ".claude/worktrees/fix-timer",
  files: {},
  events: [],
  messages: [],
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

function stub() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/transcript/")) return jsonResponse(transcript);
    if (url.includes("/api/task/")) return jsonResponse(task);
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const route = "/projects/altitude/tasks/fix-timer/live";

describe("LiveSession", () => {
  it("reads the session as prompts, replies, and folded tool calls", async () => {
    stub();
    renderApp({ route });

    const view = await screen.findByRole("region", { name: "Live transcript" });
    expect(await within(view).findByRole("separator")).toHaveTextContent("queued → running · altd");

    const prompt = view.querySelector('[data-role="user"]');
    expect(prompt).toHaveTextContent("Prompt");
    expect(prompt).toHaveTextContent("Fix the timer in alt.");
    expect(prompt?.querySelector("strong")).toHaveTextContent("timer");
    expect(prompt?.querySelector("code")).toHaveTextContent("alt");

    const reply = view.querySelector('[data-role="assistant"]');
    expect(reply).toHaveTextContent("Claude");
    expect(reply).toHaveTextContent("Reading the timer code first.");
    expect(reply?.querySelector("pre")).toHaveTextContent("make test");

    const command = within(view).getByText("$ git status").closest("details");
    expect(command).not.toHaveAttribute("open");
    expect(command).toHaveTextContent("2 lines");
    expect(command?.querySelector("[data-output]")).toHaveTextContent("nothing to commit");
    expect(within(view).queryByText("nothing to commit", { selector: "article *" })).toBeNull();

    const read = within(view).getByText("Read altitude/timer.py").closest("details");
    expect(read).toHaveTextContent("running…");

    const codex = within(view).getByText("$ make test").closest("details");
    expect(codex?.querySelector("[data-output]")).toHaveTextContent("ok");

    expect(view.textContent).not.toContain("attachment");
    expect(screen.getByText(/model reasoning is never shown/)).toBeInTheDocument();
  });

  it("finds a row by its output and keeps raw mode as the escape hatch", async () => {
    const fetchMock = stub();
    const { user } = renderApp({ route });

    const view = await screen.findByRole("region", { name: "Live transcript" });
    await within(view).findByText("$ git status");
    await user.type(screen.getByLabelText("Search transcript"), "nothing to commit");
    expect(within(view).getByText("$ git status")).toBeInTheDocument();
    expect(within(view).queryByText("Read altitude/timer.py")).toBeNull();
    await user.clear(screen.getByLabelText("Search transcript"));

    await user.click(screen.getByRole("button", { name: "Raw" }));
    const raw = await screen.findByRole("region", { name: "Raw records" });
    expect(within(raw).getByText(/claude · engine · attachment · system/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("raw=1"))).toBe(true);
    await user.click(screen.getByRole("button", { name: "Conversation" }));
    expect(await screen.findByRole("region", { name: "Live transcript" })).toBeInTheDocument();
  });
});
