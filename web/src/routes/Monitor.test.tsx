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
const agoEpoch = (minutes: number) => Math.floor(Date.now() / 1000) - minutes * 60;
const inHours = (hours: number) => Math.floor(Date.now() / 1000) + hours * 3600 + 60;

const overview = {
  projects: [],
  queue: [],
  fyis: [],
  wip: { per_project: {}, machine: 0, waiting: [] },
  quota: { known: false },
};

const monitor = {
  quota: {
    known: true,
    five_hour: 12,
    seven_day: 74,
    five_hour_resets: inHours(2),
    seven_day_resets: inHours(30),
    at: agoEpoch(3),
  },
  // The live seat reports one weekly window and no second one: absent, never zero.
  quota_codex: {
    known: true,
    primary_used: 71,
    primary_window_minutes: 10_080,
    primary_resets: new Date(inHours(4) * 1000).toISOString(),
    secondary_used: null,
    secondary_window_minutes: null,
    plan_type: "pro",
    read_at: ago(6),
  },
  routing: [
    { role: "l3", project: "altitude", pin: null, current: "claude", engine: "claude",
      why: "staying on claude: codex has 4.0 points more weekly headroom, under the 15-point switch margin" },
    { role: "l2", project: null, pin: null, current: null, engine: "codex",
      why: "more weekly headroom: claude 7d 74.0% used vs codex 7d 71.0% used" },
  ],
  sessions: [
    {
      kind: "l2",
      session_id: "abcdef1234567890",
      project: "altitude",
      slug: "fix-timer",
      state: "running",
      context_percent: 44,
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
  it("renders both seats, the session cards and the worker list", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("5-hour 12%")).toBeInTheDocument();
    expect(screen.getByText("7-day 74%")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Codex" })).toBeInTheDocument();
    expect(screen.getByText("Pro")).toBeInTheDocument();
    expect(screen.getByText("7-day 71%")).toBeInTheDocument();
    expect(screen.getByText("Sessions (2)")).toBeInTheDocument();
    expect(screen.getByText("altitude / fix-timer")).toBeInTheDocument();
    expect(screen.getByText("l2")).toBeInTheDocument();
    expect(screen.getAllByText("as of 3m ago").length).toBeGreaterThan(0);
    expect(screen.getByText(/context 33% · rotating/)).toBeInTheDocument();
    expect(screen.getByText(/aaaaaaaa worker-one active tool/)).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("shows each window's reset time in human terms, and an absent window as absent", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/resets in 2h/)).toBeInTheDocument();
    expect(screen.getByText(/resets in 1d/)).toBeInTheDocument();
    expect(screen.getByText(/resets in 4h/)).toBeInTheDocument();
    expect(screen.getByText("no second window reported")).toBeInTheDocument();
  });

  it("names the engine each role would get right now, with the router's reason", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("Routing now")).toBeInTheDocument();
    expect(screen.getByText("L3 · altitude (Auto)")).toBeInTheDocument();
    expect(screen.getByText("L2 · new task")).toBeInTheDocument();
    expect(screen.getByText(/staying on claude/)).toBeInTheDocument();
    expect(screen.getByText(/more weekly headroom: claude 7d 74.0% used/)).toBeInTheDocument();
  });

  it("keeps an aged figure visible and labels it stale", async () => {
    mockFetch({
      ...monitor,
      quota: { ...monitor.quota, known: false, stale: true, at: agoEpoch(200) },
      quota_codex: { ...monitor.quota_codex, known: false, stale: true, read_at: ago(200) },
      sessions: [{ ...monitor.sessions[0], at: ago(12) }],
    });
    renderApp({ route: "/monitor" });

    // Still the numbers, not "unknown" — with their age and the label.
    expect(await screen.findByText("5-hour 12%")).toBeInTheDocument();
    expect(screen.getByText("7-day 71%")).toBeInTheDocument();
    expect(screen.getAllByText("stale")).toHaveLength(3);
    expect(screen.getAllByText("as of 3h ago").length).toBeGreaterThan(0);
    expect(screen.getByText("as of 12m ago")).toBeInTheDocument();
  });

  it("marks an idle session by its age alone, never as stale", async () => {
    mockFetch({
      ...monitor,
      sessions: [{ ...monitor.sessions[0], state: "blocked", at: ago(45) }],
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("as of 45m ago")).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("explains an unknown quota, an unavailable route and an empty session list", async () => {
    mockFetch({
      quota: { known: false },
      quota_codex: { known: false, why: "Codex binary not found" },
      routing: [
        { role: "l3", project: "altitude", pin: "codex", current: null, engine: null,
          why: "forced codex is unavailable: weekly window exhausted" },
      ],
      sessions: [],
      agents: [],
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/needs the statusline wrapper/)).toBeInTheDocument();
    expect(screen.getByText("alt install-statusline")).toBeInTheDocument();
    expect(screen.getByText(/Codex binary not found/)).toBeInTheDocument();
    expect(screen.getByText("L3 · altitude (pinned to Codex)")).toBeInTheDocument();
    expect(screen.getByText("no engine")).toBeInTheDocument();
    expect(screen.getByText("no sessions")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("shows edits and agent state without a target or cap", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    await screen.findByText(/edits 7 · active tool/);
    expect(document.body.textContent).not.toMatch(/cap/i);
  });
});
