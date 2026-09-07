import { screen, waitFor, within } from "@testing-library/react";
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

// The engine rows the seam sends: the Monitor takes every display name from here.
const claudeRow = { engine: "claude", label: "Claude", week: 74, known: true, stale: false, at: ago(3) };
const codexRow = { engine: "codex", label: "Codex", week: 71, known: true, stale: false, at: ago(6) };
const engines = [claudeRow, codexRow];

function overviewWith(rows = engines) {
  return {
    projects: [],
    queue: [],
    fyis: [],
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    engines: rows,
  };
}

// The seat rows the seam sends: engine, the seam's label, and that seat's reading whole.
const claudeSeat = {
  engine: "claude",
  label: "Claude",
  quota: {
    known: true,
    five_hour: 12,
    seven_day: 74,
    five_hour_resets: inHours(2),
    seven_day_resets: inHours(30),
    at: agoEpoch(3),
  },
};
// The live seat reports one weekly window and no second one: absent, never zero.
const codexSeat = {
  engine: "codex",
  label: "Codex",
  quota: {
    known: true,
    primary_used: 71,
    primary_window_minutes: 10_080,
    primary_resets: new Date(inHours(4) * 1000).toISOString(),
    secondary_used: null,
    secondary_window_minutes: null,
    plan_type: "pro",
    read_at: ago(6),
  },
};

const monitor = {
  seats: [claudeSeat, codexSeat],
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
      engine: "claude",
      agent: { status: "active", state: "tool" },
      at: ago(3),
    },
    {
      kind: "l3",
      session_id: "0f0f0f0f0f0f0f0f",
      project: "altitude",
      context_percent: 33,
      engine: "codex",
      model: "gpt-5-codex",
      rotate_next: true,
      at: ago(80),
    },
    {
      kind: "statusline",
      session_id: "1111222233334444",
      context_percent: 16,
      cwd: "/home/operator/Projects/altitude",
      model: "Fable 5.1",
      at: agoEpoch(30),
    },
  ],
  agents: [
    { id: "aaaaaaaabbbb", name: "worker-one", status: "active", state: "tool", cwd: "/home/b/alt" },
  ],
};

