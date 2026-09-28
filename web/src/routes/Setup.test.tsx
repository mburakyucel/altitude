import { fireEvent, screen, within, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp, setViewport } from "../test/render";
import type { Setup } from "../data/api";

const healthy: Setup = { project: "atlas", status: "ready", checked_at: "2026-09-14T12:00:00Z", steps: [
  { id: "folder", label: "Project folder", status: "reused", detail: "Already registered" },
  { id: "guards", label: "Git guards", status: "reused", detail: "Already configured" },
  { id: "coordinator", label: "Coordinator", status: "reused", detail: "Using existing conversation" },
] };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
const existing = { role: "assistant", text: "The existing conversation", at: "2026-09-14T12:00:00Z" };
let chat = [existing];
function mockSetup(initial: Setup = healthy, refusal = false) {
  let setup = initial;
  chat = [existing];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/setup/atlas") return response(setup);
    if (url === "/api/project/setup") {
      if (refusal) return response({ error: "Project repair is denied" }, 403);
      setup = healthy;
      return response(setup);
    }
    if (url.includes("/api/overview")) return response({ projects: [{ name: "atlas", managed: true }], queue: [], wip: { per_project: {}, machine: 0, waiting: [] }, quota: { known: false }, engines: [] });
    if (url.includes("/api/project/atlas")) return response({ name: "atlas", tasks: [], l3: { session_id: "existing" } });
    if (url.includes("/api/chat/atlas")) return response({ history: chat, active: null, busy: false });
    return response({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Project setup", () => {
  it.each([390, 1440])("is revisitable and preserves the conversation/draft at width %s", async (width) => {
    setViewport(width);
    const fetchMock = mockSetup();
    const { user } = renderApp({ route: "/projects/atlas" });
    await screen.findByText("The existing conversation");
    const composer = screen.getByRole("textbox");
    await user.type(composer, "Keep this draft");
    // A healthy project keeps Setup in the project menu, not the header.
    const more = screen.getByRole("button", { name: "More actions" });
    await user.click(more);
    await user.click(await screen.findByRole("menuitem", { name: "Setup: Ready" }));
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
    const panel = screen.getByRole("dialog", { name: "Project setup" });
    expect(within(panel).getByText("Using existing conversation")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(more).toHaveFocus();
    expect(composer).toHaveValue("Keep this draft");
    await user.click(more);
    await user.click(screen.getByRole("menuitem", { name: "Setup: Ready" }));
    // A reply saved while the idle conversation polls slowly appears when setup opens the conversation.
    chat = [...chat, { role: "assistant", text: "The first reply", at: "2026-09-14T12:01:00Z" }];
    await user.click(screen.getByRole("button", { name: "Open conversation" }));
    expect(await screen.findByText("The first reply")).toBeInTheDocument();
    expect(composer).toHaveValue("Keep this draft");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("requires explicit custom-hook integration and submits the observed fingerprint", async () => {
    const fetchMock = mockSetup({ ...healthy, status: "attention", steps: [...healthy.steps.filter((s) => s.id !== "guards"), { id: "guards", label: "Git guards", status: "input_needed", detail: "Custom hooks are active", action: "combine", fingerprint: "owner-v1" }] });
    const { user } = renderApp({ route: "/projects/atlas?setup=1" });
    const panel = await screen.findByRole("dialog", { name: "Project setup" });
    await user.click(await within(panel).findByRole("button", { name: "Review integration" }));
    await user.click(within(panel).getByRole("button", { name: "Keep current setup" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    await user.click(within(panel).getByRole("button", { name: "Review integration" }));
    await user.click(within(panel).getByRole("button", { name: "Use both hook sets" }));
    await waitFor(() => expect(within(panel).getByRole("status")).toHaveTextContent("Ready"));
    // Resolved setup leaves the header quiet.
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
    const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/project/setup");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ project: "atlas", action: "combine", expected: "owner-v1" });
    expect(within(panel).queryByRole("button", { name: "Use both hook sets" })).toBeNull();
  });

  it("shows actual failed and denied states, and discussion opens chat without sending", async () => {
    const fetchMock = mockSetup({ ...healthy, status: "attention", steps: [{ id: "guards", label: "Git guards", status: "failed", detail: "Setup was interrupted", action: "repair" }] }, true);
    const { router, user } = renderApp({ route: "/projects/atlas?tab=work&setup=1" });
    const panel = await screen.findByRole("dialog", { name: "Project setup" });
    await user.click(await within(panel).findByRole("button", { name: "Retry" }));
    await within(panel).findByText("Request refused. No action was accepted.");
    expect(within(panel).getByText("Setup was interrupted")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Discuss with L3" }));
    expect(router.state.location.search).toBe("");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("opens integration for only the selected repository or worktree row", async () => {
    mockSetup({ ...healthy, status: "attention", steps: [
      { id: "guards", label: "Git guards", status: "input_needed", detail: "Repository custom hooks", action: "combine", fingerprint: "repo-owner" },
      { id: "guards:existing-task", label: "Task worktree guards", status: "input_needed", detail: "Worktree custom hooks", action: "combine", fingerprint: "worktree-owner" },
    ] });
    const { user } = renderApp({ route: "/projects/atlas?setup=1" });
    const panel = await screen.findByRole("dialog", { name: "Project setup" });
    const repository = (await within(panel).findByText("Repository custom hooks")).closest("li")!;
    const worktree = within(panel).getByText("Worktree custom hooks").closest("li")!;
    await user.click(within(repository).getByRole("button", { name: "Review integration" }));
    expect(within(repository).getByRole("button", { name: "Use both hook sets" })).toBeInTheDocument();
    expect(within(worktree).queryByRole("button", { name: "Use both hook sets" })).toBeNull();
    await user.click(within(worktree).getByRole("button", { name: "Review integration" }));
    expect(within(worktree).getByRole("button", { name: "Use both hook sets" })).toBeInTheDocument();
    expect(within(repository).queryByRole("button", { name: "Use both hook sets" })).toBeNull();
  });

  it("does not invent completion or offer another repair while accepted work is pending", async () => {
    mockSetup({ ...healthy, status: "checking", operation: { id: "op1", state: "pending", action: "repair" }, steps: [{ id: "guards", label: "Git guards", status: "pending", detail: "Waiting to refresh guards", action: "repair" }] });
    renderApp({ route: "/projects/atlas?setup=1" });
    const panel = await screen.findByRole("dialog", { name: "Project setup" });
    expect(await within(panel).findByText("Pending")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Repair" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: "Check again" })).toBeDisabled();
    expect(within(panel).queryByText("In progress")).toBeNull();
  });

  it("reconciles a lost repair response through observation without repeating the write", async () => {
    const server = mockSetup({ ...healthy, status: "attention", steps: [{ id: "guards", label: "Git guards", status: "failed", detail: "Refresh required", action: "repair" }] });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const result = await server(input, init);
      if (String(input) === "/api/project/setup") throw new TypeError("Connection closed");
      return result;
    }));
    const { user } = renderApp({ route: "/projects/atlas?setup=1" });
    const panel = await screen.findByRole("dialog", { name: "Project setup" });
    await user.click(await within(panel).findByRole("button", { name: "Retry" }));
    await within(panel).findByText("Confirmation was lost. The current setup results are shown below.");
    expect(within(panel).getByRole("status")).toHaveTextContent("Ready");
    expect(within(panel).queryByRole("button", { name: "Retry" })).toBeNull();
    expect(server.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("labels saved observations after a failed read and restores current status on reconnection", async () => {
    const server = mockSetup();
    let offline = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (offline && String(input) === "/api/setup/atlas") throw new TypeError("Offline");
      return server(input, init);
    }));
    const { user } = renderApp({ route: "/projects/atlas" });
    await user.click(await screen.findByRole("button", { name: "More actions" }));
    const item = await screen.findByRole("menuitem", { name: "Setup: Ready" });
    offline = true;
    await user.click(item);
    const panel = screen.getByRole("dialog", { name: "Project setup" });
    await within(panel).findByText("Showing saved results. Current setup could not be checked.");
    expect(within(panel).getByText("Using existing conversation")).toBeInTheDocument();
    // A failed reading cannot claim health, so the header shows it.
    expect(screen.getByRole("button", { name: "Setup: Unavailable" })).toBeInTheDocument();
    offline = false;
    await user.click(within(panel).getByRole("button", { name: "Retry connection" }));
    await waitFor(() => expect(within(panel).queryByRole("alert")).toBeNull());
    expect(within(panel).getByRole("status")).toHaveTextContent("Ready");
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
  });

  it.each([390, 1440])("brings unmet requirements to the header and returns it to quiet after repair at width %s", async (width) => {
    setViewport(width);
    mockSetup({ ...healthy, status: "attention", steps: [{ id: "guards", label: "Git guards", status: "input_needed", detail: "Git guards are not installed.", action: "repair" }] });
    const { user } = renderApp({ route: "/projects/atlas" });
    const trigger = await screen.findByRole("button", { name: "Setup: Needs attention" });
    await user.click(trigger);
    const panel = screen.getByRole("dialog", { name: "Project setup" });
    await user.click(within(panel).getByRole("button", { name: "Repair" }));
    await waitFor(() => expect(within(panel).getByRole("status")).toHaveTextContent("Ready"));
    // Opened from the header, the status stays until the checklist closes and focus returns to it.
    expect(screen.getByRole("button", { name: "Setup: Ready" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Setup: Ready" })).toHaveFocus();
    await user.click(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
  });

  it("returns focus to the header status after a click that does not focus it, then leaves with focus", async () => {
    mockSetup({ ...healthy, status: "attention", steps: [{ id: "guards", label: "Git guards", status: "input_needed", detail: "Git guards are not installed.", action: "repair" }] });
    const { user } = renderApp({ route: "/projects/atlas" });
    // Safari does not focus a clicked button; fireEvent.click leaves focus where it was.
    fireEvent.click(await screen.findByRole("button", { name: "Setup: Needs attention" }));
    const panel = screen.getByRole("dialog", { name: "Project setup" });
    await user.click(within(panel).getByRole("button", { name: "Repair" }));
    await waitFor(() => expect(within(panel).getByRole("status")).toHaveTextContent("Ready"));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Setup: Ready" })).toHaveFocus();
    await user.click(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
  });

  it("stays out of the header while the first reading loads", async () => {
    mockSetup();
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const server = vi.mocked(fetch);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/setup/atlas") await held;
      return server(input, init);
    }));
    const { user } = renderApp({ route: "/projects/atlas" });
    await screen.findByText("The existing conversation");
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menuitem", { name: "Setup: Checking" })).toBeInTheDocument();
    release();
    expect(await screen.findByRole("menuitem", { name: "Setup: Ready" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Setup:/ })).toBeNull();
  });
});
