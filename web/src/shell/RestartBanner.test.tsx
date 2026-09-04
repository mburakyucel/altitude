import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/render";

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function overview(restart: unknown) {
  return {
    projects: [],
    queue: [],
    fyis: [],
    wip: { per_project: {}, machine: 0, waiting: [] },
    quota: { known: false },
    restart,
    now: new Date().toISOString(),
  };
}

function mockFetch(restart: unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/restart")) return jsonResponse({ ok: true, unit: "altitude-restart-1" });
    if (url.includes("/api/overview")) return jsonResponse(overview(restart));
    return jsonResponse({ queue: [], fyis: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const pending = {
  since: "2026-09-02T05:12:48+00:00",
  head: "97e1197",
  files: ["altitude/tasks.py", "bin/alt"],
};

describe("RestartBanner", () => {
  it("stays hidden when no restart is pending", async () => {
    mockFetch(null);
    renderApp({ route: "/projects" });
    await screen.findByText("Add a project");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("names what the restart waits for instead of offering the button", async () => {
    mockFetch({ ...pending, waiting_for: ["altitude/fix-thing", "altitude L3"] });
    renderApp({ route: "/projects" });
    await screen.findByText(/waiting for altitude\/fix-thing, altitude L3/);
    expect(screen.queryByRole("button", { name: "Restart Altitude" })).toBeNull();
  });

  it("truthfully describes web-only activation without claiming backend code is stale", async () => {
    mockFetch({ ...pending, files: ["web/src/routes/Chat.tsx"], waiting_for: [] });
    renderApp({ route: "/projects" });
    await screen.findByText(/Merged web changes are waiting to activate/);
    expect(screen.queryByText(/runs code older than main/)).toBeNull();
  });

  it("truthfully describes combined backend and web activation", async () => {
    mockFetch({ ...pending, files: ["altitude/server.py", "web/src/routes/Chat.tsx"], waiting_for: [] });
    renderApp({ route: "/projects" });
    await screen.findByText(/Merged backend and web changes are waiting to activate/);
  });

  it("offers a failed activation retry only after the system becomes idle", async () => {
    mockFetch({ ...pending, failed: "2026-09-02T05:22:48+00:00", waiting_for: ["altitude/new-work"] });
    renderApp({ route: "/projects" });
    await screen.findByText(/Retry is available once nothing is running; waiting for altitude\/new-work/);
    expect(screen.queryByText(/Altitude activates them automatically/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart Altitude" })).toBeNull();
  });

  it("posts /api/restart when the button is pressed on an idle system", async () => {
    const fetchMock = mockFetch({ ...pending, waiting_for: [] });
    const { user } = renderApp({ route: "/projects" });
    await screen.findByText(/2 files changed since 2026-09-02 05:12Z/);
    await user.click(await screen.findByRole("button", { name: "Restart Altitude" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/restart"))).toBe(true);
    });
    expect(await screen.findByRole("button", { name: "Activating…" })).toBeDisabled();
  });
});
