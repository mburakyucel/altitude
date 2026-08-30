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
    expect(screen.getByText("Sessions (2)")).toBeInTheDocument();
    expect(screen.getByText("altitude / fix-timer")).toBeInTheDocument();
    expect(screen.getByText("l2")).toBeInTheDocument();
    expect(screen.getByText("3m")).toBeInTheDocument();
    expect(screen.getByText(/context 33% · rotating/)).toBeInTheDocument();
    expect(screen.getByText(/aaaaaaaa worker-one active tool/)).toBeInTheDocument();
  });

  it("spells the launch counter 'launches N · cap M', never 'N/M' and never 'agents'", async () => {
    mockFetch();
    renderApp({ route: "/monitor" });

    await screen.findByText(/launches 2 · cap 3 · edits 7 · active tool/);
    // Burak read "0/3" as a plan to launch three agents; both spellings are banned.
    expect(document.body.textContent).not.toMatch(/\d+\/\d+/);
    expect(document.body.textContent).not.toMatch(/agents/i);
  });

  it("explains an unknown quota and an empty session list", async () => {
    mockFetch({ quota: { known: false }, sessions: [], agents: [] });
    renderApp({ route: "/monitor" });

    expect(await screen.findByText(/needs the statusline wrapper/)).toBeInTheDocument();
    expect(screen.getByText("alt install-statusline")).toBeInTheDocument();
    expect(screen.getByText("no sessions")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
  });
});
