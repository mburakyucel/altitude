import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const overview = {
  projects: [],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
};

const monitor = {
  quota: { known: true, five_hour: 12, seven_day: 74 },
  codex_quota: { known: true, primary_used: 30, primary_window_minutes: 300, secondary_used: 10, secondary_window_minutes: 10080 },
  sessions: [
    {
      kind: "l2",
      session_id: "abcdef1234567890",
      project: "altitude",
      slug: "fix-timer",
      state: "running",
      context_percent: 44,
      subagent_launches: 2,
      cap: 3,
      edits: 7,
      agent: { status: "active", state: "tool" },
      at: ago(3),
    },
    {
      kind: "l3",
      session_id: "0f0f0f0f0f0f0f0f",
      project: "altitude",
      context_percent: 33,
      rotate_next: true,
      at: ago(80),
    },
  ],
  agents: [
    { id: "aaaaaaaabbbb", name: "worker-one", status: "active", state: "tool", cwd: "/home/b/alt" },
    { id: "deadbeef0000", name: "old-worker", status: "exited", state: "done", cwd: "/home/b/alt" },
  ],
};

function mockFetch(monitorBody: unknown = monitor) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overview);
    if (url.includes("/api/monitor")) return jsonResponse(monitorBody);
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Monitor", () => {
  it("renders the quota, the session cards and the worker list", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("5-hour window 12%")).toBeInTheDocument();
    expect(screen.getByText("7-day 74%")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Claude quota" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Codex quota" })).toBeInTheDocument();
    expect(screen.getByText("5-hour window 30%")).toBeInTheDocument();
    expect(screen.getByText("weekly 10%")).toBeInTheDocument();
    expect(screen.getByText("Sessions (2)")).toBeInTheDocument();
    expect(screen.getByText("altitude / fix-timer")).toBeInTheDocument();
    expect(screen.getByText("l2")).toBeInTheDocument();
    expect(screen.getByText("3m")).toBeInTheDocument();
    expect(screen.getByText(/context 33% · rotating/)).toBeInTheDocument();
    expect(screen.getByText(/aaaaaaaa worker-one active tool/)).toBeInTheDocument();
    expect(screen.queryByText(/old-worker/)).not.toBeInTheDocument();
    expect(screen.getByText(/A done worker only means its process exited/)).toBeInTheDocument();
  });

  it("spells the launch counter 'launches N · cap M', never 'N/M' and never 'agents'", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    await screen.findByText(/task running · context 44% · launches 2 · cap 3 · edits 7 · worker active/);
    // Burak read "0/3" as a plan to launch three agents; both spellings are banned.
    expect(document.body.textContent).not.toMatch(/\d+\/\d+/);
    expect(document.body.textContent).not.toMatch(/agents/i);
  });

  it("renders each fresh quota window independently", async () => {
    mockFetch({ quota: { known: true, five_hour: 63, seven_day: null }, sessions: [], agents: [] });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("5-hour window 63%")).toBeInTheDocument();
    expect(screen.getByText(/7-day unknown — no fresh 7-day reading/)).toBeInTheDocument();
    expect(screen.queryByText(/no fresh statusline or OAuth quota reading is available/)).not.toBeInTheDocument();
  });

  it("explains an unknown quota and an empty session list", async () => {
    mockFetch({ quota: { known: false }, sessions: [], agents: [] });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/no fresh statusline or OAuth quota reading/)).toBeInTheDocument();
    expect(screen.getByText(/unknown or stale — no fresh Codex account reading/)).toBeInTheDocument();
    expect(screen.getByText("no sessions")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("explains L2 closeout and automatic recovery states", async () => {
    mockFetch({
      quota: { known: false },
      sessions: [
        { kind: "l2", project: "altitude", slug: "reported", state: "reported", lifecycle: "awaiting_closeout",
          agent: { status: "exited", state: "done" } },
        { kind: "l2", project: "altitude", slug: "blocked", state: "blocked", lifecycle: "awaiting_l3_recovery",
          agent: { status: "exited", state: "done" } },
        { kind: "l2", project: "altitude", slug: "recovering", state: "running", lifecycle: "recovery_pending" },
        { kind: "l2", project: "altitude", slug: "decision", state: "blocked", lifecycle: "needs_user" },
        { kind: "l2", project: "altitude", slug: "scheduled", state: "blocked", lifecycle: "resume_scheduled" },
        { kind: "l2", project: "altitude", slug: "resuming", state: "blocked", lifecycle: "resume_in_progress" },
      ],
      agents: [],
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/task reported .* awaiting L3 closeout/)).toBeInTheDocument();
    expect(screen.getByText(/task blocked .* awaiting Altitude\/L3 recovery/)).toBeInTheDocument();
    expect(screen.getByText(/task running .* automatic recovery pending/)).toBeInTheDocument();
    expect(screen.getByText(/task blocked .* needs your decision/)).toBeInTheDocument();
    expect(screen.getByText(/task blocked .* automatic resume scheduled/)).toBeInTheDocument();
    expect(screen.getByText(/task blocked .* automatic resume in progress/)).toBeInTheDocument();
  });

  it("renders the tool-shape empty state when history is absent", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("no tool-shape history")).toBeInTheDocument();
  });

  it("renders project tool shapes with formatted counts", async () => {
    mockFetch({
      ...monitor,
      tool_shapes: {
        altitude: [
          { shape: "git commit", turns: 1250, context_tokens: 9_876_543, sessions: 17 },
        ],
      },
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("git commit")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "altitude" })).toBeInTheDocument();
    expect(screen.getByText("1,250")).toBeInTheDocument();
    expect(screen.getByText("9,876,543")).toBeInTheDocument();
  });
});