function mockFetch(monitorBody: unknown = monitor, overviewBody: unknown = overviewWith()) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/overview")) return jsonResponse(overviewBody);
    if (url.includes("/api/monitor")) return jsonResponse(monitorBody);
    return jsonResponse({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const seat = (name: string) => within(screen.getByRole("region", { name }));

describe("Monitor", () => {
  it("renders one seat card per configured engine with both windows, the reserve line, and the reading's age", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    await screen.findByRole("region", { name: "Claude" });
    const claude = seat("Claude");
    expect(claude.getByText("5-hour")).toBeInTheDocument();
    expect(claude.getByText("12%")).toBeInTheDocument();
    expect(claude.getByText("7-day")).toBeInTheDocument();
    expect(claude.getByText("74%")).toBeInTheDocument();
    expect(claude.getByText("reading 3m old")).toBeInTheDocument();
    const codex = seat("Codex");
    expect(codex.getByText("Pro")).toBeInTheDocument();
    expect(codex.getByText("71%")).toBeInTheDocument();
    expect(codex.getByText("reading 6m old")).toBeInTheDocument();
    // SPEC.md §3.14: the 70% reserve line is drawn on every seat window, and only there.
    expect(document.querySelectorAll(".meter-reserve")).toHaveLength(3);
    expect(document.querySelectorAll(".meter-reserve")[0]).toHaveStyle({ left: "70%" });
    expect(screen.getByText(/70% reserve line/)).toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
    // Deletion first: the raw worker list (ids, paths, a provider name spelled in the web) is gone.
    expect(screen.queryByText(/workers/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/aaaaaaaa/)).not.toBeInTheDocument();
  });

  it("shows each window's reset time in human terms, and an absent window as absent", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/resets in 2h/)).toBeInTheDocument();
    expect(screen.getByText(/resets in 1d/)).toBeInTheDocument();
    expect(screen.getByText(/resets in 4h/)).toBeInTheDocument();
    expect(screen.getByText("No second window reported.")).toBeInTheDocument();
  });

  it("names the engine each role would get right now, with the router's reason", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByRole("heading", { name: "Routing now" })).toBeInTheDocument();
    const routing = within(screen.getByRole("region", { name: "Routing now" }));
    expect(routing.getByText("L3 · altitude · Auto")).toBeInTheDocument();
    expect(routing.getByText("L2 · new task")).toBeInTheDocument();
    expect(routing.getByText("Claude")).toBeInTheDocument();
    expect(routing.getByText("Codex")).toBeInTheDocument();
    expect(routing.getByText(/staying on claude/)).toBeInTheDocument();
    expect(routing.getByText(/more weekly headroom: claude 7d 74.0% used/)).toBeInTheDocument();
  });

  it("lists sessions with their tasks, and the engine and model when the API reports them", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    expect(await screen.findByRole("heading", { name: "Sessions (3)" })).toBeInTheDocument();
    expect(screen.getByText("altitude / fix-timer")).toBeInTheDocument();
    expect(screen.getByText("L2")).toBeInTheDocument();
    expect(screen.getByText("Claude · context 44% · edits 7 · active tool")).toBeInTheDocument();
    expect(screen.getByText("3 min ago")).toBeInTheDocument();
    // The model rides next to the engine when reported (a Codex session), and nothing stands in when absent.
    expect(screen.getByText("Codex · gpt-5-codex · context 33% · rotating")).toBeInTheDocument();
    // An interactive session has no task: its folder is the title, and its model is what it reports.
    expect(screen.getByText("Statusline")).toBeInTheDocument();
    expect(screen.getByText("/home/operator/Projects/altitude")).toBeInTheDocument();
    expect(screen.getByText("Fable 5.1 · context 16%")).toBeInTheDocument();
  });

  it("keeps an aged figure visible and labels it stale", async () => {
    mockFetch({
      ...monitor,
      seats: [
        { ...claudeSeat, quota: { ...claudeSeat.quota, known: false, stale: true, at: agoEpoch(200) } },
        { ...codexSeat, quota: { ...codexSeat.quota, known: false, stale: true, read_at: ago(200) } },
      ],
      sessions: [{ ...monitor.sessions[0], at: ago(12) }],
    });
    renderApp({ route: "/monitor" });

    // Still the numbers, not "No reading": with their age and the label.
    expect(await screen.findByText("12%")).toBeInTheDocument();
    expect(screen.getByText("71%")).toBeInTheDocument();
    expect(screen.getAllByText("Stale")).toHaveLength(3);
    expect(screen.getAllByText("reading 3h old")).toHaveLength(2);
    expect(screen.getByText("12 min ago")).toBeInTheDocument();
    expect(document.querySelectorAll(".monitor-meter[data-stale]")).toHaveLength(4);
  });

  it("marks an idle session by its age alone, never as stale", async () => {
    mockFetch({
      ...monitor,
      sessions: [{ ...monitor.sessions[0], state: "blocked", at: ago(45) }],
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText("45 min ago")).toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
  });

  it("explains a seat with no reading, a role with no engine, and an empty session list", async () => {
    mockFetch({
      seats: [
        { ...claudeSeat, quota: { known: false, why: "needs the statusline wrapper (alt install-statusline) and one interactive session" } },
        { ...codexSeat, quota: { known: false, why: "Codex binary not found" } },
      ],
      routing: [
        { role: "l3", project: "altitude", pin: "codex", current: null, engine: null,
          why: "forced codex is unavailable: weekly window exhausted" },
      ],
      sessions: [],
      agents: [],
    });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/No reading\. Needs the statusline wrapper/)).toBeInTheDocument();
    expect(screen.getByText("No reading. Codex binary not found")).toBeInTheDocument();
    expect(screen.getByText("L3 · altitude · pinned to Codex")).toBeInTheDocument();
    expect(screen.getByText("No engine")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sessions (0)" })).toBeInTheDocument();
    expect(screen.getByText("No live sessions.")).toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".monitor-age")).toHaveLength(0);
  });

  it("shows one gauge when one engine is configured, with no empty second column", async () => {
    mockFetch({ ...monitor, seats: [claudeSeat] }, overviewWith([claudeRow]));
    renderApp({ route: "/monitor" });

    expect(await screen.findByRole("region", { name: "Claude" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Codex" })).not.toBeInTheDocument();
    expect(document.querySelectorAll(".monitor-seat")).toHaveLength(1);
  });

  it("takes every engine name from the seam's rows and spells none itself (decision 8)", async () => {
    mockFetch({
      ...monitor,
      seats: [{ ...claudeSeat, label: "Seat one" }, { ...codexSeat, label: "Seat two" }],
    }, overviewWith([
      { ...claudeRow, label: "Seat one" },
      { ...codexRow, label: "Seat two" },
    ]));
    renderApp({ route: "/monitor" });

    expect(await screen.findByRole("region", { name: "Seat one" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Seat two" })).toBeInTheDocument();
    const routing = within(screen.getByRole("region", { name: "Routing now" }));
    expect(routing.getByText("Seat one")).toBeInTheDocument();
    expect(routing.getByText("Seat two")).toBeInTheDocument();
    expect(screen.getByText("Seat one · context 44% · edits 7 · active tool")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Claude" })).not.toBeInTheDocument();
  });

  it("shows a skeleton in the page's shape while loading, then the content", async () => {
    let release: (value: Response) => void = () => {};
    const gate = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overviewWith());
      if (url.includes("/api/monitor")) return gate;
      return jsonResponse({ error: "not found" }, 404);
    }));
    renderApp({ route: "/monitor" });

    expect(await screen.findByLabelText("Loading")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Monitor" })).toBeInTheDocument();
    expect(document.querySelectorAll(".skeleton").length).toBeGreaterThanOrEqual(4);
    release(jsonResponse(monitor));
    expect(await screen.findByRole("heading", { name: "Routing now" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading")).not.toBeInTheDocument();
  });

  it("says one sentence and offers Retry when the read fails, and Retry repeats the read", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/overview")) return jsonResponse(overviewWith());
      if (url.includes("/api/monitor")) {
        calls += 1;
        return calls === 1 ? jsonResponse({ error: "boom" }, 500) : jsonResponse(monitor);
      }
      return jsonResponse({ error: "not found" }, 404);
    }));
    const { user } = renderApp({ route: "/monitor" });

    expect(await screen.findByText("Could not read the monitor.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Routing now" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Could not read the monitor.")).not.toBeInTheDocument());
  });
});
